"""WP4 ``parse/`` — the deterministic, LLM-free filing parse layer.

Segments raw SEC filings into byte-anchored units (10-K/10-Q sections, 8-K items) and
normalizes XBRL facts, recording ``raw_ref`` + byte offsets back into the raw blob for every
parsed unit (the citation chain's anchor, Invariant 1). No module here imports an LLM or
agent-orchestration module, and none spawns a child process (Invariant 2). A malformed filing raises
:class:`ParseError` (fail-closed, Invariant 12).
"""
from __future__ import annotations

from filings.parse.items_8k import parse_8k
from filings.parse.models import (
    InlineFact,
    ParsedFact,
    ParsedFiling,
    ParsedUnit,
    ParseError,
    TextRun,
)
from filings.parse.sections_10kq import parse_10kq
from filings.parse.xbrl_facts import (
    facts_for_accession,
    find_inline_anchor,
    iter_inline_facts,
    normalize_companyfacts,
    parse_contexts,
    select_primary,
)

__all__ = [
    "parse_10kq",
    "parse_8k",
    "normalize_companyfacts",
    "facts_for_accession",
    "select_primary",
    "iter_inline_facts",
    "find_inline_anchor",
    "parse_contexts",
    "ParsedFiling",
    "ParsedUnit",
    "ParsedFact",
    "InlineFact",
    "TextRun",
    "ParseError",
]
