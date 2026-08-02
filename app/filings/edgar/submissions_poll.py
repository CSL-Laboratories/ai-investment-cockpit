"""WP3 — ``submissions_poll.py``: the data.sec.gov submissions poller (per CIK).

Pattern-sourced from the cockpit ``ingest/finnhub_poll.py`` (re-implemented, not copied):
an **injected fetch** so CI drives it offline, a **per-item isolation** loop (one malformed
filing never aborts the batch), **dedup before emit**, and a **cursor that advances only
from successfully-emitted items**. Everything Finnhub-specific is stripped — there is no
integer ``minId`` cursor and no ``MAX_ID_GAP`` (SEC accession numbers are STRINGS like
``0000320193-24-000123``, so an integer poison-gap bound is meaningless).

Idempotent ingestion (Invariant 11) rides on TWO persisted mechanisms, not the in-memory
LRU (which is lost on restart):

* the **persisted** :class:`schemas.models.PollCursor` (one row per CIK; survives
  restart), and
* the **UNIQUE** ``Filing.accession_no`` constraint — the true "never process an accession
  twice" guarantee. Before emitting, the poller checks whether a ``Filing`` row already
  exists for the accession, so a double-replay of the same feed ingests zero duplicates.

The processing filter is 10-K/10-Q/8-K only (the ``Filing`` table accepts all five form
types; Form 4/13F processing is deferred-additive). Each emitted filing gets a
``detection_ts`` (starts the dossier 15-minute metric clock) and its primary document is
downloaded — through :class:`filings.edgar.fetch.SecFetcher` — into the WP2 write-once raw
store keyed by accession.

Failure surfacing (Invariant 4 / WP8 foundation): a ``403``/``429`` :class:`SecFairAccessHalt`
propagates and HALTS the whole poll (never swallowed by per-item isolation); a submissions
:class:`SecTransportError` propagates too — a throttled or unreachable EDGAR is a raised
error, never an empty result masquerading as a quiet filing day.
"""
from __future__ import annotations

import hmac
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select

from core.clock import Clock, default_clock
from core.ids import new_id
from filings.edgar.fetch import (
    SecFairAccessHalt,
    SecFetcher,
    SecResourceLimitHalt,
    archive_document_url,
    pad_cik,
    submissions_url,
)
from schemas.models.filing import Filing, FormType
from schemas.models.pollbaseline import PollBaseline
from schemas.models.pollcursor import PollCursor
from filings.raw_store import RawStore, RawStoreError

log = logging.getLogger("filings.edgar.submissions_poll")

# Processing filter (scaffold WP3): only these three are ingested; the Filing table accepts
# all five form types so adding Form 4 / 13F later is additive, not a rewrite.
PROCESS_FORMS: frozenset[str] = frozenset({"10-K", "10-Q", "8-K"})
DEFAULT_MAX_NEW_FILINGS_PER_POLL = 25


class PollBaselineBoundaryMissing(RuntimeError):
    """A saved SEC feed boundary is absent from a later response.

    The caller must rebaseline explicitly. Continuing with a full scan would make a
    truncated or malformed SEC response look like a new historical backlog.
    """


class PollFilingLimitExceeded(RuntimeError):
    """A poll discovered more new in-scope filings than its finite safety cap."""

    def __init__(self, cik: str, observed: int, limit: int):
        super().__init__(
            f"new in-scope filing cap exceeded for CIK {cik}: "
            f"observed {observed}, limit {limit}"
        )
        self.cik = cik
        self.observed = observed
        self.limit = limit


class _Dedup:
    """Bounded in-memory dedup by accession (cockpit ``ingest/dedup.py`` shape). The
    SUPPLEMENTARY layer only — the persisted cursor + UNIQUE constraint are authoritative
    across restarts (Invariant 11)."""

    def __init__(self, capacity: int = 50_000):
        self._seen: dict[str, None] = {}
        self._cap = capacity

    def add(self, key: str) -> bool:
        """Return True if newly added (not already seen this process)."""
        if key in self._seen:
            return False
        self._seen[key] = None
        if len(self._seen) > self._cap:
            self._seen.pop(next(iter(self._seen)))
        return True

    def seen(self, key: str) -> bool:
        return key in self._seen


@dataclass(frozen=True)
class RecentFiling:
    """One row of the submissions ``filings.recent`` parallel-array block."""

    cik: str
    accession: str
    form: str
    filing_date: date
    primary_document: str
    report_date: date | None = None


@dataclass
class PollResult:
    """The outcome of polling one CIK — distinguishes a quiet day (emitted 0, no error)
    from a failure (the poll raises). ``errors`` records per-item skips (isolation)."""

    cik: str
    filings: list[Filing] = field(default_factory=list)
    duplicates: int = 0
    skipped_form: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def emitted(self) -> int:
        return len(self.filings)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def iter_recent_filings(submissions: dict, cik: str) -> Iterator[RecentFiling]:
    """Zip the parallel arrays in ``filings.recent`` into :class:`RecentFiling` rows.

    ``recent`` is newest-first. A malformed row (short array, unparseable date) is skipped
    — per-item isolation at the parse layer, mirroring the emit loop.
    """
    cik10 = pad_cik(submissions.get("cik", cik))
    recent = (submissions.get("filings") or {}).get("recent") or {}
    accs = recent.get("accessionNumber") or []
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    reports = recent.get("reportDate") or []
    docs = recent.get("primaryDocument") or []
    for i in range(len(accs)):
        try:
            report_raw = reports[i] if i < len(reports) else ""
            yield RecentFiling(
                cik=cik10,
                accession=accs[i],
                form=forms[i],
                filing_date=_parse_date(dates[i]),
                primary_document=docs[i] if i < len(docs) else "",
                report_date=_parse_date(report_raw) if report_raw else None,
            )
        except (IndexError, ValueError, TypeError):
            continue


class PollCursorStore:
    """Persist / read the per-CIK :class:`PollCursor` (Invariant 11 — survives restart)."""

    def __init__(self, session_factory, clock: Clock = default_clock):
        self._sf = session_factory
        self._clock = clock

    def load(self, cik: str) -> PollCursor | None:
        with self._sf() as s:
            return s.execute(
                select(PollCursor).where(PollCursor.cik == pad_cik(cik))
            ).scalar_one_or_none()

    def _upsert(self, cik: str, now: datetime, last_accession_no: str, advance: bool) -> None:
        cik10 = pad_cik(cik)
        with self._sf() as s, s.begin():
            cur = s.execute(
                select(PollCursor).where(PollCursor.cik == cik10)
            ).scalar_one_or_none()
            if cur is None:
                cur = PollCursor(
                    id=new_id("cursor"),
                    cik=cik10,
                    last_accession_no=last_accession_no if advance else None,
                    last_polled_at=now,
                    created_at=now,
                    updated_at=now,
                )
                s.add(cur)
            else:
                if advance:
                    cur.last_accession_no = last_accession_no
                cur.last_polled_at = now
                cur.updated_at = now

    def advance(self, cik: str, last_accession_no: str, now: datetime) -> None:
        """Move the high-water mark to a successfully-emitted accession."""
        self._upsert(cik, now, last_accession_no, advance=True)

    def touch(self, cik: str, now: datetime) -> None:
        """Record that a poll happened but emitted nothing new (keep ``last_accession_no``)."""
        self._upsert(cik, now, last_accession_no="", advance=False)


class PollBaselineStore:
    """Persist a first-run SEC feed boundary with atomic audit-chain coverage.

    Read-only construction without an audit log is supported for diagnostics. Every
    mutation fails closed unless an object exposing ``append_in(session, ...)`` was
    injected; the row mutation and signed event share the caller transaction.
    """

    def __init__(self, session_factory, audit_log=None):
        self._sf = session_factory
        self._audit_log = audit_log

    @property
    def is_audited(self) -> bool:
        return callable(getattr(self._audit_log, "append_in", None))

    def _require_audit(self):
        if not self.is_audited:
            raise RuntimeError(
                "PollBaseline mutations require an injected audit_log.append_in; "
                "unaudited baseline mode is forbidden"
            )
        return self._audit_log

    @staticmethod
    def _refs(baseline: PollBaseline) -> dict[str, str]:
        return {
            "model": baseline.__tablename__,
            "row_id": baseline.id,
            "cik": baseline.cik,
        }

    def load(self, cik: str) -> PollBaseline | None:
        with self._sf() as s:
            return s.execute(
                select(PollBaseline).where(PollBaseline.cik == pad_cik(cik))
            ).scalar_one_or_none()

    def establish(self, cik: str, cutoff_accession_no: str | None, now: datetime) -> bool:
        """Create the first boundary, returning ``False`` if one already exists."""
        audit_log = self._require_audit()
        cik10 = pad_cik(cik)
        with self._sf() as s, s.begin():
            existing = s.execute(
                select(PollBaseline).where(PollBaseline.cik == cik10)
            ).scalar_one_or_none()
            if existing is not None:
                return False
            baseline = PollBaseline(
                id=new_id("baseline"),
                cik=cik10,
                cutoff_accession_no=cutoff_accession_no,
                established_at=now,
                updated_at=now,
            )
            s.add(baseline)
            s.flush()
            audit_log.append_in(
                s,
                "poll-baseline-established",
                actor="submissions-poller",
                refs=self._refs(baseline),
                payload={"cutoff_accession_no": cutoff_accession_no},
            )
        return True

    def advance(self, cik: str, cutoff_accession_no: str, now: datetime) -> None:
        """Advance the boundary after every newer feed item was handled or skipped."""
        audit_log = self._require_audit()
        cik10 = pad_cik(cik)
        with self._sf() as s, s.begin():
            baseline = s.execute(
                select(PollBaseline).where(PollBaseline.cik == cik10)
            ).scalar_one_or_none()
            if baseline is None:
                raise ValueError(f"no baseline exists for CIK {cik10}")
            previous = baseline.cutoff_accession_no
            baseline.cutoff_accession_no = cutoff_accession_no
            baseline.updated_at = now
            s.flush()
            audit_log.append_in(
                s,
                "poll-baseline-advanced",
                actor="submissions-poller",
                refs=self._refs(baseline),
                payload={
                    "previous_cutoff_accession_no": previous,
                    "cutoff_accession_no": cutoff_accession_no,
                },
            )

    def touch(self, cik: str, now: datetime) -> None:
        """Record a check without moving its historical-feed boundary."""
        audit_log = self._require_audit()
        cik10 = pad_cik(cik)
        with self._sf() as s, s.begin():
            baseline = s.execute(
                select(PollBaseline).where(PollBaseline.cik == cik10)
            ).scalar_one_or_none()
            if baseline is None:
                raise ValueError(f"no baseline exists for CIK {cik10}")
            baseline.updated_at = now
            s.flush()
            audit_log.append_in(
                s,
                "poll-baseline-touched",
                actor="submissions-poller",
                refs=self._refs(baseline),
                payload={"cutoff_accession_no": baseline.cutoff_accession_no},
            )


class SubmissionsPoller:
    """Poll data.sec.gov submissions for one or more CIKs, ingesting new 10-K/10-Q/8-K
    filings idempotently.

    :param on_filing: optional hook ``(session, filing)`` invoked INSIDE the insert
        transaction, so an integrator can atomically append an audit entry (Inv 10) later
        without this module importing the audit layer.
    """

    def __init__(
        self,
        fetcher: SecFetcher,
        raw_store: RawStore,
        session_factory,
        *,
        clock: Clock = default_clock,
        cursor_store: PollCursorStore | None = None,
        baseline_store: PollBaselineStore | None = None,
        baseline_on_first_poll: bool = False,
        audit_log=None,
        max_new_filings_per_poll: int = DEFAULT_MAX_NEW_FILINGS_PER_POLL,
        dedup: _Dedup | None = None,
        process_forms: Iterable[str] = PROCESS_FORMS,
        on_filing=None,
    ):
        self._fetcher = fetcher
        self._raw_store = raw_store
        self._sf = session_factory
        self._clock = clock
        self._cursor_store = cursor_store or PollCursorStore(session_factory, clock)
        self._baseline_store = baseline_store or PollBaselineStore(session_factory, audit_log)
        self._baseline_on_first_poll = baseline_on_first_poll
        if baseline_on_first_poll and not self._baseline_store.is_audited:
            raise ValueError(
                "baseline_on_first_poll requires an injected audit_log; "
                "production baseline writes may not be unaudited"
            )
        if max_new_filings_per_poll <= 0:
            raise ValueError("max_new_filings_per_poll must be positive")
        self._max_new_filings_per_poll = max_new_filings_per_poll
        self._dedup = dedup or _Dedup()
        self._process_forms = frozenset(process_forms)
        self._on_filing = on_filing

    # ---- dedup / persistence seams ----------------------------------------------------
    def _is_ingested(self, accession: str) -> bool:
        with self._sf() as s:
            return (
                s.execute(select(Filing.id).where(Filing.accession_no == accession)).first()
                is not None
            )

    def _has_ingested_cik(self, cik: str) -> bool:
        with self._sf() as s:
            query = select(Filing.id).where(Filing.cik == pad_cik(cik))
            return s.execute(query).first() is not None

    def _persist(self, filing: Filing) -> Filing:
        with self._sf() as s, s.begin():
            s.add(filing)
            s.flush()
            if self._on_filing is not None:
                self._on_filing(s, filing)  # atomic audit hook (Inv 10) — optional
            s.expunge(filing)
        return filing

    def _ingest_one(self, item: RecentFiling) -> Filing:
        doc_url = archive_document_url(item.cik, item.accession, item.primary_document)
        body = self._fetcher.get_bytes(doc_url)  # rate-limited + UA; 403/429 -> halt propagates
        # Attempt the write directly rather than check-then-write. If a prior process
        # crashed after atomically publishing raw bytes but before committing the Filing
        # row, reuse them only when their integrity-checked bytes match this SEC response.
        try:
            self._raw_store.write(item.accession, body)
        except FileExistsError:
            persisted = self._raw_store.resolve(item.accession)
            if not hmac.compare_digest(persisted, body):
                raise RawStoreError(
                    f"existing raw bytes disagree with SEC response for {item.accession!r}"
                ) from None
        filing = Filing(
            cik=item.cik,
            accession_no=item.accession,
            form_type=FormType(item.form),
            filing_date=item.filing_date,
            detection_ts=self._clock.now(),  # starts the 15-min metric clock
            raw_ref=item.accession,  # RawStore keys by accession; raw_ref points at it
            # Persist the real primary-document filename (already fetched above to build
            # doc_url) so downstream citations render a working EDGAR archive URL (WP5).
            primary_doc=item.primary_document or None,
        )
        return self._persist(filing)

    # ---- first-run baseline -----------------------------------------------------------
    def baseline(self, cik: str) -> bool:
        """Record the current SEC feed position without downloading a filing.

        This is opt-in production behavior for a watchlist's first contact with a CIK.
        An existing baseline is left untouched. A legacy cursor or persisted filing means
        that CIK has already been used, so it continues with normal polling rather than
        silently becoming a fresh baseline.
        """
        if not self._baseline_on_first_poll:
            return False
        cik10 = pad_cik(cik)
        if self._baseline_store.load(cik10) is not None:
            return False
        if self._cursor_store.load(cik10) is not None or self._has_ingested_cik(cik10):
            return False
        submissions = self._fetcher.get_json(submissions_url(cik10))
        rows = list(iter_recent_filings(submissions, cik10))
        # ``recent`` is newest-first. The marker intentionally includes a Form 4 if that
        # is the newest valid row, because comparisons happen before form filtering.
        cutoff = rows[0].accession if rows else None
        return self._baseline_store.establish(cik10, cutoff, self._clock.now())

    def baseline_missing(self, ciks: Iterable[str]) -> tuple[str, ...]:
        """Establish first-run boundaries for watchlist CIKs not yet initialized."""
        established: list[str] = []
        seen: set[str] = set()
        for cik in ciks:
            cik10 = pad_cik(cik)
            if cik10 in seen:
                continue
            seen.add(cik10)
            if self.baseline(cik10):
                established.append(cik10)
        return tuple(established)

    def _enforce_new_filing_limit(
        self, cik: str, candidate_rows: Iterable[RecentFiling]
    ) -> None:
        new_accessions: set[str] = set()
        for item in candidate_rows:
            if item.form not in self._process_forms or item.accession in new_accessions:
                continue
            if self._dedup.seen(item.accession) or self._is_ingested(item.accession):
                continue
            new_accessions.add(item.accession)
            if len(new_accessions) > self._max_new_filings_per_poll:
                raise PollFilingLimitExceeded(
                    cik, len(new_accessions), self._max_new_filings_per_poll
                )

    # ---- the poll ---------------------------------------------------------------------
    def poll(self, cik: str) -> PollResult:
        """Poll one CIK. Emits new in-scope filings; returns a :class:`PollResult`.

        Raises :class:`SecFairAccessHalt` on a 403/429 (halt, no retry) and
        :class:`filings.edgar.fetch.SecTransportError` on a network failure — both DISTINCT
        from a quiet day (which returns a result with ``emitted == 0``).
        """
        cik10 = pad_cik(cik)
        baseline = self._baseline_store.load(cik10)
        if baseline is not None and not self._baseline_store.is_audited:
            raise RuntimeError(
                "persisted PollBaseline requires an injected audit_log before polling"
            )
        submissions = self._fetcher.get_json(submissions_url(cik10))  # raises distinctly
        rows = list(iter_recent_filings(submissions, cik10))
        result = PollResult(cik=cik10)
        emitted: list[str] = []
        candidate_rows = rows
        if baseline is not None and baseline.cutoff_accession_no is not None:
            try:
                cutoff_index = next(
                    index
                    for index, item in enumerate(rows)
                    if item.accession == baseline.cutoff_accession_no
                )
            except StopIteration as exc:
                raise PollBaselineBoundaryMissing(
                    f"saved baseline accession {baseline.cutoff_accession_no!r} "
                    f"is absent for CIK {cik10}"
                ) from exc
            candidate_rows = rows[:cutoff_index]

        # Fail before the first archive-document request. A malformed/truncated feed must
        # not turn one poll into an unbounded historical backfill.
        self._enforce_new_filing_limit(cik10, candidate_rows)

        for item in candidate_rows:
            if item.form not in self._process_forms:
                result.skipped_form += 1
                continue
            acc = item.accession
            if self._dedup.seen(acc) or self._is_ingested(acc):
                result.duplicates += 1
                continue
            try:
                filing = self._ingest_one(item)
            except (SecFairAccessHalt, SecResourceLimitHalt):
                raise  # 403/429 halts the whole run — NEVER isolated as a per-item skip
            except Exception as exc:  # per-item isolation: one bad filing never aborts the batch
                result.errors.append((acc, repr(exc)))
                log.warning("skipping filing %s (CIK %s): %r", acc, cik10, exc)
                continue
            result.filings.append(filing)
            emitted.append(acc)
            self._dedup.add(acc)

        # Cursor advances ONLY from successfully-emitted items (newest emitted is first,
        # since recent[] is newest-first). A quiet/caught-up poll still records last_polled_at.
        now = self._clock.now()
        if emitted:
            self._cursor_store.advance(cik10, emitted[0], now)
        else:
            self._cursor_store.touch(cik10, now)
        if baseline is not None:
            # The saved marker can move only past a contiguous handled range. Keep every
            # failed in-scope accession above the next marker so a later poll retries it.
            error_accessions = {accession for accession, _error in result.errors}
            error_indexes = [
                index
                for index, item in enumerate(candidate_rows)
                if item.accession in error_accessions
            ]
            if error_indexes:
                next_index = max(error_indexes) + 1
                if next_index < len(candidate_rows):
                    self._baseline_store.advance(
                        cik10, candidate_rows[next_index].accession, now
                    )
                else:
                    self._baseline_store.touch(cik10, now)
            elif candidate_rows:
                self._baseline_store.advance(cik10, candidate_rows[0].accession, now)
            else:
                self._baseline_store.touch(cik10, now)
        return result

    def poll_all(self, ciks: Iterable[str]) -> dict[str, PollResult]:
        """Poll several CIKs. A fair-access halt stops the whole fan-out (fail-closed)."""
        return {pad_cik(c): self.poll(c) for c in ciks}
