"""WP4 — deterministic 10-K / 10-Q item & section segmentation (LLM-free, byte-anchored).

Segments a Workiva inline-XBRL 10-K/10-Q into its Part I/II items — Financial Statements,
**MD&A** (Part I, Item 2) and **Risk Factors** (Part II, Item 1A) called out by the dossier
architecture, plus the other numbered items. Each segment is a :class:`ParsedUnit` carrying
byte offsets into the raw blob (Invariant 1), so the section-aware text differ (``delta/
text_diff.py``) can diff the prior vs current same-form filing and mint a citation-anchored
``Delta``.

How the real body is told apart from the tables of contents (the core segmentation problem):

* A **body item heading** is its own short run containing the item number *and its title*
  (``"Item 2. Management's Discussion and Analysis ..."``). A **TOC entry** puts the title in
  a separate table cell, so its run is a bare ``"Item 1."`` — it never matches the "title in
  the same run" rule and therefore starts no section. This holds for both registrant formats
  in the fixture corpus (NVDA self-filed, AAPL) and both TOCs (the cover TOC and the
  financial-statements index).
* ``Part`` headers set the current part context (so ``Part I Item 2`` = MD&A is distinct from
  ``Part II Item 2`` = Unregistered Sales). TOC ``Part`` headers are harmless: no titled item
  follows them until the body, so they create nothing.

Fail-closed (Invariant 12): a document with no recognizable body item heading raises
:class:`ParseError`; the caller quarantines the ``Filing`` rather than deriving partial rows.
"""
from __future__ import annotations

import re

from filings.parse._html import runs as html_runs
from filings.parse._segment import dedupe_unit_id, next_boundary, runs_in_span
from filings.parse.models import ParsedFiling, ParsedUnit, ParseError, TextRun

# A body item heading: item number + optional letter suffix, a dot, then title text in the
# SAME short run. The length cap rejects a sentence that merely opens with "Item 2." — a
# real heading run is the heading alone (Workiva isolates it in its own span).
_ITEM_HEADING = re.compile(r"^Item\s+(\d+)([A-Z]?)\.\s+(\S.*)$")
_ITEM_HEADING_MAXLEN = 140

# A Part header (cover TOC, FS index, or body). Roman numeral is the part identity.
#
# Cross-filing / cross-render robustness (do NOT reintroduce a length cap): a Part divider is a
# heading-like run that CONTAINS ``Part <roman>`` — NOT necessarily at the run's very start.
# The label may render before, inside, or after the section title, so ``Part II. Other
# Information`` and ``Other Information (Part II)`` are the SAME logical divider and must resolve
# to the same part; anchoring on "starts with Part" (or on a length cap) drops the second form,
# and ``_part_for_offset`` then reparents every Part II section to Part I. When one filing
# renders the divider one way and the other filing renders it the other way, the SAME logical
# section gets different ``unit_id``s across the two filings — minting false ``new_section``
# deltas for byte-identical text and dropping the real Part II changes (round-2 defect A). We
# therefore match ``Part <roman>`` anywhere in the run and gate on :func:`_run_is_heading`,
# which is what still rejects running prose that merely mentions a part.
_PART_ANYWHERE_RE = re.compile(r"\bPart\s+([IVX]+)\b", re.IGNORECASE)

# 8-K style codes ("Item 5.07.") must NOT be treated as 10-K/10-Q items. The item-heading
# regex already excludes them (it requires whitespace after the dot, not another digit), but
# this guard documents the intent and hardens against odd spacing.
_EIGHTK_CODE = re.compile(r"^Item\s+\d+\.\d+")


def _run_is_heading(text: str) -> bool:
    """True if ``text`` reads as a heading/divider run, not running prose.

    A genuine Part divider is a heading: every word is a title word (Title-Case or ALL-CAPS) or
    pure punctuation — ``Other Information (Part II)``, ``Part II — OTHER INFORMATION``. Running
    prose that merely mentions a part always contains a lowercase-initial word ("...as described
    in Part II of this report..."). So the run is a heading iff NO word starts with a lowercase
    letter — length-independent, position-independent, and blind to Title-Case vs ALL-CAPS.
    """
    for token in text.split():
        for ch in token:
            if ch.isalpha():
                if ch.islower():
                    return False  # a lowercase-initial word -> prose, not a heading/divider
                break  # first letter is uppercase -> heading-style word; check the next token
    return True


def _part_roman(text: str) -> str | None:
    """The upper-case roman numeral if ``text`` is a Part DIVIDER heading, else ``None``.

    Length- and position-independent (see :data:`_PART_ANYWHERE_RE`): the run must CONTAIN
    ``Part <roman>`` and read as a heading (:func:`_run_is_heading`) rather than prose. Both
    ``Part II. Other Information`` and ``Other Information (Part II)`` resolve to the same part.
    """
    m = _PART_ANYWHERE_RE.search(text)
    if m is None or not _run_is_heading(text):
        return None
    return m.group(1).upper()


def _find_markers(text_runs: list[TextRun]) -> tuple[list[tuple], list[tuple[int, str]]]:
    """Return (item_markers, part_runs).

    ``item_markers`` = ``(offset, run_index, num, suffix, title)`` for each body item heading;
    ``part_runs`` = ``(byte_offset, roman)`` for every Part divider header, in document order
    (used both as section boundaries and to resolve each item's part context).
    """
    item_markers: list[tuple] = []
    part_runs: list[tuple[int, str]] = []
    for i, run in enumerate(text_runs):
        t = run.text
        roman = _part_roman(t)
        if roman is not None:
            part_runs.append((run.byte_start, roman))
            continue
        if len(t) > _ITEM_HEADING_MAXLEN or _EIGHTK_CODE.match(t):
            continue
        m = _ITEM_HEADING.match(t)
        if m:
            item_markers.append(
                (run.byte_start, i, m.group(1), m.group(2), t)
            )
    return item_markers, part_runs


def _part_for_offset(offset: int, part_runs: list[tuple[int, str]]) -> str | None:
    """The roman numeral of the last Part header at or before ``offset`` (part context)."""
    current: str | None = None
    for p_off, roman in part_runs:
        if p_off <= offset:
            current = roman
        else:
            break
    return current


def parse_10kq(
    raw: bytes,
    *,
    cik: str,
    accession: str,
    form_type: str,
    filename: str,
) -> ParsedFiling:
    """Segment a raw 10-K/10-Q into byte-anchored :class:`ParsedUnit` sections.

    :raises ParseError: if no body item heading is found (fail-closed — quarantine).
    """
    text_runs = html_runs(raw)
    item_markers, part_runs = _find_markers(text_runs)

    if not item_markers:
        raise ParseError(
            f"10-K/10-Q {accession!r}: no body item heading found — "
            "cannot segment (quarantine, do not derive partial rows)"
        )

    # Part context list: (offset, roman) in document order — already resolved by _find_markers.
    part_offsets = [off for off, _roman in part_runs]

    # Boundaries that close a section: every item heading start AND every part header start.
    boundaries = sorted({o for o, *_ in item_markers} | set(part_offsets) | {len(raw)})

    units: list[ParsedUnit] = []
    seen_ids: dict[str, int] = {}
    for offset, _run_idx, num, suffix, title in item_markers:
        end = next_boundary(boundaries, offset, len(raw))
        part = _part_for_offset(offset, part_runs)
        item_label = f"{num}{suffix}"
        base_id = f"part{part}_item{item_label}" if part else f"item{item_label}"
        # Disambiguate a rare exact-repeat within the same part (append an index).
        unit_id = dedupe_unit_id(seen_ids, base_id)

        section_runs = runs_in_span(text_runs, offset, end)
        units.append(
            ParsedUnit(
                unit_id=unit_id,
                kind="section",
                title=title.strip(),
                byte_start=offset,
                byte_end=end,
                runs=section_runs,
                part=part,
                item=item_label,
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
