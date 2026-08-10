"""WP4 — section-aware, deterministic text diff (LLM-free, byte-anchored).

Diffs each section/item of the current filing against the SAME section of the prior same-form
filing for that CIK (matched by the format-independent ``unit_id`` the parser assigns), using
``difflib.SequenceMatcher`` over the sections' text *runs*. A run is a paragraph-sized block
between tags, so a changed run maps directly to a clean, contiguous raw byte span — the
citation anchor (Invariant 1).

Determinism (Invariant 2): ``difflib`` is pure; iteration follows the filing's own run order;
identical inputs yield byte-identical :class:`TextDelta` sequences across runs.

Fail-closed citation policy: a delta is emitted only when it can be cited by *bytes present in
the current filing* — i.e. inserted or replaced text. A pure deletion (text only in the prior
filing) has no current bytes to quote, so it is not emitted as a text delta rather than
carrying an uncitable claim (Invariant 1 / 12).
"""
from __future__ import annotations

from difflib import SequenceMatcher

from filings.delta.models import TextDelta
from filings.parse._html import coalesce_lines, verbatim
from filings.parse.models import ParsedFiling, ParsedUnit, ParseError, TextRun

# A brand-new section's citation snippet is bounded to its heading + first content runs so it
# stays a quotable excerpt rather than the whole section body (the span still round-trips).
_NEW_SECTION_ANCHOR_RUNS = 3

# Minimum alphabetic characters for a changed block to count as a *narrative* text delta. A
# changed financial-statement table cell, a page number ("32"), or a bare figure carries almost
# no letters — and a changed *number* is the numeric XBRL delta's job (period-matched, cited),
# not a text delta's. This keeps section text deltas to genuine prose changes and drops the
# table/number noise that would otherwise flood (and duplicate) the numeric signal.
_MIN_PROSE_ALPHA = 12


def _is_prose(text: str) -> bool:
    """True if a changed block reads as narrative prose (vs a table cell / bare number)."""
    return sum(c.isalpha() for c in text) >= _MIN_PROSE_ALPHA


def _snippet(raw: bytes, start: int, end: int) -> str | None:
    """BUG-086: the citation contract is utf-8 by construction (see ``verbatim``), and the
    resolver enforces byte-length parity under utf-8 — a latin-1/cp1252 byte in a changed
    run CANNOT satisfy the stored contract. It used to raise UnicodeDecodeError out of the
    whole filing's delta computation, silently costing every OTHER delta of that filing.
    Contain it to the one uncitable delta: return None and let the caller skip it."""
    try:
        return verbatim(raw, start, end)
    except UnicodeDecodeError:
        return None


def _atom_span(atoms: list[TextRun], j1: int, j2: int) -> tuple[int, int]:
    """Byte span covering current atoms ``[j1:j2)`` (contiguous block of changed atoms)."""
    return atoms[j1].byte_start, atoms[j2 - 1].byte_end


def _diff_atoms(
    raw: bytes,
    accession: str,
    unit: ParsedUnit,
    prior_atoms: list[TextRun],
    current_atoms: list[TextRun],
    *,
    prose_filter: bool,
) -> list[TextDelta]:
    """Section-aware diff core shared by the run-level and coalesced-line differs.

    ``prior_atoms`` / ``current_atoms`` are byte-anchored text units (either raw
    :class:`TextRun` runs, or coalesced *lines* from :func:`coalesce_lines`). A ``replace`` /
    ``insert`` op cites the CURRENT atoms ``[j1:j2)`` — text present in ``current`` — so the
    fail-closed rule holds: a pure ``delete`` (prior-only text) has no current bytes to quote
    and is skipped. When ``prose_filter`` is set, a bare-number / table-cell change is skipped
    (the run-level narrative differ's policy); the coalesced-line differ turns it OFF so a
    complete figure row ("iPhone $ 56,994 ...") is citable as one line.
    """
    prior_texts = [a.text for a in prior_atoms]
    current_texts = [a.text for a in current_atoms]
    sm = SequenceMatcher(a=prior_texts, b=current_texts, autojunk=False)
    out: list[TextDelta] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or op == "delete":
            # "delete" = text only in the prior filing -> no current bytes to cite. Skipped.
            continue
        # op in {"replace", "insert"}: current atoms [j1:j2) are new/changed and citable.
        if j2 <= j1:
            continue
        start, end = _atom_span(current_atoms, j1, j2)
        cur_text = " ".join(current_texts[j1:j2]).strip()
        if not cur_text:
            continue
        if prose_filter and not _is_prose(cur_text):
            # A bare-number / table-cell change — covered by the numeric XBRL delta. Skip.
            continue
        prior_text = " ".join(prior_texts[i1:i2]).strip() if op == "replace" else ""
        snippet = _snippet(raw, start, end)
        if snippet is None:
            continue                       # BUG-086: uncitable bytes cost ONE delta, not the filing
        out.append(
            TextDelta(
                unit_id=unit.unit_id,
                title=unit.title,
                change_type=op,
                raw_ref=accession,
                byte_start=start,
                byte_end=end,
                snippet=snippet,
                prior_text=prior_text,
                current_text=cur_text,
                changed_runs=(j2 - j1),
                total_runs=len(current_atoms),
                part=unit.part,
                item=unit.item,
            )
        )
    return out


def _diff_matched_unit(
    raw: bytes,
    accession: str,
    prior: ParsedUnit,
    current: ParsedUnit,
) -> list[TextDelta]:
    """Run-level narrative diff of one matched section (the original, unchanged behavior)."""
    return _diff_atoms(
        raw, accession, current, list(prior.runs), list(current.runs), prose_filter=True
    )


def _new_section_delta(raw: bytes, accession: str, unit: ParsedUnit,
                       *, change_type: str = "new_section") -> TextDelta | None:
    if not unit.runs:
        return None
    anchor_runs = unit.runs[:_NEW_SECTION_ANCHOR_RUNS]
    start = anchor_runs[0].byte_start
    end = anchor_runs[-1].byte_end
    snippet = _snippet(raw, start, end)
    if snippet is None:
        return None                        # BUG-086: uncitable bytes cost one delta, not the filing
    return TextDelta(
        unit_id=unit.unit_id,
        title=unit.title,
        change_type=change_type,
        raw_ref=accession,
        byte_start=start,
        byte_end=end,
        snippet=_snippet(raw, start, end),
        prior_text="",
        current_text=unit.text,
        changed_runs=len(unit.runs),
        total_runs=len(unit.runs),
        part=unit.part,
        item=unit.item,
    )


def _reconcile_sections(current: ParsedFiling, prior: ParsedFiling) -> None:
    """Fail-closed structural reconciliation of two same-form filings (Invariant 12).

    Two consecutive same-form 10-K/10-Q filings for a CIK must expose the SAME set of
    ``part<X>_item<Y>`` section identities — a quarter does not silently gain or lose a whole
    numbered item. If a whole Part+item section present in one filing has NO counterpart in the
    other, the two filings' sections cannot be matched 1:1: a section-identity failure (e.g. a
    Part divider that did not parse in one rendering, reparenting its items ``partII_* ->
    partI_*``). Left unchecked this is FAIL-OPEN — the orphaned section mints a false
    ``new_section`` delta for byte-identical text while its real change is silently dropped
    (never diffed against its true counterpart, whose ``unit_id`` no longer matches).

    So instead of trusting a mismatched structure, quarantine: raise :class:`ParseError` exactly
    as a malformed filing does, so the orchestrator surfaces it (no partial, silently-derived
    rows). Only ``part``-bearing 10-K/10-Q sections are reconciled; an 8-K (``part is None``,
    per-filing extraction, never paired) is untouched. Fires only on a genuine unmatched-section
    mismatch — the real symmetric fixtures reconcile cleanly and are unaffected.
    """
    cur = {u.unit_id for u in current.units if u.part is not None}
    pri = {u.unit_id for u in prior.units if u.part is not None}
    if cur == pri:
        return
    # BUG-072: distinguish the two mismatch classes. REPARENTING — the same item number
    # present in both filings under DIFFERENT parts (the unparsed-Part-divider failure this
    # guard exists for) — still quarantines: the sections cannot be matched 1:1. A CLEAN
    # add/drop of a whole item (its number absent from the other filing entirely) is
    # legitimate filing variation — a 10-Q omits Item 1A when risk factors are unchanged,
    # exhibit items come and go — and used to quarantine the ENTIRE filing, silently
    # costing every delta it had. The differ already handles it fail-closed: current-only
    # sections mint new_section, prior-only sections are the pure-deletion case.
    cur_only_items = {u.item for u in current.units
                      if u.part is not None and u.unit_id not in pri}
    pri_only_items = {u.item for u in prior.units
                      if u.part is not None and u.unit_id not in cur}
    reparented = sorted(str(i) for i in (cur_only_items & pri_only_items))
    if not reparented:
        return
    raise ParseError(
        "10-K/10-Q section identity mismatch between paired filings "
        f"{current.accession!r} and {prior.accession!r} — item(s) {reparented} appear in "
        "BOTH filings under DIFFERENT parts, indicating a section-identity failure (e.g. an "
        "unparsed Part divider reparenting its items). Quarantine rather than emit false "
        "new_section deltas / drop the real diff (Inv 12). "
        f"prior_only={sorted(pri - cur)} current_only={sorted(cur - pri)}"
    )


def diff_filings(current: ParsedFiling, prior: ParsedFiling,
                 *, removed_direction: bool = False) -> list[TextDelta]:
    """Section-aware text deltas of ``current`` vs the prior same-form filing.

    Iterates the current filing's units in document order; matches each to the prior filing's
    unit of the same ``unit_id``. Matched units are run-diffed. Before diffing, the two filings'
    Part+item section identities are reconciled (:func:`_reconcile_sections`): a REPARENTED
    section quarantines fail-closed rather than fabricating a ``new_section`` delta and dropping
    the real change (Invariant 12). Deterministic and byte-anchored.

    ``removed_direction=True`` is for the engine's REVERSED pass (``diff_filings(prior,
    current)``): a section found only in the first argument is then a section the real
    current filing REMOVED, and is labeled ``removed_section`` (BUG-071) — every
    consecutive-8-K pair hit this, publishing the old filing's items as brand-new
    ``new_section`` deltas (with the materiality bump) attributed to the prior filing.
    """
    _reconcile_sections(current, prior)
    prior_map = prior.unit_map
    deltas: list[TextDelta] = []
    for unit in current.units:
        prior_unit = prior_map.get(unit.unit_id)
        if prior_unit is None:
            nd = _new_section_delta(
                current.raw, current.accession, unit,
                change_type="removed_section" if removed_direction else "new_section",
            )
            if nd is not None:
                deltas.append(nd)
            continue
        deltas.extend(
            _diff_matched_unit(current.raw, current.accession, prior_unit, unit)
        )
    return deltas


def diff_filings_coalesced(current: ParsedFiling, prior: ParsedFiling) -> list[TextDelta]:
    """Coalesced-line section-aware text deltas of ``current`` vs the prior same-form filing.

    Same section matching and citation policy as :func:`diff_filings`, but each unit's runs are
    first coalesced into complete *lines* (:func:`coalesce_lines`) so an inline-XBRL-fragmented
    figure ("...were $26 billion") or a whole table row ("iPhone $ 56,994 $ 46,841 22 %") is
    cited as ONE complete byte-anchored snippet instead of truncating at the ``$`` before the
    number. The prose filter is off at this granularity (a changed figure row IS material text).
    Every cited span still round-trips byte-for-byte (Invariant 1); deterministic (Invariant 2).
    Fail-closed on a section-identity mismatch, same as :func:`diff_filings` (Invariant 12).
    """
    _reconcile_sections(current, prior)
    prior_map = prior.unit_map
    deltas: list[TextDelta] = []
    for unit in current.units:
        cur_lines = coalesce_lines(current.raw, list(unit.runs))
        prior_unit = prior_map.get(unit.unit_id)
        prior_lines = (
            coalesce_lines(prior.raw, list(prior_unit.runs)) if prior_unit is not None else []
        )
        deltas.extend(
            _diff_atoms(
                current.raw, current.accession, unit, prior_lines, cur_lines, prose_filter=False
            )
        )
    return deltas


def extract_filing(filing: ParsedFiling) -> list[TextDelta]:
    """Per-filing EXTRACTION (no prior): every unit's coalesced lines as byte-anchored deltas.

    Used for an unpaired 8-K, where each numbered item is a self-contained event disclosure to
    surface (not a before/after diff). Each coalesced line of each item becomes a ``TextDelta``
    citing the exact bytes of that line in the 8-K (Invariant 1). Reuses :func:`_diff_atoms`
    with an empty prior side, so every line is an ``insert`` — deterministic, fail-closed.
    """
    deltas: list[TextDelta] = []
    for unit in filing.units:
        cur_lines = coalesce_lines(filing.raw, list(unit.runs))
        deltas.extend(
            _diff_atoms(filing.raw, filing.accession, unit, [], cur_lines, prose_filter=False)
        )
    return deltas
