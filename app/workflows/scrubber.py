"""WP8 — SEC-09 deterministic outbound scrubber (contract Owner A 'scrubber.py').

Implements docs/security-hardening-backlog.md SEC-09: a hijacked model can stuff holdings,
balances, or account numbers into any schema-valid free-text field (TradeProposal.thesis,
GroundingCheck.notes, MorningBrief prose) and Inv 3 will pass it — so a DEFAULT-ON,
deterministic (stdlib ``re`` only, no LLM) scrubber runs on EVERY model free-text field
before it is stored, audited, or posted. Redaction classes: account numbers (incl. masked
forms) -> ``[acct]`` (BLOCKING), dollar amounts -> ``[amount]``, share counts -> ``[qty]``,
cost-basis phrases -> ``[basis]``, and ticker+size tuples for any NON-subject symbol ->
``[position]`` (BLOCKING — a position leak for a symbol the output is not even about).
``blocked=True`` means the caller MUST quarantine (never post). Hard length cap at
``max_len``. Hits are audited as ``EVENT_SCRUB`` with the hit KINDS only — never the
matched text (the audit chain must not become the leak).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Sequence

from workflows.constants import EVENT_SCRUB

# kinds whose presence forces quarantine (SEC-09 "reject/quarantine on hit")
_BLOCKING_KINDS = frozenset({"account_number", "foreign_ticker_size"})

_ACCOUNT = re.compile(r"\d{8,}|\*{2,}\s?\d{2,6}")
_TICKER_SIZE = re.compile(
    r"\b([A-Z]{2,5})\b\s*:?\s*\d[\d,]*(?:\.\d+)?\s*(?:%|percent\b|shares\b|sh\b)"
)
# reverse-order phrasing (NUMBER -> TICKER -> UNIT), e.g. "500 TSLA shares" — same
# foreign-position leak, so it must classify foreign_ticker_size (BLOCKING) too.
_TICKER_SIZE_REV = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s+([A-Z]{2,5})\b\s*(?:%|percent\b|shares\b|sh\b)"
)
_COST_BASIS = re.compile(r"(?i)\bcost[\s\-]+basis\b[^\n.;]{0,40}")
_DOLLAR = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:[kKmMbB]\b)?|(?i:\bUSD)\s?\d[\d,]*(?:\.\d+)?")
_SHARES = re.compile(r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(?:shares|sh)\b")


@dataclass(frozen=True)
class ScrubHit:
    kind: str      # account_number | dollar_amount | share_count | cost_basis | foreign_ticker_size
    excerpt: str   # short matched excerpt (kept local; NEVER audited/posted)


@dataclass(frozen=True)
class ScrubResult:
    clean: str
    hits: tuple[ScrubHit, ...]
    blocked: bool


def _sub(pattern: re.Pattern, kind: str, replacement: str, text: str,
         hits: list[ScrubHit]) -> str:
    def repl(m: re.Match) -> str:
        hits.append(ScrubHit(kind=kind, excerpt=m.group(0)[:40]))
        return replacement
    return pattern.sub(repl, text)


def scrub_text(text: str, *, subject_symbols: frozenset[str] = frozenset(),
               max_len: int = 4000) -> ScrubResult:
    """Deterministic redaction of one free-text field (default-ON, SEC-09)."""
    hits: list[ScrubHit] = []
    t = text if isinstance(text, str) else str(text)

    # 1) account numbers (8+ digit runs, masked ****1234 forms) — BLOCKING
    t = _sub(_ACCOUNT, "account_number", "[acct]", t, hits)

    # 2) ticker+size tuples for any symbol NOT in subject_symbols — BLOCKING.
    #    Subject-symbol sizes fall through to the share-count/amount rules below.
    def _pos_repl(m: re.Match) -> str:
        if m.group(1) in subject_symbols:
            return m.group(0)
        hits.append(ScrubHit(kind="foreign_ticker_size", excerpt=m.group(0)[:40]))
        return "[position]"
    t = _TICKER_SIZE.sub(_pos_repl, t)
    t = _TICKER_SIZE_REV.sub(_pos_repl, t)

    # 3) cost-basis phrases (before dollar amounts so the whole phrase collapses)
    t = _sub(_COST_BASIS, "cost_basis", "[basis]", t, hits)

    # 4) dollar amounts ($1,234.56 / USD 500)
    t = _sub(_DOLLAR, "dollar_amount", "[amount]", t, hits)

    # 5) share counts (123 shares / 123 sh)
    t = _sub(_SHARES, "share_count", "[qty]", t, hits)

    t = t[:max_len]   # hard truncate (SEC-09 length cap)
    blocked = any(h.kind in _BLOCKING_KINDS for h in hits)
    return ScrubResult(clean=t, hits=tuple(hits), blocked=blocked)


def _scrub_value(value, subject_symbols: frozenset[str], hits: list[ScrubHit]):
    """Recursively scrub strings inside str/list/dict values (MorningBrief prose arrays
    are untyped — elements may be strings or nested objects)."""
    if isinstance(value, str):
        r = scrub_text(value, subject_symbols=subject_symbols)
        hits.extend(r.hits)
        return r.clean
    if isinstance(value, list):
        return [_scrub_value(v, subject_symbols, hits) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v, subject_symbols, hits) for k, v in value.items()}
    return value


def scrub_output_fields(state, obj: dict, fields: Sequence[str], *,
                        subject_symbols: frozenset[str], target: str) -> tuple[dict, bool]:
    """Scrub the named free-text fields of a model output dict (place-copy), audit
    ``EVENT_SCRUB`` when anything hit, and return ``(scrubbed_obj, blocked)``.
    ``blocked=True`` => the caller must quarantine — never post."""
    out = copy.deepcopy(obj)
    hits: list[ScrubHit] = []
    for field in fields:
        if field in out:
            out[field] = _scrub_value(out[field], subject_symbols, hits)
    blocked = any(h.kind in _BLOCKING_KINDS for h in hits)
    if hits:
        state.audit.append(
            EVENT_SCRUB, actor="scrubber",
            payload={"target": target, "hits": [h.kind for h in hits], "blocked": blocked},
        )
    return out, blocked
