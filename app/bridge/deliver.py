"""RW-01 — the ``deliver(channel, payload) -> bool`` seam, and its Discord REST adapter.

``workflows/`` posts to LOGICAL channels — ``#brief`` (``morning_brew.py``), ``#alerts`` /
``#proposals`` (``triage_loop.py``), ``#system`` (breakers/killswitch) — and knows nothing about
Discord. Until RW-01, ``main.compose`` never passed a ``deliver`` at all, so the morning brief
was computed, stored, audited... and then went nowhere. This module is that missing last hop.

SCOPE — THIS IS NOT THE SURFACE ABSTRACTION (that is RW-02)
------------------------------------------------------------
Deliberately kept to the shape that already exists: a plain callable, stdlib ``urllib`` only,
no ``discord.py`` import, no client object, no new coupling for ``workflows/`` to grow into. It
mirrors ``bridge/witness.py:discord_rest_witness`` (the established "post to a channel with the
keyring bot token" pattern) rather than inventing a second one. When RW-02 introduces
``OperatorSurface``, THIS is the file that becomes ``surfaces/discord/`` — the pipeline above it
does not change, because the pipeline only ever saw ``(channel, payload) -> bool``.

CHANNEL NAMES ARE LOGICAL, NOT DISCORD NAMES. The operator's Discord channel may be called
``#morning-brew``; the pipeline's channel is ``#brief``. The mapping lives in
``channel_ids`` — one place, operator-configured — so renaming a Discord channel never reaches
the pipeline, and an UNMAPPED logical channel fails closed (returns False) instead of guessing.

DELIVERY NEVER RAISES AND NEVER LIES
-------------------------------------
``morning_brew`` treats a raising ``deliver`` as undelivered and continues; ``triage_loop``
wraps it in ``_safe_deliver``. Both are correct, and both mean the return value is the ONLY
signal the operator gets. So: a real HTTP failure returns ``False`` (recorded as
``delivered: false`` in the brew result and visible in the runbook's check), and nothing here
converts a failure into a silent success. Undelivered is a fact to surface, never to smooth
over — a brief nobody received is exactly the case where "it ran fine" must not be printed.

THE BOT TOKEN LIVES ONLY IN THE CLOSURE (Inv 4). It is read from keyring by the caller, passed
here, captured in the ``Authorization`` header inside ``post_fn``, and never stored on an
object, logged, echoed into an exception message, or returned.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

log = logging.getLogger("cockpit.deliver")

# The logical channels the pipeline posts to. Anything outside this set is a wiring bug and is
# refused rather than silently dropped into whatever channel happens to be first.
CHANNEL_BRIEF = "#brief"
CHANNEL_ALERTS = "#alerts"
CHANNEL_PROPOSALS = "#proposals"
CHANNEL_SYSTEM = "#system"
LOGICAL_CHANNELS = (CHANNEL_BRIEF, CHANNEL_ALERTS, CHANNEL_PROPOSALS, CHANNEL_SYSTEM)

# Discord's message-content ceiling is 2000 characters. Render below it and say so when the
# body was cut, so a truncated brief is never mistaken for a short one.
MAX_CONTENT = 1900
_TRUNCATED = "\n… (truncated — full brief via GET /brief/today)"

# MorningBrief prose fields, in the order an operator wants to read them at 08:30. Kept as a
# literal (not derived from the payload's key order) so the message shape is deterministic and
# a model-authored key ordering cannot reorder what the operator sees.
_BRIEF_FIELDS = (
    ("market_regime", "Regime"),
    ("risk_flags", "Risk flags"),
    ("portfolio_changes", "Portfolio"),
    ("watch_items", "Watch"),
    ("thesis_updates", "Thesis"),
    ("proposed_actions", "Proposed"),
)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(f"• {_as_text(v)}" for v in value if _as_text(v))
    if isinstance(value, dict):
        return "\n".join(f"• {k}: {_as_text(v)}" for k, v in value.items())
    return str(value)


def render_brief(payload: dict) -> str:
    """Render a ``#brief`` payload as Discord message text.

    Renders ONLY the stored MorningBrief fields. It performs no scrubbing of its own and must
    not: ``morning_brew`` already ran ``scrub_output_fields`` over every prose field BEFORE the
    record was stored and this payload was built (SEC-09), and a blocked scrub quarantines the
    brief so nothing is delivered at all. A second, weaker redaction here would create a place
    for the two to disagree about what "redacted" means.
    """
    brief = payload.get("brief") or {}
    head = f"**Morning brief — {payload.get('date', '?')}**"
    if payload.get("degraded"):
        head += "  ⚠️ _degraded: some cited refs were dropped_"
    lines = [head]
    for key, label in _BRIEF_FIELDS:
        body = _as_text(brief.get(key))
        if body:
            lines.append(f"__{label}__\n{body}")
    refs = brief.get("source_refs") or []
    lines.append(f"_{len(refs)} source ref(s)_")
    return "\n\n".join(lines)


def render_payload(channel: str, payload) -> str:
    """Render any logical channel's payload as message text. ``#brief`` gets the readable
    layout; everything else falls back to compact JSON — honest and lossless, and RW-03 is
    where per-channel notification formatting actually belongs."""
    if channel == CHANNEL_BRIEF and isinstance(payload, dict):
        return render_brief(payload)
    if isinstance(payload, str):
        return payload
    return f"**{channel}**\n```json\n{json.dumps(payload, default=str, indent=2)}\n```"


def clamp(text: str, limit: int = MAX_CONTENT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATED)] + _TRUNCATED


def build_deliver(channel_ids: dict, post_fn: Callable[[int, str], None]) -> Callable:
    """The transport-agnostic core: map a logical channel to an id, render, post, report.

    ``post_fn(channel_id, content) -> None`` is the only I/O, so this whole function is unit
    testable with no network. ``discord_rest_deliver`` supplies the production ``post_fn``.
    """

    def deliver(channel: str, payload) -> bool:
        channel_id = (channel_ids or {}).get(channel)
        if channel_id is None:
            # FAIL CLOSED, LOUDLY. An unmapped channel means the operator has not configured
            # where this class of message goes; posting it somewhere else would be worse than
            # not posting it, and returning True would claim a delivery that did not happen.
            log.warning("no channel id configured for %s — NOT delivered (set the "
                        "COCKPIT_*_CHANNEL_ID for it)", channel)
            return False
        try:
            post_fn(channel_id, clamp(render_payload(channel, payload)))
        except Exception as exc:    # noqa: BLE001 - delivery failure is REPORTED, never raised
            # Log the exception TYPE and channel, not the exception body: a urllib HTTPError
            # repr can carry the request URL, and the URL is where a token would show up if a
            # future caller ever put one there. The status is what the operator needs anyway.
            log.warning("delivery to %s failed: %s", channel, type(exc).__name__)
            return False
        return True

    return deliver


def discord_rest_deliver(channel_ids: dict, bot_token: str, *, timeout: float = 10.0):
    """Production ``deliver`` bound to Discord's REST API with a keyring-held bot token.

    Stdlib ``urllib`` only — no async client, no ``discord.py`` — matching
    ``bridge/witness.py:discord_rest_witness``. The token is captured in the header dict inside
    this closure and never escapes it (Inv 4).
    """
    import urllib.request

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
        "User-Agent": "cockpit-bridge (https://localhost, 1.0)",
    }

    def post_fn(channel_id, content: str) -> None:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        body = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=timeout).read()

    return build_deliver(channel_ids, post_fn)


def channel_ids_from_settings(settings) -> dict:
    """The logical-channel -> Discord-channel-id map, from non-secret settings.

    Every entry is optional and independently configured: an operator running Phase-0.5 sets
    only ``brief_channel_id`` (their ``#morning-brew``), and the other channels then fail closed
    with a warning rather than dumping alerts into the brief channel. Unset ids are OMITTED, not
    stored as None, so ``build_deliver``'s "is it mapped?" test stays a simple membership check.
    """
    mapping = {
        CHANNEL_BRIEF: getattr(settings, "brief_channel_id", None),
        CHANNEL_ALERTS: getattr(settings, "alerts_channel_id", None),
        CHANNEL_PROPOSALS: getattr(settings, "proposals_channel_id", None),
        CHANNEL_SYSTEM: getattr(settings, "system_channel_id", None),
    }
    return {channel: cid for channel, cid in mapping.items() if cid is not None}
