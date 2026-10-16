"""WP3 — normalize raw feed items to MarketEvent + untrusted-content hardening.

Source allowlist + provenance (prompt-injection-defense §5). ALL free-text fields (headline,
summary, event_type) run through the neutralizer; symbols are validated as tickers (a whole
field must not skip the escaper — wf_005fd06c). NFKC-fold + role/delimiter marker neutralization
is BEST-EFFORT (M3 / SEC-22); the real injection boundary is downstream grounding. `&` is escaped
FIRST so the transform is idempotent and pre-encoded entities can't round-trip back to delimiters.
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
ALLOWED_SOURCES = {"alpaca:benzinga", "finnhub", "calendar", "sec:edgar", "finra"}
MAX_LEN = 4000
MAX_SYMBOLS = 64
_TICKER = re.compile(r"^[A-Z0-9.\-]{1,12}$")
_ROLE_MARKERS = re.compile(
    r"(?i)\b(?:system|assistant|human|developer|user|tool|function)\s*:"
    r"|</?untrusted-content|<\|im_(?:start|end)\|>|\[/?INST\]|###\s*instruction",
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


def python_escape_untrusted(text) -> str:
    if text is None or text == "":
        return ""
    if not isinstance(text, str):
        text = json.dumps(text, default=str)              # coerce dict/list/num safely (no crash)
    t = unicodedata.normalize("NFKC", text)
    t = _ROLE_MARKERS.sub("[role-marker]", t)
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")   # & FIRST -> idempotent
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


def python_content_hash(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def python_to_market_event(*, source, article_id, symbols, headline, summary, ts,
                           event_type="news", raw=None) -> dict:
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"source not allowlisted: {source!r}")   # §5 feed allowlist
    raw = raw if raw is not None else {"headline": headline, "summary": summary}
    # BUG-150: retain a detached source image, separately from bounded/escaped fields.
    # This is storage input only; packet/grounding builders select normalized fields.
    raw_input = json.loads(json.dumps(raw, sort_keys=True, default=str))
    return {
        "source": source,
        "article_id": str(article_id),
        "event_type": python_escape_untrusted(event_type) or "news",
        "symbols": _clean_symbols(symbols),
        "headline": python_escape_untrusted(headline),
        "summary": python_escape_untrusted(summary),
        "ts": _safe_ts(ts),
        "raw_ref": f"{source}:{_safe_ref_component(article_id)}",   # BUG-085: prompt-bound
        "content_hash": python_content_hash(raw_input),
        "raw_input": raw_input,
    }


_NATIVE = None
NORMALIZE_BACKEND = "python"


def _load_native() -> None:
    """Use the Rust normalizer when the extension is installed.

    A missing module keeps the Python path. Subclasses of ``str`` / ``list`` stay
    on the Python path: those objects can change ``strip`` or ``split`` between
    checks, which is the behavior the ticker-boundary tests pin.
    """
    global _NATIVE, NORMALIZE_BACKEND
    try:
        import cockpit_quant as native
    except ImportError:
        return
    if not all(hasattr(native, name) for name in ("escape_untrusted", "content_hash", "market_event")):
        return
    _NATIVE = native
    NORMALIZE_BACKEND = "rust"


def _runtime_unicode(text: str) -> bool:
    """False when ``text`` holds a code point this interpreter has not assigned.

    ``unicode-normalization`` 0.1.24 uses Unicode 16 tables. Python 3.11 and
    3.12 use Unicode 14 and 15. U+1CCD6 is unassigned here, so NFKC leaves it
    in place and the following ``SYSTEM:`` is still a role marker. The newer
    Rust tables fold that scalar to ``A`` and the marker survives. Those
    strings stay on the Python path.
    """
    if text.isascii():
        return True
    return all(unicodedata.category(ch) != "Cn" for ch in text)


def _utf8_text(value) -> bool:
    """True for a real ``str`` PyO3 can take as ``&str``.

    ``json.loads('"\\\\ud800"')`` yields a lone surrogate. That value is a ``str``, but
    encoding it to UTF-8 raises ``UnicodeEncodeError``. The Python normalizer accepts
    it (``json.dumps`` escapes it, and ``_safe_ref_component`` hashes with
    ``surrogatepass``). Sending it to the extension crashes the feed item.
    """
    if type(value) is not str:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _plain_json(value, _seen=None) -> bool:
    if value is None or type(value) in (int, float, bool):
        return True
    if type(value) is str:
        return _utf8_text(value)
    if type(value) not in (list, tuple, dict):
        return False
    # A cycle is not plain JSON. Returning here lets `json.dumps` raise
    # ValueError instead of this walk hitting RecursionError first.
    if _seen is None:
        _seen = set()
    marker = id(value)
    if marker in _seen:
        return False
    _seen.add(marker)
    try:
        if type(value) is dict:
            return all(
                _utf8_text(key) and _plain_json(item, _seen) for key, item in value.items()
            )
        return all(_plain_json(item, _seen) for item in value)
    finally:
        _seen.discard(marker)


def _plain_symbols(symbols) -> bool:
    if type(symbols) is str:
        return _utf8_text(symbols)
    if type(symbols) not in (list, tuple):
        return False
    # Match ``_clean_symbols``: only the first 64 entries are work. A hostile
    # tail must not be scanned or copied before the native call.
    return all(_utf8_text(item) for item in symbols[:MAX_SYMBOLS])


def escape_untrusted(text) -> str:
    native = _NATIVE
    if native is not None and (
        text is None or text == "" or (_utf8_text(text) and _runtime_unicode(text))
    ):
        if text is None or text == "":
            return ""
        return native.escape_untrusted(text)
    return python_escape_untrusted(text)


def content_hash(obj) -> str:
    native = _NATIVE
    if native is not None and _plain_json(obj):
        return native.content_hash(obj)
    return python_content_hash(obj)


def to_market_event(*, source, article_id, symbols, headline, summary, ts,
                    event_type="news", raw=None) -> dict:
    native = _NATIVE
    plain = (
        native is not None
        and _utf8_text(source)
        and source in ALLOWED_SOURCES
        and (type(article_id) is int or _utf8_text(article_id))
        and type(article_id) is not bool
        and _plain_symbols(symbols)
        and _utf8_text(headline)
        and _runtime_unicode(headline)
        and _utf8_text(summary)
        and _runtime_unicode(summary)
        and _utf8_text(event_type)
        and _runtime_unicode(event_type)
        and (ts is None or type(ts) in (int, float) or _utf8_text(ts))
        and type(ts) is not bool
        and (raw is None or _plain_json(raw))
    )
    if not plain:
        return python_to_market_event(
            source=source, article_id=article_id, symbols=symbols, headline=headline,
            summary=summary, ts=ts, event_type=event_type, raw=raw,
        )
    article = article_id if type(article_id) is str else str(article_id)
    event_type_out, symbols_out, headline_out, summary_out, raw_ref, digest, raw_json = native.market_event(
        source, article, symbols, headline, summary, event_type, raw,
    )
    raw_input = {"headline": headline, "summary": summary} if raw is None else json.loads(raw_json)
    return {
        "source": source,
        "article_id": article,
        "event_type": event_type_out,
        "symbols": symbols_out,
        "headline": headline_out,
        "summary": summary_out,
        "ts": _safe_ts(ts),
        "raw_ref": raw_ref,
        "content_hash": digest,
        "raw_input": raw_input,
    }


_load_native()
