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


def escape_untrusted(text) -> str:
    if text is None or text == "":
        return ""
    if not isinstance(text, str):
        text = json.dumps(text, default=str)              # coerce dict/list/num safely (no crash)
    t = unicodedata.normalize("NFKC", text)
    t = _ROLE_MARKERS.sub("[role-marker]", t)
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")   # & FIRST -> idempotent
    return t[:MAX_LEN]


def _clean_symbols(symbols) -> list[str]:
    if isinstance(symbols, str):
        symbols = symbols.split(",")
    if not isinstance(symbols, (list, tuple)):
        return []
    out = []
    for s in symbols[:MAX_SYMBOLS]:
        u = str(s).strip().upper()
        if _TICKER.match(u):
            out.append(u)
    return out


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
        "raw_ref": f"{source}:{article_id}",
        "content_hash": content_hash(raw),
    }
