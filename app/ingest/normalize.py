"""WP3 — normalize raw feed items to MarketEvent + untrusted-content hardening.

Source allowlist + provenance (prompt-injection-defense §5). ALL free-text fields (headline,
summary, event_type) run through the neutralizer; symbols are validated as tickers (a whole
field must not skip the escaper — wf_005fd06c). NFKC-fold + role/delimiter marker neutralization
is BEST-EFFORT (M3 / SEC-22); the real injection boundary is downstream grounding.
``escape_untrusted`` is IDEMPOTENT — ``f(f(x)) == f(x)`` — which is what makes "escape at every
boundary" (the pattern every feed, packets.py and mcp_untrusted.py rely on) safe rather than
corrupting. See that function for why ``&`` is deliberately NOT entity-encoded.
content_hash binds the raw item into the audit chain (SEC-13 / Invariant 8).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata

# The §5 feed allowlist. This literal is the ONE reviewable line that authorizes a feed, so it is
# widened HERE and never by a module registering itself at import (that would make the allowlist's
# contents import-order-dependent and un-greppable). "sec:edgar" = the edgar-morning-brew Delta
# feed (ingest/edgar_brew.py); "finra" = short-interest/Reg SHO enrichment (W20,
# ingest/finra_short_interest.py).
ALLOWED_SOURCES = {"alpaca:benzinga", "finnhub", "calendar", "sec:edgar", "finra", "congress"}
MAX_LEN = 4000
MAX_SYMBOLS = 64
_TICKER = re.compile(r"^[A-Z0-9.\-]{1,12}$")
# The delimiter spellings are matched in BOTH their literal (``<``) and entity (``&lt;``) forms.
# The entity alternatives are not decoration: because this transform is a fixed point (see
# ``escape_untrusted``), an ALREADY-escaped delimiter arrives here spelled ``&lt;/untrusted-content``
# on a re-escape pass, and a filer/headline can type that spelling directly. Matching only the
# literal form would let the entity spelling reach a prompt as a recognisable closing delimiter.
_ROLE_MARKERS = re.compile(
    r"(?i)\b(?:system|assistant|human|developer|user|tool|function)\s*:"
    r"|(?:<|&lt;)/?untrusted-content|(?:<|&lt;)\|im_(?:start|end)\|(?:>|&gt;)"
    r"|\[/?INST\]|###\s*instruction",
)


def _canonical_ticker(value) -> str | None:
    """Return one canonical ASCII ticker, or ``None`` for an invalid source value.

    The raw ASCII/no-whitespace check deliberately precedes ``upper()``. Python case conversion
    and whitespace cleanup are not validation boundaries: the long-s spelling ``\u017fpy`` or a
    padded spelling can alias ``SPY``. Accepting either would let an untrusted wire value
    impersonate a different ticker and consume its dedup key.
    """
    # This is a raw-wire boundary, not a convenience normalizer. Reject a raw spelling that
    # would change under ``strip``; Unicode or ASCII/control whitespace would otherwise vanish
    # and impersonate a real ticker. Rejecting subclasses also prevents a stateful injected seam
    # from returning one value to this check and a different value to ``to_market_event`` later.
    if type(value) is not str or not value.isascii() or value != value.strip():
        return None
    ticker = value.upper()
    return ticker if _TICKER.match(ticker) else None


def escape_untrusted(text) -> str:
    """Neutralize one untrusted free-text field (M3): NFKC-fold, blunt role/delimiter markers,
    entity-encode ``<``/``>``, cap the length.

    WHY ``&`` IS NOT ENTITY-ENCODED — this is a deliberate security decision, not an omission.
    -----------------------------------------------------------------------------------------
    This function is applied at EVERY boundary a value crosses, on purpose: the parse boundary
    (``ingest/congress/ptr_parse.py``), the packet builder (``workflows/packets.py``), the MCP
    hardener (``brokers/mcp_untrusted.py``), the copy-order thesis (``workflows/copytrade_orders``).
    That pattern is only safe if the transform is a FIXED POINT, and it only stays honest if
    escaping twice is indistinguishable from escaping once.

    The previous implementation also mapped ``&`` -> ``&amp;`` "first, so the transform is
    idempotent". It is not, and it cannot be: the claim confuses ORDER-safety within one pass
    with idempotence across passes. ``escape_untrusted("AT&T")`` -> ``AT&amp;T`` ->
    ``AT&amp;amp;T`` -> ..., so a filing for AT&T or Procter & Gamble reached the operator
    visibly corrupted, one ``amp;`` per boundary crossed.

    The two properties are mathematically incompatible over an entity alphabet: if ``&lt;`` is in
    the image of ``f`` (it is — any legitimate ``<`` produces it) then idempotence FORCES
    ``f("&lt;") == "&lt;"``, i.e. a pre-encoded entity must survive untouched. So "each pass adds
    an encode layer, so a pre-encoded entity can never decode back to a delimiter" had to go.
    Nothing in this repo HTML-unescapes an escaped field (``html.unescape`` appears only in
    ``filings/`` over raw SEC HTML, a different input path), so that layering bought no live
    defense; what it cost was legible operator text. The residual — an attacker typing ``&lt;``
    directly — is closed better at the DETECTION end: ``_ROLE_MARKERS`` matches the entity
    spelling of the delimiters too, so ``&lt;/untrusted-content&gt;`` is neutralized just like
    ``</untrusted-content>``.

    The M3 requirement in docs/prompt-injection-defense.md §4 is "entity-encode ``<``/``>``,
    neutralize role markers" — exactly what remains. ``&`` is not a delimiter in any consumer:
    element text in the ``<untrusted-content>`` prompt block (a string, never XML-parsed) and
    operator surfaces (Discord markdown). ATTRIBUTE context is a different escaper with a
    different job — ``workflows.packets._attr_escape``, which does neutralize ``&`` and ``"``.
    """
    if text is None or text == "":
        return ""
    if not isinstance(text, str):
        text = json.dumps(text, default=str)              # coerce dict/list/num safely (no crash)
    t = unicodedata.normalize("NFKC", text)
    t = _ROLE_MARKERS.sub("[role-marker]", t)
    # Fixed point: the output holds no ``<``/``>`` and no marker match, NFKC is stable over it,
    # and re-truncating a truncated string is a no-op — so a second pass changes nothing.
    t = t.replace("<", "&lt;").replace(">", "&gt;")
    return t[:MAX_LEN]


def _clean_symbols(symbols) -> list[str]:
    # The container itself is an untrusted boundary too: a string/list subclass can fabricate
    # primitive-looking members from an overridden ``split``/iteration method after the raw
    # ticker gate has rejected its original value.
    if type(symbols) is str:
        symbols = symbols.split(",")
    if type(symbols) not in (list, tuple):
        return []
    out = []
    for s in symbols[:MAX_SYMBOLS]:
        ticker = _canonical_ticker(s)
        if ticker is not None:
            out.append(ticker)
    return out


_REF_SAFE = re.compile(r"[^A-Za-z0-9._:\-]")


def _safe_ref_component(value) -> str:
    """BUG-085: ``raw_ref`` is interpolated into a prompt attribute downstream
    (``workflows/grounding.extract_slice``) — the reason the edgar poller gates accession
    ids with its ASCII ``_ACCESSION`` regex before they reach the same field. The other
    feeds passed raw wire ids straight through. Strip anything outside a conservative
    charset; when anything WAS stripped, bind a short content hash so a hostile or
    malformed id stays unique without its bytes ever reaching a prompt."""
    s = str(value)
    cleaned = _REF_SAFE.sub("", s)[:128]
    if cleaned == s:
        return cleaned
    digest = hashlib.sha256(s.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    return f"{cleaned}~{digest}"


def _safe_ts(ts):
    if ts is None:
        return None
    if isinstance(ts, float) and not math.isfinite(ts):
        return None
    return ts


def content_hash(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def to_market_event(*, source, article_id, symbols, headline, summary, ts,
                    event_type="news", raw=None) -> dict:
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"source not allowlisted: {source!r}")   # §5 feed allowlist
    raw = raw if raw is not None else {"headline": headline, "summary": summary}
    return {
        "source": source,
        "article_id": str(article_id),
        "event_type": escape_untrusted(event_type) or "news",
        "symbols": _clean_symbols(symbols),
        "headline": escape_untrusted(headline),
        "summary": escape_untrusted(summary),
        "ts": _safe_ts(ts),
        "raw_ref": f"{source}:{_safe_ref_component(article_id)}",   # BUG-085: prompt-bound
        "content_hash": content_hash(raw),
    }
