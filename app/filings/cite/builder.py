"""WP5 — deterministic ``Citation`` builder (Invariant 1, fail-closed).

Mints EXACTLY ONE stored ``Citation`` per computed ``Delta``, 1:1 and in input order, by
copying the Delta's already-anchored fields (accession, EDGAR URL, byte offsets, verbatim
snippet, raw_ref) and stamping the source filing's filing date. No LLM, no subprocess, no
network — a pure function of (Delta, Filing) and an injected clock.

Determinism + no-reuse (Invariant 1): ``citation_id`` is derived from ``delta_id``
(``"cite_" + sha256(delta_id)[:24]``), so repeat runs are byte-identical and one id is
NEVER minted for two different deltas — the reverse of the WP5 ``claim_gate`` duplicate
sweep. A guessed or ``None`` filing date is impossible: a Delta whose accession is absent
from ``filings`` (or whose filing carries no date) FAILS CLOSED with
:class:`CitationBuildError` rather than emitting a citation with an invented date.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from core.clock import Clock, default_clock
from schemas.models.citation import Citation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable, Mapping

    from schemas.models.delta import Delta
    from schemas.models.filing import Filing


class CitationBuildError(RuntimeError):
    """Raised when a Delta cannot be turned into a well-formed, fully-grounded Citation.

    Fail-closed (Invariant 12): the only correct response to a missing filing (hence a
    missing, un-guessable filing date) is to refuse — never to emit a citation with a
    fabricated ``filing_date``.
    """


def _citation_id(delta_id: str) -> str:
    """Deterministic, 1:1-with-delta_id citation id (never reused across two deltas)."""
    digest = hashlib.sha256(delta_id.encode("utf-8")).hexdigest()
    return "cite_" + digest[:24]


def _fallback_source_ref(delta: Any) -> str:
    """A deterministic ref when the Delta's label is absent or blank/whitespace-only."""
    source_ref = getattr(delta, "source_ref", None)
    if isinstance(source_ref, str) and source_ref.strip():
        return source_ref
    return f"{delta.kind}:{delta.accession_no}:{delta.snippet_start_offset}"


def build_citation(
    delta: Delta, filing: Filing, *, clock: Clock = default_clock
) -> Citation:
    """Mint the single ``Citation`` for one ``Delta`` using its source ``Filing``.

    ``filing`` must be the filing named by ``delta.accession_no`` (it supplies the
    non-guessable ``filing_date``). All citation-anchor fields are copied verbatim from the
    Delta so the WP5 resolver re-reads exactly the bytes WP4 recorded.
    """
    filing_date = getattr(filing, "filing_date", None)
    if filing_date is None:
        # Fail closed: no real filing date -> no citation (Inv 1: never a guessed date).
        raise CitationBuildError(
            f"cannot build citation for delta {delta.delta_id!r}: source filing for "
            f"accession {delta.accession_no!r} has no filing_date"
        )

    source_ref = _fallback_source_ref(delta)
    return Citation(
        citation_id=_citation_id(delta.delta_id),
        delta_id=delta.delta_id,
        accession_no=delta.accession_no,
        edgar_url=delta.edgar_url,
        filing_date=filing_date,
        source_ref=source_ref,
        snippet=delta.snippet,
        byte_start=delta.snippet_start_offset,
        byte_end=delta.snippet_end_offset,
        raw_ref=delta.raw_ref,
        created_ts=clock.now(),
    )


def build_citations(
    deltas: Iterable[Delta],
    filings: Mapping[str, Filing],
    *,
    clock: Clock = default_clock,
) -> list[Citation]:
    """Mint one ``Citation`` per ``Delta``, 1:1 and in the same order.

    :param filings: mapping ``accession_no`` -> ``Filing`` supplying each delta's
        filing date. A delta whose ``accession_no`` is ABSENT from this mapping fails
        closed (:class:`CitationBuildError`) — never a citation with a guessed date.
    """
    citations: list[Citation] = []
    for delta in deltas:
        try:
            filing = filings[delta.accession_no]
        except (KeyError, TypeError) as exc:
            raise CitationBuildError(
                f"cannot build citation for delta {delta.delta_id!r}: no filing supplied "
                f"for accession {delta.accession_no!r}"
            ) from exc
        citations.append(build_citation(delta, filing, clock=clock))
    return citations
