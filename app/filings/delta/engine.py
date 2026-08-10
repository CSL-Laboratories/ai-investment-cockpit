"""WP4 — the public delta-engine orchestrator (deterministic, LLM-free citation core).

:func:`compute_deltas` is the single WP4 entry point the rest of the pipeline (and the golden
suite) drives. It ties the proven building blocks together for one filing and, when present, its
prior same-form filing for that CIK:

1. **Load** the raw filing bytes from the write-once :class:`~filings.raw_store.RawStore`
   (the citation anchor Invariant 1 resolves back to).
2. **Parse** them deterministically (``filings.parse``), dispatching on ``Filing.form_type``:
   10-K/10-Q -> :func:`~filings.parse.sections_10kq.parse_10kq`, 8-K -> ``parse_8k``.
3. **Text deltas** — a *paired* filing is diffed section-aware (run-level + complete-figure
   coalesced-line, both directions so a REMOVED passage is cited to the prior filing); an
   *unpaired* 8-K (``prior_filing is None``) is a per-item EXTRACTION, never diffed against nothing.
4. **Numeric deltas** — the supplied WP1a ``XbrlFact`` rows are reconciled period-matched, each
   anchored by byte offset to the value as displayed in the CURRENT filing's inline XBRL; a
   value that cannot be located is skipped fail-closed (Inv 1 / 12) — never a fabricated offset.
5. **Mint** ranked schema ``Delta`` rows via the existing materiality path, permissively (no
   threshold / top-N) so no material change is dropped, each born with a resolvable ``raw_ref`` +
   byte offsets + verbatim snippet.

Determinism (Invariant 2): every step is a pure function of the inputs and a fixed clock, so
identical inputs produce byte-identical ``Delta`` sets. No LLM, no subprocess, no network here or
anywhere under ``app/parse`` / ``app/delta``.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from core.clock import Clock, default_clock
from filings.delta.materiality import build_deltas
from filings.delta.models import NumericDelta, TextDelta
from filings.delta.text_diff import diff_filings, diff_filings_coalesced, extract_filing
from filings.delta.xbrl_delta import diff_facts
from filings.parse.items_8k import parse_8k
from filings.parse.models import ParsedFact, ParsedFiling
from filings.parse.sections_10kq import parse_10kq
from schemas.models.delta import Delta

_TENKQ_FORMS = frozenset({"10-K", "10-Q"})
_EIGHTK_FORMS = frozenset({"8-K"})


class UnpairedPeriodicFiling(RuntimeError):
    """A 10-K/10-Q is a reference filing until a prior same-form filing exists.

    Emitting a whole-filing extraction for an unpaired periodic report would mislabel the
    reference document as a set of changes. Schedulers should mark this condition
    reference-only/nonqualifying and wait for the next same-form filing.
    """

    def __init__(self, accession_no: str, form_type: str):
        super().__init__(
            f"{form_type} {accession_no} has no prior same-form filing; "
            "store as reference-only and emit no deltas"
        )
        self.accession_no = accession_no
        self.form_type = form_type


# ---------------------------------------------------------------------------------------
# Input adapters — accept the WP1a ORM shapes the pipeline supplies.
# ---------------------------------------------------------------------------------------
def _form_type(filing: Any) -> str:
    """The filing's SEC form label ("10-K"/"10-Q"/"8-K"), tolerant of str or enum."""
    ft = filing.form_type
    return getattr(ft, "value", ft)


def _primary_filename(filing: Any) -> str:
    """Primary-document filename for the EDGAR archive URL.

    Prefers the REAL primary-document name captured at ingest (``Filing.primary_doc``, e.g.
    ``nvda-20260426.htm``, sourced from the SEC submissions feed) so the citation URL resolves
    to the actual document Lucas clicks in the morning digest. Falls back to a stable synthesized
    name from the accession for rows without it (e.g. fixtures built before the column existed);
    the archive path already carries the accession, which is what pins the citation to the filing.
    """
    real = getattr(filing, "primary_doc", None)
    if real:
        return str(real)
    return f"{str(filing.accession_no).replace('-', '')}.htm"


def _load_raw(raw_store: Any, filing: Any) -> bytes:
    """Read a filing's byte-identical raw blob from the raw store (Inv 1 / Inv 5 anchor).

    Uses the integrity-checked ``resolve`` path whenever one is available, trying the
    ``raw_ref`` pointer then the accession key.  A failed integrity check is evidence of a
    tampered raw blob, not a reason to retry an unverified ``read`` path.
    """
    keys: list[str] = []
    for attr in ("raw_ref", "accession_no", "accession"):
        val = getattr(filing, attr, None)
        if val and str(val) not in keys:
            keys.append(str(val))
    resolver = getattr(raw_store, "resolve", None)
    last_err: FileNotFoundError | None = None
    if resolver is not None:
        for key in keys:
            try:
                return resolver(key)
            except FileNotFoundError as exc:
                last_err = exc
            except Exception:
                raise

    # A minimal test double may expose only ``read``.  Production RawStore always exposes
    # ``resolve``; this compatibility path is never taken after an integrity-check failure.
    reader = getattr(raw_store, "read", None)
    if resolver is None and reader is not None:
        for key in keys:
            try:
                return reader(key)
            except FileNotFoundError as exc:
                last_err = exc
    raise FileNotFoundError(
        f"raw filing bytes not resolvable for {keys!r} in {type(raw_store).__name__}"
        + (f" ({last_err!r})" if last_err else "")
    )


def _parse_filing(filing: Any, raw: bytes) -> ParsedFiling:
    """Deterministically segment a raw filing, dispatching on its form type."""
    cik = str(filing.cik)
    accession = str(filing.accession_no)
    form = _form_type(filing)
    filename = _primary_filename(filing)
    if form in _TENKQ_FORMS:
        return parse_10kq(raw, cik=cik, accession=accession, form_type=form, filename=filename)
    if form in _EIGHTK_FORMS:
        return parse_8k(raw, cik=cik, accession=accession, filename=filename, form_type=form)
    # Form 4 / 13F processing is deferred (scaffold); fall back to 8-K item segmentation so an
    # unexpected form still produces byte-anchored, citeable units rather than a hard failure.
    return parse_8k(raw, cik=cik, accession=accession, filename=filename, form_type=form)


def _xbrlfact_to_parsedfact(xf: Any) -> ParsedFact:
    """Adapt one WP1a ``XbrlFact`` ORM row to the parse layer's :class:`ParsedFact` value object."""
    return ParsedFact(
        taxonomy=xf.taxonomy,
        concept=xf.concept,
        unit=xf.unit,
        value=xf.value if isinstance(xf.value, Decimal) else Decimal(str(xf.value)),
        period_end=xf.period_end,
        accession=str(getattr(xf, "accession_no", None) or getattr(xf, "accession", "")),
        cik=str(xf.cik),
        period_start=getattr(xf, "period_start", None),
        fiscal_year=getattr(xf, "fiscal_year", None),
        fiscal_period=getattr(xf, "fiscal_period", None),
        form=getattr(xf, "form", None),
        filed=getattr(xf, "filed", None),
        frame=getattr(xf, "frame", None),
        label=getattr(xf, "label", None),
    )


def _as_parsed_facts(facts: Iterable[Any] | None) -> list[ParsedFact]:
    if not facts:
        return []
    return [f if isinstance(f, ParsedFact) else _xbrlfact_to_parsedfact(f) for f in facts]


# ---------------------------------------------------------------------------------------
# Text-delta assembly.
# ---------------------------------------------------------------------------------------
def _dedup_text(deltas: Iterable[TextDelta]) -> list[TextDelta]:
    """Drop exact-span duplicates (a coalesced line that coincides with a single run) while
    preserving first-seen document order — deterministic (Invariant 2)."""
    seen: set[tuple[str, str, int, int]] = set()
    out: list[TextDelta] = []
    for td in deltas:
        key = (td.raw_ref, td.source_ref, td.byte_start, td.byte_end)
        if key in seen:
            continue
        seen.add(key)
        out.append(td)
    return out


def _paired_text_deltas(
    current: ParsedFiling, prior: ParsedFiling
) -> list[TextDelta]:
    """Bidirectional, section-aware text deltas for a before/after filing pair.

    Runs the proven run-level :func:`diff_filings` AND the complete-figure
    :func:`diff_filings_coalesced`, in BOTH directions: the current direction cites new/changed
    passages to the current filing; the reversed direction cites REMOVED passages to the prior
    filing (they have no bytes in the current filing to quote). Deduped by exact citation span.
    """
    deltas: list[TextDelta] = []
    deltas += diff_filings(current, prior)
    deltas += diff_filings_coalesced(current, prior)
    # BUG-071: the reversed pass cites REMOVED material to the prior filing — its
    # prior-only sections are removals and must not read as new_section (which would both
    # mislabel the change and collect the new-section materiality bump).
    deltas += diff_filings(prior, current, removed_direction=True)
    deltas += diff_filings_coalesced(prior, current)
    return _dedup_text(deltas)


# ---------------------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------------------
def compute_deltas(
    current_filing: Any,
    prior_filing: Any | None = None,
    raw_store: Any = None,
    current_facts: Sequence[Any] | None = None,
    prior_facts: Sequence[Any] | None = None,
    *,
    clock: Clock = default_clock,
) -> list[Delta]:
    """Compute the ranked, citation-anchored ``Delta`` rows for one filing.

    :param current_filing: WP1a ``Filing`` (``.cik``/``.accession_no``/``.form_type``/``.raw_ref``).
    :param prior_filing: the prior same-form ``Filing`` to diff against, or ``None`` for an
        unpaired 8-K (per-filing extraction).
    :param raw_store: the write-once :class:`~filings.raw_store.RawStore` holding both filings'
        raw bytes (resolved by ``raw_ref`` / accession).
    :param current_facts / prior_facts: the current/prior filing's WP1a ``XbrlFact`` rows for the
        period-matched numeric diff (empty/omitted for an 8-K or when no XBRL is available).
    :returns: a list of schema ``Delta`` rows, each born with a resolvable ``raw_ref`` + byte
        offsets + verbatim snippet + materiality score. Deterministic (Invariant 2).
    """
    if raw_store is None:
        raise ValueError("compute_deltas requires a raw_store to resolve filing bytes (Inv 1)")

    current_form = _form_type(current_filing)
    if prior_filing is None and current_form in _TENKQ_FORMS:
        # Guard before reading/parsing bytes: an unpaired periodic filing is a baseline
        # reference, never an 8-K-style extraction mislabeled as a delta batch.
        raise UnpairedPeriodicFiling(str(current_filing.accession_no), current_form)

    current_parsed = _parse_filing(current_filing, _load_raw(raw_store, current_filing))
    prior_parsed: ParsedFiling | None = None
    if prior_filing is not None:
        prior_parsed = _parse_filing(prior_filing, _load_raw(raw_store, prior_filing))

    # --- TEXT ---------------------------------------------------------------------------
    if prior_parsed is not None:
        text_deltas = _paired_text_deltas(current_parsed, prior_parsed)
    else:
        # Unpaired 8-K: extract each item's disclosure; never diff against nothing.
        text_deltas = extract_filing(current_parsed)

    # --- NUMERIC (only meaningful for a before/after pair with XBRL facts) --------------
    numeric_deltas: list[NumericDelta] = []
    cur_pf = _as_parsed_facts(current_facts)
    prior_pf = _as_parsed_facts(prior_facts)
    if prior_parsed is not None and cur_pf and prior_pf:
        numeric_deltas = diff_facts(
            cur_pf,
            prior_pf,
            current_accession=current_parsed.accession,
            current_raw=current_parsed.raw,
        )

    # --- MINT: group findings by the filing they cite, mint with that filing's cik+filename ---
    parsed_by_acc: dict[str, ParsedFiling] = {current_parsed.accession: current_parsed}
    if prior_parsed is not None:
        parsed_by_acc.setdefault(prior_parsed.accession, prior_parsed)

    text_by_acc: dict[str, list[TextDelta]] = defaultdict(list)
    for td in text_deltas:
        text_by_acc[td.raw_ref].append(td)
    numeric_by_acc: dict[str, list[NumericDelta]] = defaultdict(list)
    for nd in numeric_deltas:
        numeric_by_acc[nd.raw_ref].append(nd)

    rows: list[Delta] = []
    for accession, pf in parsed_by_acc.items():
        rows.extend(
            build_deltas(
                text_by_acc.get(accession, []),
                numeric_by_acc.get(accession, []),
                cik=pf.cik,
                filename=pf.filename,
                clock=clock,
                threshold=None,
                top_n=None,
            )
        )
    return rows
