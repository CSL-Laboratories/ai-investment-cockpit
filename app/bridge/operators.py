"""WP6 — the single-operator allowlist (Inv 1; SEC — approval-actor authentication).

The ONE gate that decides whether a Discord user id may adjudicate a proposal. The single
authorized numeric Discord id is OPERATIONAL config, never an LLM-reachable path: it is read from
``Settings`` (env ``COCKPIT_OPERATOR_IDS`` or an injected config seam), so no model output can add
itself to the allowlist. A foreign/unknown Discord user id
is NOT authorized; the approval gateway (``bridge/approval.py``) and the auto-veto path
(``bridge/veto.py``) both refuse and audit any decision from a non-allowlisted actor.

Production ``Settings`` loads ``COCKPIT_OPERATOR_IDS`` once from environment/``.env`` and exposes
only an immutable startup snapshot through its ``operator_ids`` property. The direct environment
read remains the fallback for injected/legacy settings without that snapshot. No normal
config-write path can replace the frozen authorization set while the process is running.
"""
from __future__ import annotations

import os

_ENV_KEY = "COCKPIT_OPERATOR_IDS"
_MISSING = object()


def _parse(raw) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if isinstance(raw, (set, frozenset, list, tuple)):
        items = [str(x) for x in raw]
    else:
        # comma- or whitespace-separated string
        items = [tok for tok in str(raw).replace(",", " ").split()]
    return frozenset(s.strip() for s in items if s and s.strip())


def _validated(raw) -> frozenset[str]:
    """Apply the Settings grammar/bounds to every source; malformed input denies everyone."""
    from config.settings import normalize_operator_ids

    if isinstance(raw, (set, frozenset, list, tuple)):
        raw = ",".join(str(value) for value in raw)
    try:
        normalized = normalize_operator_ids(raw)
    except (TypeError, ValueError):
        return frozenset()
    return _parse(normalized)


def authorized_operator_ids(settings=None) -> frozenset[str]:
    """The allowlisted Discord operator ids. Precedence: an immutable/injected
    ``operator_ids`` seam on settings -> direct ``COCKPIT_OPERATOR_IDS`` environment fallback.
    NEVER an LLM-produced field."""
    if settings is not None:
        seam = getattr(settings, "operator_ids", _MISSING)
        if seam is not _MISSING:
            # Presence is authoritative even when empty: a startup deny-all snapshot must never
            # become permissive because somebody mutates os.environ later in the process.
            return _validated(seam)
    return _validated(os.environ.get(_ENV_KEY))


def is_authorized(user_id, settings=None) -> bool:
    """True iff ``user_id`` is on the operator allowlist. A ``None``/empty id, or any id not in
    the configured set (a foreign Discord user), is NOT authorized (fail closed)."""
    if user_id is None:
        return False
    uid = str(user_id).strip()
    if not uid:
        return False
    return uid in authorized_operator_ids(settings)
