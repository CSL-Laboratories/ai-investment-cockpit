"""RW-10 — the in-process EDGAR pipeline: SEC submissions -> filings -> cited deltas.

COCKPIT-AUTHORED ORCHESTRATION over the vendored deterministic core in ``app/filings/``.
It lives HERE, not there, and that boundary is deliberate: **everything under
``app/filings/`` is vendored and provenance-checked against an upstream pin** (see
``app/filings/PROVENANCE.md``), so a cockpit-authored module inside that tree would either
fail the provenance test or force an exception that erodes the rule. Orchestration is ours;
the algorithms are upstream's. Keeping the seam at a directory boundary makes that
statement checkable rather than aspirational.

WHAT THIS REPLACES. Upstream ``edgar-morning-brew`` performs this sequence in its WP8
``scheduler/morning_run.py``, wrapped in a run lock, artifact writer, LLM brief composer and
approve-before-send queue. RW-10 took none of those: the cockpit already has a scheduler, an
operator surface, an approval path and a hash-chained audit, and a second brief composer on
the same evidence was the duplicate inference the absorption existed to remove. What is left
is the deterministic half — and it is the half that produces the rows the cockpit reads.

NO MODEL CALL HAPPENS HERE OR ANYWHERE BELOW IT. The output is evidence: what changed in a
filing, anchored to a byte range of the filing it came from. Interpretation is the morning
brief's job, downstream, behind the human gate.

FAIL-CLOSED POSTURE, PER FEED. A missing ``sec_user_agent`` makes ``build_sec_fetcher``
return None and the feed report itself unprovisioned. It does NOT raise, because an
unprovisioned EDGAR feed must not take down the order path, the risk engine and the operator
surface with it — the same rule every other cockpit feed follows. SEC fair access is
unaffected: the vendored client refuses a blank UA before building a request and refuses any
rate at or above the 10 req/s ceiling, so the control sits at the request, not at boot.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from core.clock import Clock, default_clock

log = logging.getLogger(__name__)

# 10-K/10-Q with no prior filing to diff against is a BASELINE, not a delta batch. Upstream
# raises UnpairedPeriodicFiling rather than silently emitting an 8-K-style extraction for a
# periodic form; we count it and move on, because the first time a ticker is ever polled that
# is the CORRECT and expected outcome for every periodic filing it has.
_BASELINE = "baseline"


@dataclass
class EdgarPipelineResult:
    """What one pipeline run did. Every non-emitting outcome is COUNTED, never silent.

    A feed that reports nothing is indistinguishable from a feed that is broken, which is the
    property the sibling project's counters exist to preserve and the reason a "quiet day"
    must be a positive statement rather than an absence of one.
    """

    provisioned: bool = True
    ciks_polled: int = 0
    filings_ingested: int = 0
    deltas_persisted: int = 0
    baselines: int = 0                       # periodic filing with no prior — expected, not a fault
    poll_errors: list[tuple[str, str]] = field(default_factory=list)
    delta_errors: list[tuple[str, str]] = field(default_factory=list)
    unprovisioned_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.provisioned and not self.poll_errors and not self.delta_errors

    @property
    def quiet(self) -> bool:
        """A GENUINE quiet day: the pipeline ran, reached SEC, and there was nothing new."""
        return self.ok and self.filings_ingested == 0 and self.ciks_polled > 0

    def summary(self) -> str:
        if not self.provisioned:
            return f"edgar: NOT PROVISIONED ({self.unprovisioned_reason})"
        return (
            f"edgar: {self.ciks_polled} cik(s), {self.filings_ingested} filing(s), "
            f"{self.deltas_persisted} delta(s), {self.baselines} baseline(s), "
            f"{len(self.poll_errors)} poll error(s), {len(self.delta_errors)} delta error(s)"
        )


def build_sec_fetcher(settings, *, transport: Any = None):
    """Build the vendored ``SecFetcher``, or return None when EDGAR is unprovisioned.

    Returns None — never raises — when no User-Agent is declared, so the caller reports one
    dead feed instead of a dead process. ``transport`` is injected by tests (the fixture
    replay) so no test can reach sec.gov; production leaves it None.
    """
    from filings.edgar.fetch import SecFetcher

    ua = (getattr(settings, "sec_user_agent", None) or "").strip()
    if not ua:
        return None
    return SecFetcher.from_settings(settings, transport=transport)


def build_raw_store(settings, *, root: Any = None):
    """The write-once store for filing bytes (Inv 8: raw kept separate from derived).

    Defaults under the cockpit's data dir. Filing bytes are what every citation resolves
    against, so this store is the difference between a delta you can verify and a claim you
    have to trust.
    """
    from pathlib import Path

    from filings.raw_store import RawStore

    if root is None:
        root = Path(getattr(settings, "parquet_root", None) or "data") / "raw" / "sec"
    return RawStore(Path(root))


def _prior_same_form(session_factory, filing) -> Any:
    """The most recent EARLIER filing of the same form for the same CIK, or None.

    Ordered by ``filing_date`` then ``accession_no`` so two filings on one day resolve
    deterministically (Inv 2) rather than by whatever order the DB happens to return.
    """
    from sqlalchemy import and_, or_, select

    from schemas.models.filing import Filing

    with session_factory() as s:
        # BUG-083: "prior" means strictly earlier in (filing_date, accession_no) order. SEC
        # accession sequences are assigned in submission-receipt order, so for a same-day
        # same-form pair the old `date <= current` filter with an accession DESC sort
        # resolved the EARLIER filing's prior to the LATER-submitted one — a delta diffed
        # against a chronologically-later baseline.
        return s.execute(
            select(Filing)
            .where(
                Filing.cik == filing.cik,
                Filing.form_type == filing.form_type,
                or_(
                    Filing.filing_date < filing.filing_date,
                    and_(Filing.filing_date == filing.filing_date,
                         Filing.accession_no < filing.accession_no),
                ),
            )
            .order_by(Filing.filing_date.desc(), Filing.accession_no.desc())
            .limit(1)
        ).scalars().first()


def _persist_deltas(session_factory, deltas) -> int:
    """Persist Delta rows, skipping any ``delta_id`` already stored.

    IDEMPOTENT BY CONSTRUCTION. ``delta_id`` is content-derived upstream, so re-running the
    pipeline over the same filings re-derives the same ids and inserts nothing — which is
    what makes a re-run after a crash safe, and what stops a scheduler retry from doubling
    the evidence the morning brief then reads.
    """
    from sqlalchemy import select

    from schemas.models.delta import Delta

    if not deltas:
        return 0
    written = 0
    with session_factory() as s, s.begin():
        existing = set(
            s.execute(
                select(Delta.delta_id).where(
                    Delta.delta_id.in_([d.delta_id for d in deltas])
                )
            ).scalars().all()
        )
        for d in deltas:
            if d.delta_id in existing:
                continue
            s.add(d)
            existing.add(d.delta_id)         # guard against duplicates WITHIN one batch too
            written += 1
    return written


def _as_date(value: Any):
    """Coerce an SEC ISO date string to ``datetime.date``. None/unparseable -> None.

    LOAD-BEARING, AND NOT OBVIOUS. Building an ORM object does NOT apply the column's type:
    a ``Mapped[date]`` assigned a string keeps the string until a flush, and these rows are
    deliberately never flushed (see ``_to_xbrl_facts``). So the raw JSON strings sailed
    straight into the diff, where ``parse/xbrl_facts._primary_sort_key`` called
    ``f.filed.toordinal()`` and raised ``AttributeError: 'str' object has no attribute
    'toordinal'``. The pipeline caught that as a per-filing delta error — meaning the 10-Q
    silently lost every NUMERIC delta while its text deltas landed normally and the run
    still looked healthy. Only a fixture run over a real 10-Q pair surfaced it.
    """
    from datetime import date as _date

    if value is None or isinstance(value, _date):
        return value
    try:
        return _date.fromisoformat(str(value))
    except ValueError:
        return None


def _to_xbrl_facts(records: Iterable[Any], cik: str) -> list:
    """Adapt ``companyfacts.XbrlFactRecord`` values to WP1a ``XbrlFact`` rows.

    NOT AN OPTIONAL NICETY — the two shapes genuinely differ and the mismatch is silent until
    it is not. ``XbrlFactRecord`` carries ``val`` / ``start`` / ``end`` / ``accession`` and no
    ``cik``; ``delta.engine._xbrlfact_to_parsedfact`` reads ``value`` / ``period_start`` /
    ``period_end`` / ``accession_no`` / ``cik``. Passing the record straight through raises
    ``AttributeError`` mid-diff, which the pipeline then counts as a per-filing delta error —
    i.e. the 10-Q quietly loses its NUMERIC deltas while its text deltas still land, and the
    run still looks broadly healthy. Found exactly that way.

    The ``cik`` comes from the poll, not the record, because companyfacts is fetched per
    issuer and its rows do not repeat the issuer they belong to.

    Rows are built but NOT persisted: ``compute_deltas`` only reads these attributes, and
    writing an ``xbrl_fact`` table this pipeline never reads back would be storage without a
    consumer. Persisting them is a separate decision, made when something needs them.
    """
    from decimal import Decimal

    from schemas.models.xbrlfact import XbrlFact

    rows = []
    for r in records:
        try:
            period_end = _as_date(r.end)
            if period_end is None:
                continue                      # period_end is the join key; a fact without one
                                              # cannot be period-matched against a prior filing
            rows.append(XbrlFact(
                cik=str(cik), accession_no=str(r.accession), taxonomy=r.taxonomy,
                concept=r.concept, unit=r.unit, value=Decimal(str(r.val)),
                period_start=_as_date(r.start), period_end=period_end,
                fiscal_year=r.fiscal_year, fiscal_period=r.fiscal_period,
                form=r.form, filed=_as_date(r.filed), frame=r.frame,
                raw_ref=str(r.accession),
            ))
        except Exception:                     # noqa: BLE001 - one unusable fact, not the set
            continue
    # Deterministic order (Inv 2): the numeric diff must not depend on dict iteration order.
    rows.sort(key=lambda r: (r.taxonomy, r.concept, r.unit,
                             str(r.period_start), str(r.period_end)))
    return rows


def _facts_by_accession(fetcher, cik: str, accessions: set[str]) -> dict[str, list]:
    """XBRL facts for the accessions we care about, fetched ONCE per CIK.

    companyfacts is a single large document per issuer; fetching it per FILING would multiply
    SEC requests by the number of filings in a poll for no new information — the fair-access
    budget is a shared resource, not a per-call allowance. A failure here is NOT fatal: the
    numeric delta is an enrichment of the text delta, so we log and continue with empty facts
    rather than losing the textual evidence too.
    """
    from filings.edgar.companyfacts import facts_for_accession, fetch_companyfacts

    if not accessions:
        return {}
    try:
        doc = fetch_companyfacts(fetcher, cik)
    except Exception as exc:                  # noqa: BLE001 - enrichment, never fatal
        log.warning("companyfacts unavailable for CIK %s (%s); text deltas only",
                    cik, type(exc).__name__)
        return {}
    return {acc: _to_xbrl_facts(facts_for_accession(doc, acc), cik) for acc in accessions}


def _poll_isolated(poller, cik_list: list[str], result: EdgarPipelineResult):
    """Poll each CIK, isolating a PER-CIK fault but honouring a FAIR-ACCESS HALT.

    THE DISTINCTION IS LOAD-BEARING, AND A BLANKET ``except`` WOULD DESTROY IT.
    ``SubmissionsPoller.poll_all`` deliberately lets an exception stop the whole fan-out, and
    its docstring says why: "a fair-access halt stops the whole fan-out (fail-closed)". When
    SEC answers 403/429, or we blow the run byte budget, continuing to poll the remaining
    issuers is precisely the behaviour fair access forbids — so those exceptions must still
    stop everything. Catching them here to "be robust" would convert an upstream safety
    control into a politeness violation committed at speed.

    Everything else is per-issuer: one CIK exceeding ``sec_max_new_filings_per_poll``, or one
    malformed submissions document, must not cost the OTHER issuers their evidence. Those are
    counted into ``poll_errors`` — which keeps ``result.quiet`` False, so a run that failed
    can never be mistaken for a day on which nothing was filed.

    Returns ``(polled, halted)``.
    """
    from filings.edgar.fetch import SecFairAccessHalt, SecResourceLimitHalt
    from filings.edgar.submissions_poll import pad_cik

    polled: dict[str, Any] = {}
    for cik in cik_list:
        try:
            polled[pad_cik(cik)] = poller.poll(cik)
        except (SecFairAccessHalt, SecResourceLimitHalt) as exc:
            # STOP. Not an error to count and continue past — a directive to back off.
            result.poll_errors.append((cik, type(exc).__name__))
            return polled, True
        except Exception as exc:              # noqa: BLE001 - isolate ONE issuer's fault
            result.poll_errors.append((cik, type(exc).__name__))
            log.warning("polling CIK %s failed (%s); other issuers unaffected",
                        cik, type(exc).__name__)
    return polled, False


def run_edgar_pipeline(
    session_factory,
    *,
    settings,
    ciks: Iterable[str],
    fetcher: Any = None,
    raw_store: Any = None,
    audit_log: Any = None,
    clock: Clock = default_clock,
    transport: Any = None,
) -> EdgarPipelineResult:
    """Poll SEC for ``ciks``, ingest new filings, and persist their cited deltas.

    Returns an :class:`EdgarPipelineResult`; raises only on a programming error, never on an
    unprovisioned feed or a single bad filing. One filing that fails to diff must not cost
    the other filings in the same poll their evidence — the error is counted and named.
    """
    from filings.delta import UnpairedPeriodicFiling, compute_deltas
    from filings.edgar.submissions_poll import SubmissionsPoller

    result = EdgarPipelineResult()
    fetcher = fetcher if fetcher is not None else build_sec_fetcher(settings, transport=transport)
    if fetcher is None:
        result.provisioned = False
        result.unprovisioned_reason = "set COCKPIT_SEC_USER_AGENT (SEC needs no API key)"
        log.info("EDGAR feed NOT PROVISIONED: %s", result.unprovisioned_reason)
        return result

    raw_store = raw_store if raw_store is not None else build_raw_store(settings)
    cik_list = [c for c in ciks]
    if not cik_list:
        log.info("EDGAR pipeline: empty CIK list — nothing to poll")
        return result

    poller = SubmissionsPoller(
        fetcher, raw_store, session_factory, clock=clock, audit_log=audit_log,
        baseline_on_first_poll=audit_log is not None,
        max_new_filings_per_poll=getattr(settings, "sec_max_new_filings_per_poll", 25),
    )
    # BUG-053: first contact with a CIK records the current feed position (an AUDITED
    # PollBaseline) instead of treating the issuer's whole recent window as new work —
    # which, for any established company, exceeds sec_max_new_filings_per_poll and made
    # the first poll raise PollFilingLimitExceeded on every run, forever. The baseline
    # store refuses unaudited writes, so a run without an audit_log keeps the VISIBLE
    # cap fault rather than silently writing an unaudited boundary.
    if audit_log is not None:
        from filings.edgar.fetch import SecFairAccessHalt, SecResourceLimitHalt
        from filings.edgar.submissions_poll import pad_cik

        for cik in cik_list:
            try:
                poller.baseline(cik)
            except (SecFairAccessHalt, SecResourceLimitHalt):
                # Fair access wins over first contact, same as the poll fan-out.
                log.warning("SEC fair-access halt during first-contact baselining: "
                            "run stopped before polling")
                return result
            except Exception as exc:  # noqa: BLE001 — per-issuer isolation, as in _poll_isolated
                result.poll_errors.append((pad_cik(cik), f"baseline: {exc!r}"))
    else:
        log.warning("EDGAR: no audit_log wired — first-contact CIKs cannot be baselined; "
                    "an established issuer's first poll will trip the filing cap")
    polled, halted = _poll_isolated(poller, cik_list, result)
    result.ciks_polled = len(polled)
    if halted:
        # A fair-access halt already stopped the fan-out. Deltas for whatever WAS ingested
        # before the halt are still computed below — that evidence is real and paid for.
        log.warning("SEC fair-access halt: remaining CIKs were not polled this run")

    for cik, poll in polled.items():
        result.filings_ingested += poll.emitted
        for accession, reason in poll.errors:
            result.poll_errors.append((f"{cik}:{accession}", reason))
        if not poll.filings:
            continue
        facts = _facts_by_accession(
            fetcher, cik,
            {f.accession_no for f in poll.filings} | {
                p.accession_no for p in (
                    _prior_same_form(session_factory, f) for f in poll.filings
                ) if p is not None
            },
        )
        for filing in poll.filings:
            prior = _prior_same_form(session_factory, filing)
            try:
                deltas = compute_deltas(
                    filing, prior, raw_store,
                    facts.get(filing.accession_no, []),
                    facts.get(prior.accession_no, []) if prior is not None else [],
                    clock=clock,
                )
            except UnpairedPeriodicFiling:
                # EXPECTED, not an error: the first 10-K/10-Q ever seen for an issuer has
                # nothing to diff against. It is stored as evidence and becomes the baseline
                # the NEXT one is measured against.
                result.baselines += 1
                continue
            except Exception as exc:          # noqa: BLE001 - isolate one filing's failure
                result.delta_errors.append((filing.accession_no, type(exc).__name__))
                log.warning("delta computation failed for %s (%s); other filings unaffected",
                            filing.accession_no, type(exc).__name__)
                continue
            result.deltas_persisted += _persist_deltas(session_factory, deltas)

    log.info("%s", result.summary())
    return result
