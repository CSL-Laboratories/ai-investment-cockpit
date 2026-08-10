"""WP4 — period-matched numeric XBRL deltas from ``companyfacts`` (LLM-free, byte-anchored).

Two deterministic comparisons between the current filing and the prior same-form filing for a
CIK, both cited to the value *as reported in the current filing's inline XBRL* (Invariant 1):

* **Period-over-period.** For each concept+unit, compare each filing's *headline* fact
  (``select_primary``: latest period, shortest duration). Different periods are the norm for
  consecutive filings (e.g. this quarter vs the last reported quarter); the delta records both
  period ends explicitly so the change is never mislabeled as same-period.
* **Restatement.** For a concept+unit+period reported by BOTH filings with a *different* value
  (an amended ``/A`` filing, or a revised comparative), emit a restatement delta — the
  cleanest period-matched signal, flagged ``is_restatement=True``.

A delta is emitted only if the current value can be anchored to a displayed
``<ix:nonFraction>`` element in the current filing (``find_inline_anchor``); an unciteable
figure is skipped, never emitted uncited (fail-closed, Invariant 1 / 12).

No LLM, no subprocess, no network (Invariant 2). Ordering derives from ``companyfacts``
insertion order, so repeated runs are byte-identical.
"""
from __future__ import annotations

from decimal import Decimal

from filings.delta.models import NumericDelta, _quarter_span
from filings.parse.models import ParsedFact
from filings.parse.xbrl_facts import (
    anchor_snippet,
    facts_for_accession,
    find_inline_anchor,
    iter_inline_facts,
    select_primary,
)


def _pct_change(prior: Decimal, current: Decimal) -> Decimal | None:
    if prior == 0:
        return None
    return (current - prior) / abs(prior)


def _comparable_periods(f_cur: ParsedFact, f_prior: ParsedFact) -> bool:
    """True iff two facts span like-for-like reporting periods (period-comparability gate).

    A numeric period-over-period delta is only meaningful when both sides cover a
    comparable-length period. An *instant* / balance-sheet fact (no ``period_start``) is
    comparable only to another instant; two *duration* facts are comparable only when they fall
    in the same fiscal-quarter bucket (:func:`~filings.delta.models._quarter_span`). This is what
    keeps a 90-day quarter from being differenced against a 272-day year-to-date roll-up — a
    mismatch is dropped fail-closed (Invariant 12) rather than emitted as a false delta.
    """
    cur_inst = f_cur.period_start is None
    prior_inst = f_prior.period_start is None
    if cur_inst or prior_inst:
        return cur_inst and prior_inst
    return _quarter_span(f_cur.duration_days) == _quarter_span(f_prior.duration_days)


def diff_companyfacts(
    companyfacts: dict,
    *,
    current_accession: str,
    prior_accession: str,
    current_raw: bytes,
    cik: str,
) -> list[NumericDelta]:
    """Numeric XBRL deltas of the current filing vs the prior same-form filing.

    :param current_raw: the current filing's raw HTML bytes (as stored in the raw store keyed
        by ``current_accession``) — the source of the citation offsets.
    """
    cur_facts = facts_for_accession(companyfacts, current_accession, cik=cik)
    prior_facts = facts_for_accession(companyfacts, prior_accession, cik=cik)
    return diff_facts(
        cur_facts,
        prior_facts,
        current_accession=current_accession,
        current_raw=current_raw,
    )


def diff_facts(
    current_facts: list[ParsedFact],
    prior_facts: list[ParsedFact],
    *,
    current_accession: str,
    current_raw: bytes,
) -> list[NumericDelta]:
    """Numeric XBRL deltas over already-normalized :class:`ParsedFact` rows.

    The period-matched core of :func:`diff_companyfacts`, split out so the WP4 orchestrator can
    feed it the WP1a ``XbrlFact`` rows (converted to :class:`ParsedFact`) that the pipeline
    supplies, instead of a raw ``companyfacts`` dict. Both entry points share this one body, so
    the restatement + period-over-period logic and the fail-closed inline-anchor citation policy
    (Invariant 1) are identical regardless of how the facts were sourced.
    """
    cur_primary = select_primary(current_facts)
    prior_primary = select_primary(prior_facts)
    inline = iter_inline_facts(current_raw)

    deltas: list[NumericDelta] = []
    emitted: set[tuple[str, str, int]] = set()  # (concept, unit, byte_start) dedupe

    def emit(f_cur: ParsedFact, f_prior: ParsedFact, *, restatement: bool) -> None:
        if f_cur.value == f_prior.value:
            return
        # Period-comparability gate (Invariant 12): never difference two facts that span
        # non-comparable-length periods (e.g. a 90-day quarter vs a 272-day YTD roll-up). A
        # restatement pairs on an *identical* period, so it is trivially comparable and always
        # passes; only a mismatched period-over-period pairing is suppressed here.
        if not _comparable_periods(f_cur, f_prior):
            return
        anchor = find_inline_anchor(f_cur, inline)
        if anchor is None:
            return  # uncitable in the current filing -> skip (fail-closed)
        try:
            snippet = anchor_snippet(current_raw, anchor)
        except UnicodeDecodeError:
            return  # BUG-086: a non-utf8 anchor is uncitable — skip this fact, not the filing
        key = (f_cur.concept, f_cur.unit, anchor.byte_start)
        if key in emitted:
            return
        emitted.add(key)
        abs_change = f_cur.value - f_prior.value
        deltas.append(
            NumericDelta(
                taxonomy=f_cur.taxonomy,
                concept=f_cur.concept,
                unit=f_cur.unit,
                raw_ref=current_accession,
                byte_start=anchor.byte_start,
                byte_end=anchor.byte_end,
                # Verbatim citation snippet in the SAME utf-8 encoding as the Citation contract,
                # so byte_end - byte_start == len(snippet.encode("utf-8")) and the raw slice
                # decodes back to this snippet (Invariant 1 round-trip).
                snippet=snippet,
                prior_value=f_prior.value,
                current_value=f_cur.value,
                abs_change=abs_change,
                pct_change=_pct_change(f_prior.value, f_cur.value),
                current_period_end=f_cur.period_end,
                prior_period_end=f_prior.period_end,
                current_period_start=f_cur.period_start,
                prior_period_start=f_prior.period_start,
                is_restatement=restatement,
                label=f_cur.label,
            )
        )

    # 1) Restatements: same concept+unit+period reported by both filings, value changed.
    # A within-accession duplicate — the SAME concept+unit+period reported twice in the prior
    # filing with DIFFERENT values — has no single "true prior", so pairing against it could
    # mint a fabricated restatement. Detect that collision and drop the whole key fail-closed
    # (Invariant 12) rather than silently letting last-write-win pick an arbitrary value.
    prior_by_period: dict[tuple[str, str, tuple], ParsedFact] = {}
    collided_periods: set[tuple[str, str, tuple]] = set()
    for f in prior_facts:
        pkey = (f.concept, f.unit, f.period_key)
        existing = prior_by_period.get(pkey)
        if existing is not None and existing.value != f.value:
            collided_periods.add(pkey)  # ambiguous prior -> poison this key
            continue
        prior_by_period.setdefault(pkey, f)
    # BUG-087: the same collision can live on the CURRENT side — an amended companyfacts
    # payload listing one concept+period twice with different values (as-reported vs
    # as-restated). Without the mirror check, BOTH rows paired against the single prior
    # value and minted two contradictory is_restatement deltas. Same fail-closed rule,
    # both directions: an ambiguous side poisons the key.
    cur_by_period: dict[tuple[str, str, tuple], ParsedFact] = {}
    cur_collided: set[tuple[str, str, tuple]] = set()
    for f in current_facts:
        pkey = (f.concept, f.unit, f.period_key)
        existing = cur_by_period.get(pkey)
        if existing is not None and existing.value != f.value:
            cur_collided.add(pkey)
            continue
        cur_by_period.setdefault(pkey, f)
    for pkey, f_cur in cur_by_period.items():
        if pkey in collided_periods or pkey in cur_collided:
            continue  # duplicate-period collision on either side -> no restatement
        f_prior = prior_by_period.get(pkey)
        if f_prior is not None:
            emit(f_cur, f_prior, restatement=True)

    # 2) Period-over-period: headline fact vs headline fact.
    for key, f_cur in cur_primary.items():
        f_prior = prior_primary.get(key)
        if f_prior is not None:
            emit(f_cur, f_prior, restatement=False)

    return deltas
