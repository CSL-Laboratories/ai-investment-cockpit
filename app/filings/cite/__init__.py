"""WP5 — ``cite/``: the deterministic citation-integrity core (Invariant 1 + 12).

The whole product lives in the chain this package makes executable and re-verifiable:

    claim (Delta) -> Citation -> raw filing bytes at accession + byte offset

* :func:`build_citation` / :func:`build_citations` mint exactly one ``Citation`` per
  ``Delta`` (1:1, deterministic ids, fail-closed on a missing filing date).
* :func:`resolve_citation` / :func:`resolve_all` re-read the raw store and reproduce each
  snippet byte-for-byte, or fail with a distinct reason.
* :func:`evaluate` (the claim gate) makes "zero uncited factual claims" a hard,
  all-or-nothing, fail-closed check over a candidate brief.
* :func:`render_digest` renders the first personally-useful cited artifact — or a
  clearly-marked UNPUBLISHABLE report when the gate blocks the brief.

No LLM, no subprocess, no network anywhere in this package (deterministic like the WP4
core). This module re-exports the public surface; it defines no new contracts.
"""
from __future__ import annotations

from filings.cite.builder import (
    CitationBuildError,
    build_citation,
    build_citations,
)
from filings.cite.claim_gate import GateResult, evaluate
from filings.cite.digest import (
    DigestResult,
    edgar_url_matches_accession,
    render_digest,
)
from filings.cite.resolver import ResolveResult, resolve_all, resolve_citation

__all__ = [
    "CitationBuildError",
    "DigestResult",
    "GateResult",
    "ResolveResult",
    "build_citation",
    "build_citations",
    "edgar_url_matches_accession",
    "evaluate",
    "render_digest",
    "resolve_all",
    "resolve_citation",
]
