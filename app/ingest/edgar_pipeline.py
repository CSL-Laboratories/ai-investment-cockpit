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
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from core.clock import Clock, default_clock

log = logging.getLogger(__name__)

# 10-K/10-Q with no prior filing to diff against is a BASELINE, not a delta batch. Upstream
# raises UnpairedPeriodicFiling rather than silently emitting an 8-K-style extraction for a
# periodic form; we count it and move on, because the first time a ticker is ever polled that
# is the CORRECT and expected outcome for every periodic filing it has.
_BASELINE = "baseline"
_SUBMISSIONS_ARCHIVE_NAME = re.compile(r"^CIK\d{10}-submissions-\d{3}\.json$")
_SEC_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_MAX_HISTORICAL_SUBMISSION_FILES = 20
_BASELINE_EVENTS = frozenset({
    "poll-baseline-established",
    "poll-baseline-advanced",
    "poll-baseline-touched",
})
_LEGACY_BASELINE_CLOCK_SKEW_SECONDS = 1.0


class EdgarInProcessStatus(str, Enum):
    """Operational states for the scheduled in-process EDGAR adapter."""

    NOT_RUN = "not-run"
    CURRENT = "current"
    NOTHING_MATERIAL = "nothing-material"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class EdgarInProcessOutcome:
    """Duck-typed by ``IngestRunner`` into the authenticated status response."""

    status: EdgarInProcessStatus
    reason: str
    quiet_day: bool = False

    @property
    def trustworthy(self) -> bool:
        return self.status in {
            EdgarInProcessStatus.CURRENT,
            EdgarInProcessStatus.NOTHING_MATERIAL,
        }


class EdgarPipelineRunError(RuntimeError):
    """A scheduled in-process run could not establish trustworthy current evidence."""


def _as_utc(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_legacy_baseline_clock_skew(row_value, event_value) -> bool:
    """Recognize only BUG-106's small, forward-moving double clock sample."""
    skew = (_as_utc(event_value) - _as_utc(row_value)).total_seconds()
    return 0 < skew <= _LEGACY_BASELINE_CLOCK_SKEW_SECONDS


def _verified_poll_baseline_in(session, audit_log, cik: str):
    """Return a baseline only when its complete mutable history is audit-consistent."""
    from sqlalchemy import select

    from filings.edgar.submissions_poll import pad_cik
    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline

    verified = audit_log.verify_session(session)
    if not verified.ok:
        raise EdgarPipelineRunError(
            f"audit chain invalid before EDGAR baseline use: {verified.error}"
        )
    cik10 = pad_cik(cik)
    row = session.execute(
        select(PollBaseline).where(PollBaseline.cik == cik10)
    ).scalar_one_or_none()
    if row is None:
        return None
    events = session.execute(
        select(AuditEntry)
        .where(AuditEntry.event_type.in_(_BASELINE_EVENTS))
        .order_by(AuditEntry.seq)
    ).scalars().all()
    transitions = [event for event in events if (event.refs or {}).get("row_id") == row.id]
    cutoff = None
    established = None
    for event in transitions:
        refs = event.refs or {}
        payload = event.payload or {}
        if (
            refs.get("model") != PollBaseline.__tablename__
            or refs.get("cik") != cik10
        ):
            raise EdgarPipelineRunError("EDGAR baseline audit identity is inconsistent")
        if event.event_type == "poll-baseline-established":
            if established is not None:
                raise EdgarPipelineRunError("EDGAR baseline has multiple establishment events")
            established = event
            cutoff = payload.get("cutoff_accession_no")
        elif established is None:
            raise EdgarPipelineRunError("EDGAR baseline transition precedes establishment")
        elif event.event_type == "poll-baseline-advanced":
            if payload.get("previous_cutoff_accession_no") != cutoff:
                raise EdgarPipelineRunError("EDGAR baseline transition continuity failed")
            cutoff = payload.get("cutoff_accession_no")
        elif payload.get("cutoff_accession_no") != cutoff:
            raise EdgarPipelineRunError("EDGAR baseline touch does not match signed state")
    latest = transitions[-1] if transitions else None
    if established is None or latest is None or cutoff != row.cutoff_accession_no:
        raise EdgarPipelineRunError("mutable EDGAR baseline diverges from signed history")
    already_reconciled = any(
        (event.payload or {}).get("timestamp_reconciliation") == "BUG-106"
        for event in transitions
    )
    try:
        established_matches = (
            _as_utc(established.created_at) == _as_utc(row.established_at)
        )
        updated_matches = _as_utc(latest.created_at) == _as_utc(row.updated_at)
        repairable = not already_reconciled and (
            (established_matches or _is_legacy_baseline_clock_skew(
                row.established_at, established.created_at
            ))
            and (updated_matches or _is_legacy_baseline_clock_skew(
                row.updated_at, latest.created_at
            ))
        )
    except (TypeError, ValueError):
        repairable = False
        established_matches = False
        updated_matches = False
    if not established_matches or not updated_matches:
        if not repairable:
            raise EdgarPipelineRunError("mutable EDGAR baseline diverges from signed history")
        # BUG-106 migration: pre-fix rows sampled the poller clock immediately before
        # AuditLog sampled its signed clock. Rebuild only those narrowly bounded,
        # forward-moving timestamp fields from verified authority. The repair is itself a
        # signed touch in the same transaction; cutoff or identity divergence still fails.
        repaired = audit_log.append_in(
            session,
            "poll-baseline-touched",
            actor="edgar-baseline-repair",
            refs={"model": row.__tablename__, "row_id": row.id, "cik": cik10},
            payload={
                "cutoff_accession_no": cutoff,
                "timestamp_reconciliation": "BUG-106",
                "established_at": established.created_at.isoformat(),
            },
        )
        row.established_at = established.created_at
        row.updated_at = repaired.created_at
        session.flush()
    return row


class _VerifiedPollBaselineStore:
    """Adapter-owned baseline store that refuses keyless row substitution before use."""

    def __init__(self, session_factory, audit_log):
        self._sf = session_factory
        self._audit = audit_log
        self._expected: dict[str, str | None] = {}
        self._lock = threading.RLock()

    @property
    def is_audited(self) -> bool:
        return True

    @staticmethod
    def _refs(row) -> dict[str, str]:
        return {"model": row.__tablename__, "row_id": row.id, "cik": row.cik}

    def load(self, cik: str):
        from filings.edgar.submissions_poll import pad_cik

        cik10 = pad_cik(cik)
        with self._lock, self._sf() as session, session.begin():
            row = _verified_poll_baseline_in(session, self._audit, cik10)
            if row is not None:
                self._expected[cik10] = row.cutoff_accession_no
                session.expunge(row)
            return row

    def establish(self, cik: str, cutoff_accession_no: str | None, now: datetime) -> bool:
        from filings.edgar.submissions_poll import pad_cik
        from schemas.models.pollbaseline import PollBaseline
        from core.ids import new_id

        cik10 = pad_cik(cik)
        with self._lock, self._sf() as session, session.begin():
            existing = _verified_poll_baseline_in(session, self._audit, cik10)
            if existing is not None:
                self._expected[cik10] = existing.cutoff_accession_no
                return False
            row = PollBaseline(
                id=new_id("baseline"), cik=cik10,
                cutoff_accession_no=cutoff_accession_no,
                established_at=now, updated_at=now,
            )
            session.add(row)
            session.flush()
            event = self._audit.append_in(
                session, "poll-baseline-established", actor="submissions-poller",
                refs=self._refs(row),
                payload={"cutoff_accession_no": cutoff_accession_no},
            )
            # BUG-106: AuditLog owns the signed timestamp. A real clock advances between
            # the poller's ``now`` sample and append_in's sample, so persisting ``now`` made
            # every production baseline differ from its signed history by milliseconds.
            # Keep the provisional values for the non-null insert, then bind the domain row
            # to the returned event inside this same transaction. Do not relax verification.
            row.established_at = event.created_at
            row.updated_at = event.created_at
            self._expected[cik10] = cutoff_accession_no
            return True

    def _mutate(self, cik: str, cutoff_accession_no: str | None, now: datetime) -> None:
        from filings.edgar.submissions_poll import pad_cik

        cik10 = pad_cik(cik)
        with self._lock, self._sf() as session, session.begin():
            row = _verified_poll_baseline_in(session, self._audit, cik10)
            if row is None:
                raise EdgarPipelineRunError(f"no verified baseline exists for CIK {cik10}")
            expected = self._expected.get(cik10)
            if row.cutoff_accession_no != expected:
                raise EdgarPipelineRunError("EDGAR baseline changed during the active poll")
            previous = row.cutoff_accession_no
            row.cutoff_accession_no = cutoff_accession_no
            row.updated_at = now
            session.flush()
            if cutoff_accession_no == previous:
                event_type = "poll-baseline-touched"
                payload = {"cutoff_accession_no": cutoff_accession_no}
            else:
                event_type = "poll-baseline-advanced"
                payload = {
                    "previous_cutoff_accession_no": previous,
                    "cutoff_accession_no": cutoff_accession_no,
                }
            event = self._audit.append_in(
                session, event_type, actor="submissions-poller",
                refs=self._refs(row), payload=payload,
            )
            row.updated_at = event.created_at
            self._expected[cik10] = cutoff_accession_no

    def advance(self, cik: str, cutoff_accession_no: str, now: datetime) -> None:
        self._mutate(cik, cutoff_accession_no, now)

    def touch(self, cik: str, now: datetime) -> None:
        from filings.edgar.submissions_poll import pad_cik

        cik10 = pad_cik(cik)
        if cik10 not in self._expected:
            self.load(cik10)
        self._mutate(cik10, self._expected.get(cik10), now)


class _CompleteSubmissionsFetcher:
    """Reject any post-boundary submissions row the deterministic poller would skip."""

    def __init__(self, delegate, baseline_store, *, strict_ciks=()):
        self._delegate = delegate
        self._baselines = baseline_store
        self._strict_ciks = frozenset(str(cik) for cik in strict_ciks)

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def get_bytes(self, url):
        return self._delegate.get_bytes(url)

    def get_json(self, url):
        from filings.edgar.fetch import pad_cik
        from filings.edgar.submissions_poll import PROCESS_FORMS

        document = self._delegate.get_json(url)
        recent = (document.get("filings") or {}).get("recent") if isinstance(document, dict) else None
        if not isinstance(recent, dict):
            return document
        requested = re.search(r"/submissions/CIK(\d{10})\.json(?:\?.*)?$", str(url))
        if requested is None:
            raise EdgarPipelineRunError("SEC submissions response has no requested CIK identity")
        cik = requested.group(1)
        try:
            response_cik = pad_cik(document.get("cik", ""))
        except (TypeError, ValueError) as exc:
            raise EdgarPipelineRunError("SEC submissions response CIK is malformed") from exc
        if response_cik != cik:
            raise EdgarPipelineRunError("SEC submissions response CIK mismatches its request")
        baseline = self._baselines.load(cik)
        if cik not in self._strict_ciks or baseline is None:
            return document
        accessions = recent.get("accessionNumber")
        count = len(accessions) if isinstance(accessions, list) else -1
        if (
            count < 0
            or not recent
            or any(not isinstance(values, list) or len(values) != count
                   for values in recent.values())
        ):
            raise EdgarPipelineRunError(
                "malformed SEC submissions arrays block strategy freshness"
            )
        forms = recent.get("form")
        dates = recent.get("filingDate")
        documents = recent.get("primaryDocument")
        accepted = recent.get("acceptanceDateTime")
        if not all(isinstance(values, list) for values in (forms, dates, documents, accepted)):
            raise EdgarPipelineRunError(
                "SEC submissions response lacks required strategy chronology"
            )
        normalized_accessions = [str(value).strip() for value in accessions]
        if (
            any(_SEC_ACCESSION.fullmatch(value) is None for value in normalized_accessions)
            or len(set(normalized_accessions)) != count
        ):
            raise EdgarPipelineRunError(
                "SEC submissions accessions are invalid or ambiguous"
            )
        try:
            parsed_dates = [date.fromisoformat(str(value)) for value in dates]
            parsed_acceptance = [_as_utc(value) for value in accepted]
        except (TypeError, ValueError) as exc:
            raise EdgarPipelineRunError(
                "SEC submissions chronology is malformed"
            ) from exc
        if any(
            parsed_dates[index] > parsed_dates[index - 1]
            or parsed_acceptance[index] > parsed_acceptance[index - 1]
            or parsed_acceptance[index].date() != parsed_dates[index]
            for index in range(1, count)
        ) or (count and parsed_acceptance[0].date() != parsed_dates[0]):
            raise EdgarPipelineRunError(
                "SEC submissions rows are not in unambiguous newest-first order"
            )
        if baseline.cutoff_accession_no is None:
            boundary = count
        else:
            matches = [
                index for index, value in enumerate(normalized_accessions)
                if value == baseline.cutoff_accession_no
            ]
            if not matches:
                return document  # vendored poller raises its boundary-missing fault
            if len(matches) != 1:
                raise EdgarPipelineRunError(
                    "saved SEC submissions boundary is ambiguous"
                )
            boundary = matches[0]
        for index in range(boundary):
            accession = normalized_accessions[index]
            form = str(forms[index]).strip()
            primary = str(documents[index]).strip()
            if not accession or not primary or form not in PROCESS_FORMS:
                raise EdgarPipelineRunError(
                    f"unsupported post-boundary SEC filing {accession or '<missing>'} "
                    "blocks freshness"
                )
        return document


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
    return RawStore(
        Path(root), min_free_bytes=getattr(settings, "raw_min_free_bytes", 256 * 1024 * 1024)
    )


def _sync_watchlist_companies(
    session_factory, mapping: dict[str, str], *, clock, audit_log=None
) -> None:
    """Persist the exact ticker↔CIK attribution consumed by ``EdgarBrewPoller``.

    The hardened event adapter joins every Delta through ``Company.cik``. Silently omitting
    this mapping would turn valid filings into counted ``no_ticker`` drops; guessing through a
    conflict would be worse because ``filing`` is a hard event. Conflicts therefore fail closed.
    """
    from sqlalchemy import or_, select

    from core.ids import new_id
    from schemas.models.company import Company

    if not mapping:
        raise ValueError("EDGAR watchlist resolved to no ticker/CIK mappings")
    with session_factory() as session, session.begin():
        rows = session.execute(
            select(Company).where(
                or_(Company.ticker.in_(mapping), Company.cik.in_(mapping.values()))
            )
        ).scalars().all()
        by_ticker = {row.ticker: row for row in rows}
        by_cik = {row.cik: row for row in rows}
        for ticker, cik in sorted(mapping.items()):
            ticker_row = by_ticker.get(ticker)
            cik_row = by_cik.get(cik)
            action = None
            if ticker_row is not None and ticker_row.cik != cik:
                raise ValueError(
                    f"EDGAR attribution conflict: ticker {ticker!r} is already bound to "
                    f"CIK {ticker_row.cik!r}, not {cik!r}"
                )
            if cik_row is not None and cik_row.ticker != ticker:
                raise ValueError(
                    f"EDGAR attribution conflict: CIK {cik!r} is already bound to "
                    f"ticker {cik_row.ticker!r}, not {ticker!r}"
                )
            if ticker_row is None and cik_row is None:
                row = Company(
                    id=new_id("company"), ticker=ticker, cik=cik, name=None,
                    active=True, created_at=clock.now(),
                )
                session.add(row)
                by_ticker[ticker] = row
                by_cik[cik] = row
                action = "created"
            else:
                row = ticker_row or cik_row
                if not row.active:
                    row.active = True
                    action = "reactivated"
            if action is not None:
                session.flush()
                append_in = getattr(audit_log, "append_in", None)
                if callable(append_in):
                    append_in(
                        session,
                        "edgar-company-attribution",
                        actor="edgar-pipeline",
                        refs={"row_id": row.id, "cik": cik},
                        payload={"action": action, "ticker": ticker, "cik": cik},
                    )


class InProcessEdgarFetch:
    """Scheduled fetch adapter: SEC pipeline write, then hardened Delta-to-event read.

    A fresh fetcher is built for every tick so its byte budget remains a *per-run* budget.
    The downstream reader retains the existing cursor/dedup/escaping/citation behavior while
    RW-11 gathers the live evidence required before RW-12 may delete that legacy adapter.
    """

    edgar_mode = "in_process"

    def __init__(
        self,
        *,
        session_factory,
        settings,
        tickers: Iterable[str],
        strict_tickers: Iterable[str],
        db_path,
        raw_store_root,
        audit_log,
        clock,
        process_epoch,
        fetcher_factory,
    ) -> None:
        from ingest.edgar_brew import make_brew_fetch

        self._session_factory = session_factory
        self._settings = settings
        self._tickers = tuple(sorted(set(tickers)))
        self._strict_tickers = frozenset(
            str(ticker).strip().upper() for ticker in strict_tickers
        )
        self._raw_store_root = Path(raw_store_root)
        self._audit_log = audit_log
        self._clock = clock
        self._process_epoch = str(process_epoch or "")
        self._fetcher_factory = fetcher_factory
        # company_tickers.json is a full-market document. Resolve the static operator
        # watchlist at most once per UTC/clock day instead of re-downloading it every
        # five-minute scheduler tick. A failed lookup is deliberately not cached.
        self._cik_mapping: dict[str, str] | None = None
        self._cik_mapping_day = None
        self.db_path = str(db_path)
        self._reader = make_brew_fetch(db_path)
        self._outcome = EdgarInProcessOutcome(
            EdgarInProcessStatus.NOT_RUN,
            "in-process EDGAR pipeline has not completed a scheduled poll",
        )

    def __call__(self, cursor, monotonic_now):
        from filings.edgar.cik_map import fetch_cik_map

        try:
            fetcher = self._fetcher_factory()
            if fetcher is None:
                raise EdgarPipelineRunError("SEC fetcher factory returned no fetcher")
            mapping_day = self._clock.now().date()
            if self._cik_mapping is None or self._cik_mapping_day != mapping_day:
                mapping = fetch_cik_map(fetcher, self._tickers, strict=True)
                self._cik_mapping = dict(mapping)
                self._cik_mapping_day = mapping_day
            else:
                mapping = self._cik_mapping
            strict_ciks = {
                cik for ticker, cik in mapping.items()
                if str(ticker).strip().upper() in self._strict_tickers
            }
            _sync_watchlist_companies(
                self._session_factory, mapping, clock=self._clock,
                audit_log=self._audit_log,
            )
            result = run_edgar_pipeline(
                self._session_factory,
                settings=self._settings,
                ciks=sorted(set(mapping.values())),
                fetcher=fetcher,
                raw_store=build_raw_store(self._settings, root=self._raw_store_root),
                audit_log=self._audit_log,
                clock=self._clock,
                process_epoch=self._process_epoch,
                strict_completeness_ciks=strict_ciks,
            )
            batch = self._reader(cursor, monotonic_now)
            complete = result.ok and result.ciks_polled == len(set(mapping.values()))
            if not complete:
                # Per-CIK isolation must extend through event delivery: a malformed issuer
                # cannot suppress already-persisted evidence from every healthy issuer. The
                # feed remains explicitly DEGRADED even while the hardened reader emits the
                # partial trustworthy batch; the next successful tick re-reads any remainder.
                self._outcome = EdgarInProcessOutcome(
                    EdgarInProcessStatus.DEGRADED, result.summary()
                )
            elif result.quiet and not batch:
                self._outcome = EdgarInProcessOutcome(
                    EdgarInProcessStatus.NOTHING_MATERIAL,
                    "SEC poll completed successfully and found no new material deltas",
                    quiet_day=True,
                )
            else:
                self._outcome = EdgarInProcessOutcome(
                    EdgarInProcessStatus.CURRENT, result.summary()
                )
            return batch
        except Exception as exc:
            self._outcome = EdgarInProcessOutcome(
                EdgarInProcessStatus.DEGRADED,
                f"in-process EDGAR poll failed: {type(exc).__name__}: {exc}",
            )
            raise

    def brew_status(self, _day) -> EdgarInProcessOutcome:
        """Compatibility seam consumed by ``IngestRunner.status`` during RW-11."""
        return self._outcome


def build_in_process_edgar_fetch(
    *, session_factory, settings, tickers, db_path, raw_store_root, audit_log, clock,
    process_epoch="", strict_tickers=(), fetcher_factory=None,
):
    """Build the scheduled adapter, or ``None`` when it has no bounded watchlist scope."""
    ticker_scope = tuple(sorted(set(tickers)))
    if not ticker_scope:
        return None
    if fetcher_factory is None:
        def fetcher_factory():
            return build_sec_fetcher(settings)
    return InProcessEdgarFetch(
        session_factory=session_factory,
        settings=settings,
        tickers=ticker_scope,
        strict_tickers=strict_tickers,
        db_path=db_path,
        raw_store_root=raw_store_root,
        audit_log=audit_log,
        clock=clock,
        process_epoch=process_epoch,
        fetcher_factory=fetcher_factory,
    )


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


def _filing_audit_hook(audit_log, raw_store):
    """Return the adapter-owned atomic W-23 filing/raw binding hook.

    ``filings/`` is pinned vendored source. Its ``on_filing`` seam is the supported extension
    point, so cockpit-specific audit policy belongs here rather than in the vendored poller.
    """
    from orchestrator.state import EVENT_FILING_INGESTED

    def _audit_filing(session, filing):
        audit_log.append_in(
            session,
            EVENT_FILING_INGESTED,
            actor="edgar",
            refs={
                "model": filing.__tablename__,
                "row_id": filing.id,
                "raw_ref": filing.raw_ref,
            },
            payload={
                "cik": filing.cik,
                "accession_no": filing.accession_no,
                "form_type": filing.form_type.value,
                "filing_date": filing.filing_date.isoformat(),
                "detection_ts": filing.detection_ts.isoformat(),
                "raw_content_hash": raw_store.stored_hash(filing.raw_ref),
            },
        )

    return _audit_filing


def backfill_filing_accession(
    session_factory, *, cik: str, accession: str, fetcher, raw_store,
    audit_log, clock: Clock = default_clock, anchor_store=None,
):
    """Re-fetch and atomically sign one exact historical SEC accession.

    Normal polling deliberately baselines a newly watched issuer and skips already-present
    rows.  This bounded operator path is the supported way to establish W-23 provenance for a
    post-announcement target or a legacy unsigned row.  It searches only SEC's main submissions
    document plus a finite number of SEC-declared archive shards, fetches the exact primary
    document through the normal fair-access client, and never contacts a model or broker.
    """
    from sqlalchemy import select

    from filings.edgar.fetch import archive_document_url, pad_cik, submissions_url
    from filings.edgar.submissions_poll import PROCESS_FORMS, iter_recent_filings
    from schemas.models.audit_entry import AuditEntry
    from schemas.models.filing import Filing, FormType

    cik10 = pad_cik(cik)
    accession = str(accession).strip()
    if not accession or len(accession) > 25:
        raise ValueError("historical filing accession is invalid")
    main_url = submissions_url(cik10)
    main = fetcher.get_json(main_url)
    matches = [
        row for row in iter_recent_filings(main, cik10) if row.accession == accession
    ]
    files = (main.get("filings") or {}).get("files") or []
    if not matches and len(files) > _MAX_HISTORICAL_SUBMISSION_FILES:
        raise EdgarPipelineRunError("SEC historical submissions shard limit exceeded")
    base_url = main_url.rsplit("/", 1)[0]
    for item in files if not matches else []:
        name = str((item or {}).get("name") or "") if isinstance(item, dict) else ""
        if _SUBMISSIONS_ARCHIVE_NAME.fullmatch(name) is None:
            raise EdgarPipelineRunError("SEC historical submissions shard name is invalid")
        archived = fetcher.get_json(f"{base_url}/{name}")
        matches.extend(
            row for row in iter_recent_filings(
                {"cik": cik10, "filings": {"recent": archived}}, cik10
            )
            if row.accession == accession
        )
        if matches:
            break
    if len(matches) != 1:
        raise EdgarPipelineRunError(
            f"exact accession {accession!r} was not uniquely present in bounded SEC history"
        )
    source = matches[0]
    if source.form not in PROCESS_FORMS or not source.primary_document:
        raise EdgarPipelineRunError("historical filing is outside the supported form/document scope")
    body = fetcher.get_bytes(
        archive_document_url(cik10, accession, source.primary_document)
    )
    try:
        raw_store.write(accession, body)
    except FileExistsError:
        if raw_store.resolve(accession) != body:
            raise EdgarPipelineRunError(
                "existing historical raw bytes disagree with the SEC response"
            ) from None

    with session_factory() as session, session.begin():
        latest_anchor = anchor_store.latest() if anchor_store is not None else None
        verified = (
            audit_log.verify_session_against_anchor(session, *latest_anchor)
            if latest_anchor is not None
            else audit_log.verify_session(session)
        )
        if not verified.ok:
            raise EdgarPipelineRunError(
                f"audit chain invalid before historical filing import: {verified.error}"
            )
        existing = session.execute(
            select(Filing).where(Filing.accession_no == accession)
        ).scalar_one_or_none()
        if existing is None:
            filing = Filing(
                cik=cik10,
                accession_no=accession,
                form_type=FormType(source.form),
                filing_date=source.filing_date,
                detection_ts=clock.now(),
                raw_ref=accession,
                primary_doc=source.primary_document,
            )
            session.add(filing)
            session.flush()
        else:
            filing = existing
            if (
                filing.cik != cik10
                or filing.form_type.value != source.form
                or filing.filing_date != source.filing_date
                or filing.raw_ref != accession
                or filing.primary_doc != source.primary_document
            ):
                raise EdgarPipelineRunError(
                    "legacy filing row disagrees with the re-fetched SEC metadata"
                )
        signed = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "filing_ingested")
        ).scalars().all()
        matches = [
            event for event in signed
            if (event.refs or {}).get("row_id") == filing.id
            and (event.payload or {}).get("accession_no") == accession
        ]
        if matches:
            expected_hash = raw_store.stored_hash(accession)

            if any(
                (event.refs or {}).get("model") != Filing.__tablename__
                or (event.refs or {}).get("raw_ref") != accession
                or (event.payload or {}).get("cik") != cik10
                or (event.payload or {}).get("form_type") != filing.form_type.value
                or (
                    (event.payload or {}).get("filing_date") is not None
                    and (event.payload or {}).get("filing_date")
                    != filing.filing_date.isoformat()
                )
                or _as_utc((event.payload or {}).get("detection_ts"))
                != _as_utc(filing.detection_ts)
                or (event.payload or {}).get("raw_content_hash") != expected_hash
                for event in matches
            ):
                raise EdgarPipelineRunError("existing signed filing binding is conflicting")
            enriched = [
                event for event in matches
                if (event.payload or {}).get("filing_date") == filing.filing_date.isoformat()
            ]
            if len(enriched) > 1:
                raise EdgarPipelineRunError("historical filing has conflicting enriched bindings")
            if not enriched:
                _filing_audit_hook(audit_log, raw_store)(session, filing)
        else:
            _filing_audit_hook(audit_log, raw_store)(session, filing)
        session.expunge(filing)
        return filing


def _record_poll_completions(
    session_factory, audit_log, clock, ciks: Iterable[str], *, process_epoch: str
) -> None:
    """Append durable per-CIK proof that one SEC poll completed without a hidden gap.

    The mutable poll cursor is operational telemetry, not order authority.  W-23 consumes this
    HMAC-chain event instead, together with the exact filing-event head it covered.  The event
    is written only after submissions polling and deterministic extraction complete; a crash
    before this transaction therefore leaves the strategy unavailable rather than blessing a
    partially processed run.
    """
    from sqlalchemy import select

    from filings.edgar.submissions_poll import pad_cik
    from orchestrator.state import EVENT_EDGAR_POLL_COMPLETED, EVENT_FILING_INGESTED
    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollcursor import PollCursor

    append_in = getattr(audit_log, "append_in", None)
    if not callable(append_in):
        raise EdgarPipelineRunError("EDGAR poll completion requires an audit log")
    normalized = tuple(sorted({pad_cik(cik) for cik in ciks}))
    if not normalized:
        return
    completed_at = clock.now()
    with session_factory() as session, session.begin():
        for cik in normalized:
            baseline = _verified_poll_baseline_in(session, audit_log, cik)
            cursor = session.execute(
                select(PollCursor).where(PollCursor.cik == cik)
            ).scalar_one_or_none()
            filing_events = session.execute(
                select(AuditEntry)
                .where(AuditEntry.event_type == EVENT_FILING_INGESTED)
                .order_by(AuditEntry.seq.desc())
            ).scalars().all()
            filing_head = next(
                (event.seq for event in filing_events
                 if (event.payload or {}).get("cik") == cik),
                0,
            )
            refs = {"cik": cik}
            if baseline is not None:
                refs.update({"model": baseline.__tablename__, "row_id": baseline.id})
            append_in(
                session,
                EVENT_EDGAR_POLL_COMPLETED,
                actor="edgar-pipeline",
                refs=refs,
                payload={
                    "cik": cik,
                    "process_epoch": process_epoch,
                    "completed_at": completed_at.isoformat(),
                    "baseline_accession": (
                        None if baseline is None else baseline.cutoff_accession_no
                    ),
                    "cursor_accession": (
                        None if cursor is None else cursor.last_accession_no
                    ),
                    "filing_event_seq": filing_head,
                },
            )


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


def _extraction_candidates(session_factory, ciks: Iterable[str]) -> list:
    """Filings whose deterministic extraction has never completed or must be retried.

    Existing filings with Delta rows predate this retry ledger and are already complete. A
    filing with no Delta and no ledger row is intentionally retried once: it may be a historic
    transient failure, a legitimate baseline, or a no-material-change success. The latter two
    get a terminal ledger row so subsequent ticks stay cheap.
    """
    from sqlalchemy import and_, exists, or_, select

    from schemas.models.delta import Delta
    from schemas.models.filing import Filing
    from schemas.models.filing_extraction_state import FilingExtractionState

    scope = sorted(set(ciks))
    if not scope:
        return []
    has_delta = exists(
        select(Delta.delta_id).where(Delta.accession_no == Filing.accession_no)
    )
    with session_factory() as session:
        rows = session.execute(
            select(Filing)
            .outerjoin(
                FilingExtractionState,
                FilingExtractionState.accession_no == Filing.accession_no,
            )
            .where(
                Filing.cik.in_(scope),
                or_(
                    FilingExtractionState.status == "error",
                    and_(FilingExtractionState.id.is_(None), ~has_delta),
                ),
            )
            .order_by(Filing.filing_date, Filing.accession_no)
        ).scalars().all()
        for row in rows:
            session.expunge(row)
        return rows


def _record_extraction_state(
    session_factory, filing, *, status: str, error: str | None, clock, audit_log=None
) -> None:
    """Upsert one retry-ledger row and bind it into the audit chain when available."""
    from sqlalchemy import select

    from schemas.models.filing_extraction_state import FilingExtractionState

    with session_factory() as session, session.begin():
        row = session.execute(
            select(FilingExtractionState).where(
                FilingExtractionState.accession_no == filing.accession_no
            )
        ).scalar_one_or_none()
        previous = row.status if row is not None else None
        if row is None:
            row = FilingExtractionState(
                accession_no=filing.accession_no,
                status=status,
                attempts=1,
                last_error=error,
                updated_at=clock.now(),
            )
            session.add(row)
        else:
            row.status = status
            row.attempts += 1
            row.last_error = error
            row.updated_at = clock.now()
        session.flush()
        append_in = getattr(audit_log, "append_in", None)
        if callable(append_in):
            append_in(
                session,
                "filing-extraction-state",
                actor="edgar-pipeline",
                refs={"row_id": row.id, "accession_no": filing.accession_no},
                payload={
                    "previous": previous,
                    "status": status,
                    "attempts": row.attempts,
                    "error": error,
                },
            )


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


def _facts_by_accession(
    fetcher, cik: str, accessions: set[str]
) -> tuple[dict[str, list], str | None]:
    """XBRL facts for the accessions we care about, fetched ONCE per CIK.

    companyfacts is a single large document per issuer; fetching it per FILING would multiply
    SEC requests by the number of filings in a poll for no new information — the fair-access
    budget is a shared resource, not a per-call allowance. A transient failure is returned
    alongside an empty mapping: paired filings are held atomically until retry so an early
    text-only event cannot suppress the later numeric enrichment through accession dedup.
    """
    from filings.edgar.companyfacts import facts_for_accession, fetch_companyfacts
    from filings.edgar.fetch import SecFairAccessHalt, SecResourceLimitHalt

    if not accessions:
        return {}, None
    try:
        doc = fetch_companyfacts(fetcher, cik)
    except (SecFairAccessHalt, SecResourceLimitHalt):
        # A fair-access/resource halt is a STOP directive for the whole SEC run, never an
        # enrichment miss to swallow and continue through the remaining watchlist.
        raise
    except Exception as exc:                  # noqa: BLE001 - enrichment, never fatal
        log.warning("companyfacts unavailable for CIK %s (%s); paired filings held for retry",
                    cik, type(exc).__name__)
        return {}, type(exc).__name__
    return (
        {acc: _to_xbrl_facts(facts_for_accession(doc, acc), cik) for acc in accessions},
        None,
    )


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
    process_epoch: str = "",
    strict_completeness_ciks: Iterable[str] = (),
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

    baseline_store = (
        _VerifiedPollBaselineStore(session_factory, audit_log)
        if audit_log is not None else None
    )
    poll_fetcher = (
        _CompleteSubmissionsFetcher(
            fetcher, baseline_store, strict_ciks=strict_completeness_ciks
        )
        if baseline_store is not None else fetcher
    )
    poller = SubmissionsPoller(
        poll_fetcher, raw_store, session_factory, clock=clock, audit_log=audit_log,
        baseline_store=baseline_store,
        baseline_on_first_poll=audit_log is not None,
        max_new_filings_per_poll=getattr(settings, "sec_max_new_filings_per_poll", 25),
        on_filing=_filing_audit_hook(audit_log, raw_store) if audit_log is not None else None,
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

    # Extraction is a durable second step after filing/raw persistence. Include prior error
    # rows even when this SEC tick discovered no new filing: the submissions cursor will not
    # re-download an already-ingested accession, so the retry ledger is its recovery path.
    candidates = [] if halted else _extraction_candidates(session_factory, cik_list)
    by_cik: dict[str, list] = {}
    for filing in candidates:
        by_cik.setdefault(filing.cik, []).append(filing)

    delta_failed_ciks: set[str] = set()
    for cik, filings in by_cik.items():
        from filings.edgar.fetch import SecFairAccessHalt, SecResourceLimitHalt

        try:
            facts, facts_error = _facts_by_accession(
                fetcher, cik,
                {f.accession_no for f in filings} | {
                    p.accession_no for p in (
                        _prior_same_form(session_factory, f) for f in filings
                    ) if p is not None
                },
            )
        except (SecFairAccessHalt, SecResourceLimitHalt) as exc:
            result.poll_errors.append((cik, type(exc).__name__))
            delta_failed_ciks.add(cik)
            log.warning("SEC halt during companyfacts enrichment; extraction fan-out stopped")
            break
        for filing in filings:
            prior = _prior_same_form(session_factory, filing)
            if facts_error is not None and prior is not None:
                # Accession-level event dedup means a text-only event emitted now would burn
                # the identity needed to surface numeric deltas after recovery. Record the
                # retry state BEFORE any partial Delta commit and publish this filing only
                # once its complete deterministic extraction succeeds.
                error = f"companyfacts:{facts_error}"
                result.delta_errors.append((filing.accession_no, error))
                delta_failed_ciks.add(cik)
                _record_extraction_state(
                    session_factory, filing, status="error", error=error,
                    clock=clock, audit_log=audit_log,
                )
                continue
            try:
                deltas = compute_deltas(
                    filing, prior, raw_store,
                    facts.get(filing.accession_no, []),
                    facts.get(prior.accession_no, []) if prior is not None else [],
                    clock=clock,
                )
                written = _persist_deltas(session_factory, deltas)
            except UnpairedPeriodicFiling:
                # EXPECTED, not an error: the first 10-K/10-Q ever seen for an issuer has
                # nothing to diff against. It is stored as evidence and becomes the baseline
                # the NEXT one is measured against.
                result.baselines += 1
                _record_extraction_state(
                    session_factory, filing, status="baseline", error=None,
                    clock=clock, audit_log=audit_log,
                )
                continue
            except Exception as exc:          # noqa: BLE001 - isolate one filing's failure
                error = type(exc).__name__
                result.delta_errors.append((filing.accession_no, error))
                delta_failed_ciks.add(cik)
                _record_extraction_state(
                    session_factory, filing, status="error", error=error,
                    clock=clock, audit_log=audit_log,
                )
                log.warning("delta extraction failed for %s (%s); other filings unaffected",
                            filing.accession_no, error)
                continue
            result.deltas_persisted += written
            _record_extraction_state(
                session_factory, filing, status="complete", error=None,
                clock=clock, audit_log=audit_log,
            )

    # A fair-access/resource halt is global: no CIK from that run receives a freshness grant.
    # Otherwise preserve per-CIK isolation while excluding any issuer with a baseline, item,
    # polling, or extraction error.  The durable events are the only liveness authority used by
    # the merger strategy; an in-memory status string can never substitute for them.
    if audit_log is not None and not halted:
        from filings.edgar.submissions_poll import pad_cik

        failed_ciks = set(delta_failed_ciks)
        for label, _reason in result.poll_errors:
            candidate = str(label).split(":", 1)[0]
            try:
                failed_ciks.add(pad_cik(candidate))
            except ValueError:
                continue
        successful_ciks = [
            cik for cik, poll in polled.items()
            if cik not in failed_ciks and not poll.errors
        ]
        try:
            _record_poll_completions(
                session_factory, audit_log, clock, successful_ciks,
                process_epoch=process_epoch,
            )
        except Exception as exc:  # noqa: BLE001 - missing authority makes the run degraded
            result.poll_errors.append(("poll-watermark", type(exc).__name__))
            log.warning("EDGAR poll completion watermark failed: %r", exc)

    log.info("%s", result.summary())
    return result
