"""WP4 — shared segment-assembly helpers for the ``parse/`` segmenters (LLM-free).

``parse_10kq`` (10-K/10-Q sections) and ``parse_8k`` (8-K items) segment different heading
schemes into the same :class:`ParsedUnit` shape, so the mechanics of assembling units — which
heading offset closes a section, how a repeated heading still yields a unique ``unit_id``, and
which text runs belong to a segment — live here once instead of in two copies.

Pure functions only: no LLM, no subprocess, no network (Invariant 2).
"""
from __future__ import annotations

from collections.abc import Sequence

from filings.parse.models import TextRun


def next_boundary(boundaries: Sequence[int], after: int, raw_len: int) -> int:
    """First boundary strictly after ``after``; ``raw_len`` when none remains.

    ``boundaries`` is the caller's sorted set of heading offsets (plus ``len(raw)``) — every
    heading both opens a unit and closes the previous one.
    """
    for b in boundaries:
        if b > after:
            return b
    return raw_len


def dedupe_unit_id(seen: dict[str, int], base_id: str) -> str:
    """Unique unit id for ``base_id``; an exact repeat gets ``#<n>`` appended."""
    seen[base_id] = seen.get(base_id, 0) + 1
    return base_id if seen[base_id] == 1 else f"{base_id}#{seen[base_id]}"


def runs_in_span(runs: Sequence[TextRun], start: int, end: int) -> tuple[TextRun, ...]:
    """Runs whose byte offset lies in ``[start, end)`` — the unit's own anchored text."""
    return tuple(r for r in runs if start <= r.byte_start < end)
