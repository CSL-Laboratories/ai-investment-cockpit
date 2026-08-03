"""WP4 ``delta/`` — the deterministic, LLM-free delta engine.

Computes section-aware text deltas (``text_diff``) and period-matched numeric XBRL deltas
(``xbrl_delta``) between a filing and its prior same-form filing, then mints ranked schema
``Delta`` rows (``materiality``) each born with a resolvable ``raw_ref`` + byte offsets +
verbatim snippet (Invariant 1). No module here imports an LLM or agent-orchestration module, and
none spawns a child process (Invariant 2); identical inputs produce byte-identical ``Delta`` sets.
"""
from __future__ import annotations

from filings.delta.engine import UnpairedPeriodicFiling, compute_deltas
from filings.delta.materiality import (
    build_deltas,
    compute_filing_deltas,
    score_numeric,
    score_text,
)
from filings.delta.models import NumericDelta, TextDelta
from filings.delta.text_diff import diff_filings, diff_filings_coalesced, extract_filing
from filings.delta.xbrl_delta import diff_companyfacts, diff_facts

__all__ = [
    "compute_deltas",
    "UnpairedPeriodicFiling",
    "compute_filing_deltas",
    "build_deltas",
    "diff_filings",
    "diff_filings_coalesced",
    "extract_filing",
    "diff_companyfacts",
    "diff_facts",
    "score_text",
    "score_numeric",
    "TextDelta",
    "NumericDelta",
]
