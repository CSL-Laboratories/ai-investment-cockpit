"""Inverse Cramer — deterministic detection of a Jim Cramer stock call.

The SIDE that ultimately drives a trade must be decided by this deterministic, regex-only
parser — NEVER by an LLM's reading of the (untrusted, feed-sourced) article text. This
mirrors the project's existing posture that a model's self-labeled classification is never
trusted alone for a safety/decision-critical value (SEC-21's "the headline's
self-description never counts").

Fail-closed on ambiguity: a headline/summary tagged with more than one symbol, or carrying
both buy- and sell-side language (or neither), is treated as ordinary news — never guessed.
Runs at ingest time, before ``persist_events`` binds ``event_type`` into the signed
EVENT_INGEST_BATCH chain (SEC-11 domain-row binding), so ``cramer_call:buy``/``cramer_call:
sell`` is trustworthy exactly like ``halt``/``filing``.
"""
from __future__ import annotations

import re

_CRAMER = re.compile(r"(?i)\bcramer\b")
_BUY = re.compile(r"(?i)\b(?:buy|buys|buying|bullish on|likes|recommends buying|upgrades)\b")
_SELL = re.compile(r"(?i)\b(?:sell|sells|selling|bearish on|dumps|recommends selling|downgrades)\b")


def detect_cramer_call(headline: str, summary: str | None, symbols) -> tuple[str, str] | None:
    """Returns ``(symbol, side)`` with ``side`` in ``{"buy", "sell"}`` ONLY when confident:
    a "Cramer" mention, exactly one distinct tagged symbol, and an UNAMBIGUOUS buy XOR sell
    keyword. Anything else returns ``None`` (fail closed, never guessed)."""
    text = f"{headline or ''} {summary or ''}"
    if not _CRAMER.search(text):
        return None
    syms = sorted({str(s).strip().upper() for s in (symbols or []) if str(s).strip()})
    if len(syms) != 1:
        return None   # ambiguous multi-ticker (or tickerless) article -> skip, never guess
    buy_hit = bool(_BUY.search(text))
    sell_hit = bool(_SELL.search(text))
    if buy_hit == sell_hit:   # both or neither -> ambiguous
        return None
    return (syms[0], "buy" if buy_hit else "sell")


def inverse_side(side: str) -> str:
    """The Inverse Cramer action: do the opposite of what Cramer said."""
    return "sell" if side == "buy" else "buy"
