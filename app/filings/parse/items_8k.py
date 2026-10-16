"""WP4 — deterministic 8-K item-code segmentation (LLM-free, byte-anchored).

An 8-K reports one or more numbered *events* under SEC item codes — e.g. ``5.02`` (departure/
election of directors/officers), ``5.07`` (submission of matters to a shareholder vote),
``2.02`` (results of operations). The Pair-3 fixtures are genuine 8-Ks (NVDA Item 5.02 and
Item 5.07). Each item becomes a byte-anchored :class:`ParsedUnit`, so a downstream ``Delta``
can cite the exact bytes of the event disclosure in the raw filing (Invariant 1).

The heading pattern is ``Item <n>.<nn>`` with the item title in the same run (Workiva isolates
the bold heading in its own span), which the tokenizer surfaces cleanly. Fail-closed
(Invariant 12): an 8-K with no recognizable item code raises :class:`ParseError` so the
caller quarantines the ``Filing`` instead of deriving partial rows.
"""
from __future__ import annotations

import re

from filings.parse._html import runs as html_runs
from filings.parse._segment import dedupe_unit_id, next_boundary, runs_in_span
from filings.parse.models import ParsedFiling, ParsedUnit, ParseError, TextRun

# "Item 5.07. Submission of Matters ..." — code = <digits>.<digits>, title follows.
_ITEM_CODE = re.compile(r"^Item\s+(\d+\.\d+)\.?\s*(\S.*)?$")
_ITEM_HEADING_MAXLEN = 160


def _item_markers(text_runs: list[TextRun]) -> list[tuple[int, int, str, str]]:
    """``(offset, run_index, code, title)`` for each 8-K item heading, in document order."""
    out: list[tuple[int, int, str, str]] = []
    for i, run in enumerate(text_runs):
        t = run.text
        if len(t) > _ITEM_HEADING_MAXLEN:
            continue
        m = _ITEM_CODE.match(t)
        if m:
            out.append((run.byte_start, i, m.group(1), (m.group(2) or "").strip()))
    return out


def parse_8k(
    raw: bytes,
    *,
    cik: str,
    accession: str,
    filename: str,
    form_type: str = "8-K",
) -> ParsedFiling:
    """Segment a raw 8-K into byte-anchored :class:`ParsedUnit` items (one per item code).

    :raises ParseError: if no item code is found (fail-closed — quarantine).
    """
    text_runs = html_runs(raw)
    markers = _item_markers(text_runs)
    if not markers:
        raise ParseError(
            f"8-K {accession!r}: no item code found — cannot segment "
            "(quarantine, do not derive partial rows)"
        )

    boundaries = sorted({o for o, *_ in markers} | {len(raw)})

    units: list[ParsedUnit] = []
    seen: dict[str, int] = {}
    for offset, _idx, code, title in markers:
        end = next_boundary(boundaries, offset, len(raw))
        base_id = f"item_{code}"
        unit_id = dedupe_unit_id(seen, base_id)
        item_runs = runs_in_span(text_runs, offset, end)
        units.append(
            ParsedUnit(
                unit_id=unit_id,
                kind="item",
                title=title,
                byte_start=offset,
                byte_end=end,
                runs=item_runs,
                part=None,
                item=code,
            )
        )

    return ParsedFiling(
        cik=cik,
        accession=accession,
        form_type=form_type,
        filename=filename,
        raw=raw,
        units=tuple(units),
    )
