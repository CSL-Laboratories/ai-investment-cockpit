"""RW-10 — the in-process EDGAR pipeline, driven end-to-end over recorded SEC fixtures.

This is the test that says the absorption actually WORKS rather than merely imports: real
recorded NVDA/AAPL filing bytes go in at one end and cited, persisted ``Delta`` rows come out
at the other, inside the cockpit's own database, with no network and no model call.

The last test in this file is the one that matters most for the project: it proves the rows
this pipeline writes are readable by ``ingest/edgar_brew.py`` — the hardened reader that
already survived six adversarial rounds — with nothing pointed anywhere new except the
cockpit's own DB. That is what makes the in-process path a drop-in for the cross-process one
instead of a parallel universe that still needs an adapter written for it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from config.settings import Settings
from core.clock import FrozenClock
from filings.raw_store import RawStore
from ingest.edgar_pipeline import (
    EdgarPipelineResult,
    backfill_filing_accession,
    build_sec_fetcher,
    run_edgar_pipeline,
)
from storage.engine import create_all, make_engine, make_session_factory
from tests.filings._sec_replay import FixtureTransport, StatusTransport, VirtualTime

UA = "Lucas Guerra lucas@example.com (personal research)"
NVDA = "0001045810"
AAPL = "0000320193"
CLOCK = FrozenClock(datetime(2026, 7, 8, 9, 30, tzinfo=UTC))


class AdvancingClock:
    def __init__(self, at, step=timedelta(milliseconds=1)):
        self.at = at
        self.step = step

    def now(self):
        current = self.at
        self.at += self.step
        return current


@pytest.fixture(autouse=True)
def _no_live_sec(monkeypatch):
    """NOTHING in this file may reach sec.gov. Fixtures only — upstream's rule, kept."""
    import urllib.request

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("a live urllib.request.urlopen was attempted in a pipeline test")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def sf(tmp_path):
    eng = make_engine(f"sqlite:///{(tmp_path / 'cockpit.db').as_posix()}")
    create_all(eng)
    return make_session_factory(eng)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "cockpit.db"


@pytest.fixture
def store(tmp_path):
    return RawStore(tmp_path / "raw")


@pytest.fixture
def settings():
    # The recorded NVDA fixture holds 26 in-scope filings — one MORE than the production
    # default cap of 25, which is exactly the kind of thing you only learn by running it.
    # Raised here so the happy-path tests exercise the pipeline rather than the cap; the cap
    # gets its own tests below.
    return Settings(sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
                    sec_max_new_filings_per_poll=250)


def make_fetcher(transport):
    from filings.edgar.fetch import SecFetcher

    vt = VirtualTime()
    return SecFetcher(user_agent=UA, rate_per_sec=2.0, transport=transport,
                      monotonic=vt.monotonic, sleep=vt.sleep)


def run(sf, store, settings, ciks=(NVDA,), transport=None):
    return run_edgar_pipeline(
        sf, settings=settings, ciks=list(ciks), raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(transport or FixtureTransport()),
    )


# ---------------------------------------------------------------------------------------
# unprovisioned: one dead feed, never a dead process
# ---------------------------------------------------------------------------------------
def test_no_user_agent_reports_unprovisioned_and_does_not_raise(sf, store):
    """SEC needs no API key, but it does need a declared UA. Missing one must degrade THIS
    feed only — the same per-feed posture alpaca/finnhub/finra already follow."""
    result = run_edgar_pipeline(
        sf, settings=Settings(), ciks=[NVDA], raw_store=store, clock=CLOCK,
    )
    assert result.provisioned is False
    assert result.ok is False
    assert "COCKPIT_SEC_USER_AGENT" in result.unprovisioned_reason
    assert "no API key" in result.unprovisioned_reason
    assert result.filings_ingested == 0


def test_build_sec_fetcher_returns_none_without_a_user_agent():
    assert build_sec_fetcher(Settings()) is None
    assert build_sec_fetcher(Settings(sec_user_agent="   ")) is None, "whitespace is not a UA"


def test_build_sec_fetcher_builds_one_when_provisioned(settings):
    assert build_sec_fetcher(settings, transport=FixtureTransport()) is not None


def test_an_empty_cik_list_is_a_clean_no_op(sf, store, settings):
    result = run(sf, store, settings, ciks=())
    assert result.ok and result.ciks_polled == 0 and result.filings_ingested == 0


# ---------------------------------------------------------------------------------------
# the pipeline itself
# ---------------------------------------------------------------------------------------
def test_pipeline_ingests_filings_and_persists_deltas(sf, store, settings):
    """THE end-to-end proof: recorded SEC bytes -> Filing rows -> cited Delta rows."""
    result = run(sf, store, settings)

    assert result.provisioned is True
    assert result.ciks_polled == 1
    assert result.filings_ingested > 0, "no filings ingested from the NVDA fixtures"
    assert result.deltas_persisted > 0, "filings ingested but no deltas computed"
    assert result.delta_errors == []
    assert result.quiet is False


def test_persisted_deltas_carry_a_resolvable_byte_anchor(sf, store, settings):
    """A delta whose snippet cannot be reproduced from the stored bytes is an unverifiable
    claim, which is the whole thing this pipeline exists NOT to produce."""
    from sqlalchemy import select

    from schemas.models.delta import Delta

    run(sf, store, settings)
    with sf() as s:
        deltas = s.execute(select(Delta)).scalars().all()
    assert deltas
    checked = 0
    for d in deltas[:25]:
        if not d.raw_ref or d.snippet is None:
            continue
        blob = store.resolve(d.raw_ref)
        assert blob[d.snippet_start_offset:d.snippet_end_offset].decode("utf-8") == d.snippet
        checked += 1
    assert checked > 0, "no delta carried a resolvable anchor to check"


def test_a_second_run_persists_no_duplicate_deltas(sf, store, settings):
    """IDEMPOTENCY. A scheduler retry, or a crash-and-restart, must not double the evidence
    the morning brief then reads."""
    first = run(sf, store, settings)
    second = run(sf, store, settings)

    assert first.deltas_persisted > 0
    assert second.deltas_persisted == 0, "a re-run duplicated deltas"
    assert second.filings_ingested == 0, "a re-run re-ingested filings"
    # NOT asserting second.quiet here, and the reason is worth stating: the recorded fixture
    # set deliberately holds documents for only a few accessions, so the poller records a
    # per-item 404 skip for the rest. Those skips are REAL — evidence SEC listed and we could
    # not fetch — so `quiet` is correctly False. `quiet` means "we reached SEC and there was
    # genuinely nothing new", which is a stronger claim than "we ingested nothing".
    assert second.poll_errors, "expected the fixture's unrecorded documents to be counted"


def test_quiet_means_nothing_new_not_merely_nothing_ingested():
    """The distinction that keeps a broken feed from reading like a calm market."""
    assert EdgarPipelineResult(ciks_polled=1).quiet is True
    assert EdgarPipelineResult(ciks_polled=1, filings_ingested=3).quiet is False
    assert EdgarPipelineResult(ciks_polled=1, poll_errors=[("x", "SecHttpError")]).quiet is False
    assert EdgarPipelineResult(ciks_polled=1, delta_errors=[("x", "ValueError")]).quiet is False
    assert EdgarPipelineResult(provisioned=False).quiet is False, "unprovisioned is not quiet"
    assert EdgarPipelineResult(ciks_polled=0).quiet is False, "polling nothing is not a quiet day"


def test_two_ciks_are_both_polled(sf, store, settings):
    result = run(sf, store, settings, ciks=(NVDA, AAPL))
    assert result.ciks_polled == 2
    assert result.filings_ingested > 0


def test_the_declared_user_agent_is_actually_sent(sf, store, settings):
    """SEC fair access: a suppressed UA is empirically a 403, and the UA is how they reach us."""
    transport = FixtureTransport()
    run(sf, store, settings, transport=transport)
    assert transport.requests, "no SEC request was made at all"
    for _method, _url, headers in transport.requests:
        assert headers.get("User-Agent") == UA


# ---------------------------------------------------------------------------------------
# failure isolation — a bad day must stay countable, never silent
# ---------------------------------------------------------------------------------------
def test_a_403_is_reported_not_swallowed(sf, store, settings):
    """A throttled or blocked SEC must never look like a quiet day."""
    result = run(sf, store, settings, transport=StatusTransport(403))
    assert result.filings_ingested == 0
    assert result.poll_errors, "a blocked feed recorded no error"
    assert result.quiet is False, "a blocked feed reported itself as a quiet day"
    assert result.ok is False


def test_a_fair_access_halt_STOPS_the_fan_out_rather_than_isolating_it(sf, store, settings):
    """NOT A ROBUSTNESS BUG — the one failure that must NOT be isolated.

    Upstream's ``poll_all`` docstring is explicit: "a fair-access halt stops the whole
    fan-out (fail-closed)". When SEC answers 403/429, continuing on to the next issuer is
    exactly the behaviour fair access forbids, so a blanket ``except`` in the runner would
    turn an upstream safety control into a politeness violation committed at speed. This
    pins that only the FIRST CIK is attempted.
    """
    transport = StatusTransport(403)
    result = run(sf, store, settings, ciks=(NVDA, AAPL), transport=transport)

    assert result.ciks_polled == 0, "a halted run recorded a successful poll"
    assert len(result.poll_errors) == 1, "the fan-out continued past a fair-access halt"
    requested_ciks = {url for _m, url, _h in transport.requests}
    assert not any(AAPL in u for u in requested_ciks), "polled the second CIK after a halt"


def test_fair_access_halt_skips_pending_extraction_and_companyfacts(sf, store, settings):
    """A retry candidate must not reopen network fan-out after SEC told the run to stop."""
    from sqlalchemy import select

    from schemas.models.filing_extraction_state import FilingExtractionState

    run(sf, store, settings)
    with sf() as session, session.begin():
        state = session.execute(select(FilingExtractionState).limit(1)).scalar_one()
        state.status = "error"
        state.last_error = "simulated retry"

    transport = StatusTransport(403)
    result = run(sf, store, settings, transport=transport)

    assert result.ok is False and result.poll_errors
    assert len(transport.requests) == 1, "companyfacts was requested after fair-access halt"


def test_companyfacts_outage_holds_retryable_filing_until_complete_extraction(
    sf, store, settings, monkeypatch
):
    """A partial event must not burn the identity needed by recovered numeric evidence."""
    from sqlalchemy import select

    import filings.edgar.companyfacts as companyfacts
    from schemas.models.delta import Delta
    from schemas.models.filing_extraction_state import FilingExtractionState

    real_fetch = companyfacts.fetch_companyfacts

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("temporary companyfacts outage")

    monkeypatch.setattr(companyfacts, "fetch_companyfacts", unavailable)
    partial = run(sf, store, settings)
    with sf() as session:
        partial_deltas = session.execute(select(Delta)).scalars().all()
        retryable = session.execute(
            select(FilingExtractionState).where(FilingExtractionState.status == "error")
        ).scalars().all()

    assert partial.delta_errors and retryable, "numeric outage was marked complete"
    retry_accessions = {row.accession_no for row in retryable}
    assert not [
        delta for delta in partial_deltas if delta.accession_no in retry_accessions
    ], "a retryable accession was partially persisted and could burn event dedup"

    monkeypatch.setattr(companyfacts, "fetch_companyfacts", real_fetch)
    recovered = run(sf, store, settings)
    with sf() as session:
        still_retryable = session.execute(
            select(FilingExtractionState).where(FilingExtractionState.status == "error")
        ).scalars().all()

    assert recovered.delta_errors == []
    assert still_retryable == []
    assert recovered.deltas_persisted > 0


def test_the_new_filing_cap_is_a_per_cik_fault_that_does_not_abort_the_fan_out(sf, store):
    """The OTHER side of the fair-access coin: a per-issuer fault is isolated and counted.

    Both recorded issuers hold more than 25 in-scope filings, so a cap of 25 trips on each —
    which makes this a clean test of the isolation itself: the loop must ATTEMPT every CIK
    and record an error per CIK, rather than abandoning the fan-out at the first one (which
    is what a fair-access halt, and only a fair-access halt, is allowed to do).
    """
    low_cap = Settings(sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
                       sec_max_new_filings_per_poll=25)
    result = run(sf, store, low_cap, ciks=(NVDA, AAPL))

    assert len(result.poll_errors) == 2, "the fan-out stopped at the first capped issuer"
    assert all("PollFilingLimitExceeded" in reason for _c, reason in result.poll_errors)
    assert result.ok is False, "a capped run must not report itself as clean"
    assert result.quiet is False, "a capped run must not read as a quiet day"


def test_result_summary_names_every_outcome(sf, store, settings):
    result = run(sf, store, settings)
    summary = result.summary()
    for token in ("cik(s)", "filing(s)", "delta(s)", "baseline(s)", "error(s)"):
        assert token in summary


def test_unprovisioned_summary_says_so():
    r = EdgarPipelineResult(provisioned=False, unprovisioned_reason="set COCKPIT_SEC_USER_AGENT")
    assert "NOT PROVISIONED" in r.summary()


# ---------------------------------------------------------------------------------------
# THE SEAM: the hardened reader consumes what this pipeline writes
# ---------------------------------------------------------------------------------------
def test_the_existing_brew_reader_can_read_the_rows_this_pipeline_writes(
    tmp_path, settings, store
):
    """The in-process path is a DROP-IN for the cross-process one.

    ``ingest/edgar_brew.make_brew_fetch`` is ~1000 hardened lines that survived six
    adversarial rounds reading a FOREIGN SQLite. Because RW-10 rebased the brew's models onto
    the cockpit's own ``Base``, the identical reader — unchanged, still opening a read-only
    URI — now reads the cockpit's own database. That is what makes this an absorption rather
    than a rewrite, and it is why RW-10 did not have to touch that module at all.
    """
    from ingest.edgar_brew import make_brew_fetch

    db = tmp_path / "cockpit.db"
    eng = make_engine(f"sqlite:///{db.as_posix()}")
    create_all(eng)
    sf_local = make_session_factory(eng)

    result = run_edgar_pipeline(
        sf_local, settings=settings, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()),
    )
    assert result.deltas_persisted > 0

    fetch = make_brew_fetch(str(db))
    rows = fetch(None, 0.0)

    assert rows, "the hardened reader saw nothing the pipeline had just written"
    row = rows[0]
    for key in ("delta_id", "accession_no", "cik", "created_at"):
        assert key in row, f"reader row is missing {key}"


# ---------------------------------------------------------------------------------------
# BUG-053: first contact with an established CIK must baseline, not fault forever
# ---------------------------------------------------------------------------------------
def _audit_log(sf):
    from storage.audit import AuditLog

    return AuditLog(sf, hmac_key=b"edgar-pipeline-key", clock=CLOCK)


def test_verified_baseline_uses_signed_timestamps_when_the_clock_advances(sf):
    """BUG-106: a real clock advances between the domain write and audit append.

    FrozenClock previously made both independent samples identical, hiding that every
    production baseline diverged from its signed history by a few milliseconds.
    """
    from sqlalchemy import select

    from ingest.edgar_pipeline import _VerifiedPollBaselineStore
    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline
    from storage.audit import AuditLog

    clock = AdvancingClock(CLOCK.now())
    audit = AuditLog(sf, hmac_key=b"edgar-pipeline-key", clock=clock)
    store = _VerifiedPollBaselineStore(sf, audit)

    assert store.establish(NVDA, "cutoff-1", clock.now()) is True
    assert store.load(NVDA).cutoff_accession_no == "cutoff-1"
    store.advance(NVDA, "cutoff-2", clock.now())
    assert store.load(NVDA).cutoff_accession_no == "cutoff-2"
    store.touch(NVDA, clock.now())
    assert store.load(NVDA).cutoff_accession_no == "cutoff-2"

    with sf() as session:
        baseline = session.execute(select(PollBaseline)).scalar_one()
        events = session.execute(
            select(AuditEntry).order_by(AuditEntry.seq)
        ).scalars().all()
    assert baseline.established_at == events[0].created_at
    assert baseline.updated_at == events[-1].created_at
    assert audit.verify_chain().ok


def test_verified_baseline_reconciles_narrow_legacy_clock_skew_once(sf):
    """Existing BUG-106 rows recover from signed authority without a DB reset."""
    from sqlalchemy import func, select, update

    from filings.edgar.submissions_poll import PollBaselineStore
    from ingest.edgar_pipeline import EdgarPipelineRunError, _VerifiedPollBaselineStore
    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline
    from storage.audit import AuditLog

    clock = AdvancingClock(CLOCK.now())
    audit = AuditLog(sf, hmac_key=b"edgar-pipeline-key", clock=clock)
    legacy = PollBaselineStore(sf, audit)
    assert legacy.establish(NVDA, "cutoff-1", clock.now()) is True

    verified = _VerifiedPollBaselineStore(sf, audit)
    assert verified.load(NVDA).cutoff_accession_no == "cutoff-1"
    assert verified.load(NVDA).cutoff_accession_no == "cutoff-1"

    with sf() as session:
        baseline = session.execute(select(PollBaseline)).scalar_one()
        events = session.execute(
            select(AuditEntry).order_by(AuditEntry.seq)
        ).scalars().all()
    assert [event.event_type for event in events] == [
        "poll-baseline-established",
        "poll-baseline-touched",
    ]
    assert events[-1].actor == "edgar-baseline-repair"
    assert events[-1].payload["timestamp_reconciliation"] == "BUG-106"
    assert baseline.established_at == events[0].created_at
    assert baseline.updated_at == events[-1].created_at

    with sf() as attacker, attacker.begin():
        attacker.execute(
            update(PollBaseline).values(
                updated_at=events[-1].created_at - timedelta(milliseconds=1)
            )
        )
    with pytest.raises(EdgarPipelineRunError, match="diverges from signed history"):
        verified.load(NVDA)
    with sf() as session:
        repairs = session.execute(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.actor == "edgar-baseline-repair"
            )
        ).scalar_one()
    assert repairs == 1
    assert audit.verify_chain().ok


def test_concurrent_loads_share_one_signed_legacy_repair(sf):
    """One composed store serializes concurrent callers and repairs exactly once."""
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import func, select

    from filings.edgar.submissions_poll import PollBaselineStore
    from ingest.edgar_pipeline import _VerifiedPollBaselineStore
    from schemas.models.audit_entry import AuditEntry
    from storage.audit import AuditLog

    clock = AdvancingClock(CLOCK.now())
    audit = AuditLog(sf, hmac_key=b"edgar-pipeline-key", clock=clock)
    assert PollBaselineStore(sf, audit).establish(
        NVDA, "cutoff-1", clock.now()
    ) is True
    verified = _VerifiedPollBaselineStore(sf, audit)

    with ThreadPoolExecutor(max_workers=2) as pool:
        loaded = list(pool.map(verified.load, (NVDA, NVDA)))

    assert [row.cutoff_accession_no for row in loaded] == ["cutoff-1", "cutoff-1"]
    with sf() as session:
        repairs = session.execute(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.actor == "edgar-baseline-repair"
            )
        ).scalar_one()
    assert repairs == 1
    assert audit.verify_chain().ok


@pytest.mark.parametrize(
    ("step", "repairable"),
    [
        (timedelta(seconds=1), True),
        (timedelta(seconds=1, microseconds=1), False),
        (timedelta(milliseconds=-1), False),
    ],
)
def test_verified_baseline_legacy_skew_boundary_is_fail_closed(sf, step, repairable):
    """Only a bounded, forward double sample is eligible for reconciliation."""
    from filings.edgar.submissions_poll import PollBaselineStore
    from ingest.edgar_pipeline import EdgarPipelineRunError, _VerifiedPollBaselineStore
    from storage.audit import AuditLog

    clock = AdvancingClock(CLOCK.now(), step=step)
    audit = AuditLog(sf, hmac_key=b"edgar-pipeline-key", clock=clock)
    legacy = PollBaselineStore(sf, audit)
    assert legacy.establish(NVDA, "cutoff-1", clock.now()) is True

    if repairable:
        assert _VerifiedPollBaselineStore(sf, audit).load(NVDA) is not None
    else:
        with pytest.raises(EdgarPipelineRunError, match="diverges from signed history"):
            _VerifiedPollBaselineStore(sf, audit).load(NVDA)
    assert audit.verify_chain().ok


def test_verified_baseline_timestamp_mutations_roll_back_when_audit_append_fails(sf):
    """Every BUG-106 row mutation remains atomic with its covering audit event."""
    from sqlalchemy import func, select

    from filings.edgar.submissions_poll import PollBaselineStore
    from ingest.edgar_pipeline import _VerifiedPollBaselineStore
    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline
    from storage.audit import AuditLog

    clock = AdvancingClock(CLOCK.now())
    audit = AuditLog(sf, hmac_key=b"edgar-pipeline-key", clock=clock)

    class FailingAppendAudit:
        def verify_session(self, session):
            return audit.verify_session(session)

        def append_in(self, *_args, **_kwargs):
            raise RuntimeError("audit unavailable")

    failing = FailingAppendAudit()
    with pytest.raises(RuntimeError, match="audit unavailable"):
        _VerifiedPollBaselineStore(sf, failing).establish(
            AAPL, "never-committed", clock.now()
        )
    with sf() as session:
        assert session.execute(
            select(func.count()).select_from(PollBaseline)
        ).scalar_one() == 0

    verified = _VerifiedPollBaselineStore(sf, audit)
    assert verified.establish(NVDA, "cutoff-1", clock.now()) is True
    failing_store = _VerifiedPollBaselineStore(sf, failing)
    before = failing_store.load(NVDA)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        failing_store.touch(NVDA, clock.now() + timedelta(minutes=5))
    with sf() as session:
        after = session.execute(
            select(PollBaseline).where(PollBaseline.cik == NVDA)
        ).scalar_one()
        event_count = session.execute(
            select(func.count()).select_from(AuditEntry)
        ).scalar_one()
    assert after.updated_at == before.updated_at
    assert event_count == 1

    legacy = PollBaselineStore(sf, audit)
    assert legacy.establish(AAPL, "legacy-cutoff", clock.now()) is True
    with sf() as session:
        legacy_before = session.execute(
            select(PollBaseline).where(PollBaseline.cik == AAPL)
        ).scalar_one()
        legacy_updated_at = legacy_before.updated_at
    with pytest.raises(RuntimeError, match="audit unavailable"):
        _VerifiedPollBaselineStore(sf, failing).load(AAPL)
    with sf() as session:
        legacy_after = session.execute(
            select(PollBaseline).where(PollBaseline.cik == AAPL)
        ).scalar_one()
        event_count = session.execute(
            select(func.count()).select_from(AuditEntry)
        ).scalar_one()
    assert legacy_after.updated_at == legacy_updated_at
    assert event_count == 2
    assert audit.verify_chain().ok


def test_advancing_clock_pipeline_keeps_baseline_history_current(sf, store, settings):
    """The composed EDGAR run reaches BUG-106's fixed establish/load/touch path."""
    from sqlalchemy import select

    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline
    from storage.audit import AuditLog

    clock = AdvancingClock(CLOCK.now())
    audit = AuditLog(sf, hmac_key=b"edgar-pipeline-key", clock=clock)
    configured = settings.model_copy(update={"sec_max_new_filings_per_poll": 25})

    first = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=clock,
        fetcher=make_fetcher(FixtureTransport()), audit_log=audit,
        process_epoch="advancing-clock-process",
    )
    second = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=clock,
        fetcher=make_fetcher(FixtureTransport()), audit_log=audit,
        process_epoch="advancing-clock-process",
    )

    assert first.ok and first.poll_errors == []
    assert second.ok and second.poll_errors == []
    with sf() as session:
        baseline = session.execute(select(PollBaseline)).scalar_one()
        events = session.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type.in_([
                "poll-baseline-established",
                "poll-baseline-advanced",
                "poll-baseline-touched",
            ]))
            .order_by(AuditEntry.seq)
        ).scalars().all()
    assert events[0].event_type == "poll-baseline-established"
    assert events[-1].event_type == "poll-baseline-touched"
    assert baseline.established_at == events[0].created_at
    assert baseline.updated_at == events[-1].created_at
    assert audit.verify_chain().ok


def test_adapter_filing_hook_atomically_binds_row_and_raw_hash(sf, store):
    from datetime import date

    from sqlalchemy import select

    from ingest.edgar_pipeline import _filing_audit_hook
    from schemas.models.audit_entry import AuditEntry
    from schemas.models.filing import Filing, FormType

    accession = "0001045810-26-123456"
    raw = store.write(accession, b"operator-reviewed merger filing")
    audit = _audit_log(sf)
    filing = Filing(
        id="filing-audited-adapter", cik=NVDA, accession_no=accession,
        form_type=FormType.FORM_8K, filing_date=date(2026, 7, 8),
        detection_ts=CLOCK.now(), raw_ref=accession, primary_doc="merger.htm",
    )
    with sf() as session, session.begin():
        session.add(filing)
        session.flush()
        _filing_audit_hook(audit, store)(session, filing)
    with sf() as session:
        event = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "filing_ingested")
        ).scalar_one()
    assert event.refs == {"model": "filing", "row_id": filing.id, "raw_ref": accession}
    assert event.payload["filing_date"] == "2026-07-08"
    assert event.payload["raw_content_hash"] == raw.sha256
    assert audit.verify_chain().ok


def test_first_contact_with_an_established_cik_baselines_instead_of_faulting(sf, store):
    """BUG-053: the recorded NVDA window holds 26 in-scope filings — one more than the
    production cap of 25. Before the fix, the first audited poll of any established issuer
    raised PollFilingLimitExceeded and did so again on EVERY subsequent run: the baseline
    machinery built for exactly this was dead wiring (baseline_on_first_poll defaulted
    False and nothing called baseline_missing). First contact must record the audited feed
    position, ingest nothing, and leave the next run quiet."""
    prod_cap = Settings(sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
                        sec_max_new_filings_per_poll=25)
    result = run_edgar_pipeline(
        sf, settings=prod_cap, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=_audit_log(sf),
        process_epoch="test-process",
    )
    assert result.poll_errors == [], "first contact must baseline, not trip the cap"
    assert result.ok is True
    assert result.filings_ingested == 0, "first contact records position; it never backfills"
    from sqlalchemy import select

    from schemas.models.audit_entry import AuditEntry

    with sf() as session:
        completion = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "edgar_poll_completed")
        ).scalar_one()
    assert completion.payload["cik"] == NVDA
    assert completion.payload["process_epoch"] == "test-process"
    assert completion.payload["filing_event_seq"] == 0

    result2 = run_edgar_pipeline(
        sf, settings=prod_cap, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=_audit_log(sf),
        process_epoch="test-process",
    )
    assert result2.poll_errors == [] and result2.filings_ingested == 0
    assert result2.ok is True


class _MutatedSubmissionsFetcher:
    def __init__(
        self, delegate, *, form="8-K/A", filing_date="2026-07-09", body_cik=None
    ):
        self._delegate = delegate
        self._form = form
        self._filing_date = filing_date
        self._body_cik = body_cik

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def get_bytes(self, url):
        return self._delegate.get_bytes(url)

    def get_json(self, url):
        import copy

        document = copy.deepcopy(self._delegate.get_json(url))
        if self._body_cik is not None and isinstance(document, dict):
            document["cik"] = self._body_cik
        recent = (document.get("filings") or {}).get("recent")
        if isinstance(recent, dict):
            overrides = {
                "accessionNumber": "0001045810-26-999999",
                "form": self._form,
                "filingDate": self._filing_date,
                "reportDate": "2026-07-09",
                "acceptanceDateTime": "2026-07-09T12:00:00.000Z",
                "primaryDocument": "merger-amendment.htm",
            }
            for name, values in recent.items():
                if isinstance(values, list):
                    value = overrides.get(name, values[0] if values else "")
                    values.insert(0, value)
        return document


class _InterleavingSubmissionsFetcher:
    def __init__(self, delegate, mutate_baseline):
        self._delegate = delegate
        self._mutate_baseline = mutate_baseline

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def get_bytes(self, url):
        return self._delegate.get_bytes(url)

    def get_json(self, url):
        document = self._delegate.get_json(url)
        recent = (document.get("filings") or {}).get("recent")
        if not isinstance(recent, dict):
            return document
        callback = self._mutate_baseline

        class RacingAccessions(list):
            fired = False

            def __iter__(self):
                if not self.fired:
                    self.fired = True
                    callback()
                return super().__iter__()

        recent["accessionNumber"] = RacingAccessions(recent["accessionNumber"])
        return document


class _DuplicateBoundaryFetcher(_MutatedSubmissionsFetcher):
    def __init__(self, delegate, cutoff):
        super().__init__(delegate)
        self._cutoff = cutoff

    def get_json(self, url):
        document = super().get_json(url)
        recent = (document.get("filings") or {}).get("recent")
        if not isinstance(recent, dict):
            return document
        boundary = recent["accessionNumber"].index(self._cutoff)
        for values in recent.values():
            if isinstance(values, list):
                values.insert(0, values[boundary])
        recent["filingDate"][1] = recent["filingDate"][0]
        recent["acceptanceDateTime"][1] = recent["acceptanceDateTime"][0]
        return document


@pytest.mark.parametrize(
    ("form", "filing_date"),
    [("8-K/A", "2026-07-09"), ("8-K", "not-a-date")],
)
def test_post_boundary_unsupported_or_malformed_rows_deny_poll_completion(
    sf, store, form, filing_date,
):
    from sqlalchemy import func, select

    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline

    configured = Settings(
        sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
        sec_max_new_filings_per_poll=25,
    )
    audit = _audit_log(sf)
    first = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=audit,
        process_epoch="test-process",
    )
    assert first.ok
    with sf() as session:
        cutoff = session.execute(select(PollBaseline.cutoff_accession_no)).scalar_one()

    attacked = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=_MutatedSubmissionsFetcher(
            make_fetcher(FixtureTransport()), form=form, filing_date=filing_date
        ),
        audit_log=audit, process_epoch="test-process",
        strict_completeness_ciks={NVDA},
    )

    assert attacked.poll_errors and not attacked.ok
    with sf() as session:
        assert session.execute(select(PollBaseline.cutoff_accession_no)).scalar_one() == cutoff
        completions = session.execute(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.event_type == "edgar_poll_completed"
            )
        ).scalar_one()
    assert completions == 1


def test_empty_first_contact_boundary_cannot_skip_first_unsupported_filing(sf, store):
    from sqlalchemy import func, select

    from filings.edgar.submissions_poll import PollBaselineStore
    from schemas.models.audit_entry import AuditEntry

    configured = Settings(
        sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
        sec_max_new_filings_per_poll=25,
    )
    audit = _audit_log(sf)
    assert PollBaselineStore(sf, audit).establish(NVDA, None, CLOCK.now())

    attacked = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=_MutatedSubmissionsFetcher(make_fetcher(FixtureTransport())),
        audit_log=audit, process_epoch="test-process",
        strict_completeness_ciks={NVDA},
    )

    assert attacked.poll_errors and not attacked.ok
    with sf() as session:
        completions = session.execute(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.event_type == "edgar_poll_completed"
            )
        ).scalar_one()
    assert completions == 0


def test_submissions_body_cik_cannot_escape_requested_cik_strict_scope(sf, store):
    from sqlalchemy import func, select

    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline

    configured = Settings(
        sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
        sec_max_new_filings_per_poll=25,
    )
    audit = _audit_log(sf)
    assert run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=audit,
        process_epoch="test-process",
    ).ok
    with sf() as session:
        cutoff = session.execute(select(PollBaseline.cutoff_accession_no)).scalar_one()

    attacked = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=_MutatedSubmissionsFetcher(
            make_fetcher(FixtureTransport()), body_cik=AAPL
        ),
        audit_log=audit, process_epoch="test-process",
        strict_completeness_ciks={NVDA},
    )

    assert attacked.poll_errors and not attacked.ok
    with sf() as session:
        assert session.execute(select(PollBaseline.cutoff_accession_no)).scalar_one() == cutoff
        completions = session.execute(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.event_type == "edgar_poll_completed"
            )
        ).scalar_one()
    assert completions == 1


def test_duplicate_saved_cutoff_cannot_hide_unsupported_newer_row(sf, store):
    from sqlalchemy import func, select

    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline

    configured = Settings(
        sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
        sec_max_new_filings_per_poll=25,
    )
    audit = _audit_log(sf)
    assert run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=audit,
        process_epoch="test-process",
    ).ok
    with sf() as session:
        cutoff = session.execute(select(PollBaseline.cutoff_accession_no)).scalar_one()

    attacked = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=_DuplicateBoundaryFetcher(make_fetcher(FixtureTransport()), cutoff),
        audit_log=audit, process_epoch="test-process",
        strict_completeness_ciks={NVDA},
    )

    assert attacked.poll_errors and not attacked.ok
    with sf() as session:
        assert session.execute(select(PollBaseline.cutoff_accession_no)).scalar_one() == cutoff
        completions = session.execute(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.event_type == "edgar_poll_completed"
            )
        ).scalar_one()
    assert completions == 1


def test_keyless_baseline_edit_cannot_be_laundered_by_a_later_poll(sf, store):
    from sqlalchemy import func, select, update

    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline

    configured = Settings(
        sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
        sec_max_new_filings_per_poll=25,
    )
    audit = _audit_log(sf)
    first = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=audit,
        process_epoch="test-process",
    )
    assert first.ok
    with sf() as attacker, attacker.begin():
        attacker.execute(
            update(PollBaseline).values(cutoff_accession_no="0001045810-26-999999")
        )

    attacked = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=audit,
        process_epoch="test-process",
    )

    assert attacked.poll_errors and not attacked.ok
    assert any("baseline" in reason.lower() for _cik, reason in attacked.poll_errors)
    with sf() as session:
        completions = session.execute(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.event_type == "edgar_poll_completed"
            )
        ).scalar_one()
    assert completions == 1 and audit.verify_chain().ok


def test_wal_baseline_substitution_during_poll_denies_completion(sf, store):
    from sqlalchemy import func, select, update

    from schemas.models.audit_entry import AuditEntry
    from schemas.models.pollbaseline import PollBaseline

    configured = Settings(
        sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
        sec_max_new_filings_per_poll=25,
    )
    audit = _audit_log(sf)
    assert run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=make_fetcher(FixtureTransport()), audit_log=audit,
        process_epoch="test-process",
    ).ok
    interleaved = {"fired": False}

    def attacker_commit():
        with sf() as attacker, attacker.begin():
            attacker.execute(
                update(PollBaseline).values(cutoff_accession_no="0001045810-26-999999")
            )
        interleaved["fired"] = True

    attacked = run_edgar_pipeline(
        sf, settings=configured, ciks=[NVDA], raw_store=store, clock=CLOCK,
        fetcher=_InterleavingSubmissionsFetcher(
            make_fetcher(FixtureTransport()), attacker_commit
        ),
        audit_log=audit, process_epoch="test-process",
        strict_completeness_ciks={NVDA},
    )

    assert interleaved["fired"] and attacked.poll_errors and not attacked.ok
    with sf() as session:
        completions = session.execute(
            select(func.count()).select_from(AuditEntry).where(
                AuditEntry.event_type == "edgar_poll_completed"
            )
        ).scalar_one()
    assert completions == 1 and audit.verify_chain().ok


def test_exact_historical_accession_backfill_bypasses_baseline_without_bypassing_audit(
    sf, store,
):
    from sqlalchemy import select

    from schemas.models.audit_entry import AuditEntry
    from schemas.models.filing import Filing

    audit = _audit_log(sf)
    accession = "0001045810-26-000060"
    row = backfill_filing_accession(
        sf, cik=NVDA, accession=accession,
        fetcher=make_fetcher(FixtureTransport()), raw_store=store,
        audit_log=audit, clock=CLOCK,
    )
    again = backfill_filing_accession(
        sf, cik=NVDA, accession=accession,
        fetcher=make_fetcher(FixtureTransport()), raw_store=store,
        audit_log=audit, clock=CLOCK,
    )

    assert again.id == row.id and store.verify(accession)
    with sf() as session:
        assert session.execute(
            select(Filing).where(Filing.accession_no == accession)
        ).scalar_one().primary_doc == "nvda-20260628.htm"
        events = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "filing_ingested")
        ).scalars().all()
    assert len(events) == 1
    assert events[0].payload["raw_content_hash"] == store.stored_hash(accession)
    assert events[0].payload["filing_date"] == row.filing_date.isoformat()
    assert events[0].payload["detection_ts"] == CLOCK.now().isoformat()
    assert audit.verify_chain().ok


def test_legacy_unsigned_filing_is_refetched_before_it_receives_a_signed_binding(sf, store):
    from datetime import date

    from sqlalchemy import select

    from schemas.models.audit_entry import AuditEntry
    from schemas.models.filing import Filing, FormType

    accession = "0001045810-26-000056"
    with sf() as session, session.begin():
        session.add(Filing(
            id="legacy-unsigned", cik=NVDA, accession_no=accession,
            form_type=FormType.FORM_8K, filing_date=date(2026, 6, 30),
            detection_ts=CLOCK.now(), raw_ref=accession,
            primary_doc="nvda-20260624.htm",
        ))
    audit = _audit_log(sf)
    row = backfill_filing_accession(
        sf, cik=NVDA, accession=accession,
        fetcher=make_fetcher(FixtureTransport()), raw_store=store,
        audit_log=audit, clock=CLOCK,
    )

    assert row.id == "legacy-unsigned" and store.verify(accession)
    with sf() as session:
        event = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == "filing_ingested")
        ).scalar_one()
    assert event.refs["row_id"] == "legacy-unsigned"
    assert event.payload["raw_content_hash"] == store.stored_hash(accession)


def test_unaudited_runs_keep_the_visible_cap_fault(sf, store):
    """Without an audit log a production-shaped baseline may not be written (the store
    refuses unaudited baseline writes), so the cap fault stays — and stays VISIBLE as a
    per-CIK error rather than becoming a silent unaudited baseline."""
    low_cap = Settings(sec_user_agent=UA, sec_rate_limit_per_sec=2.0,
                       sec_max_new_filings_per_poll=25)
    result = run(sf, store, low_cap)
    assert any("PollFilingLimitExceeded" in reason for _c, reason in result.poll_errors)


# ---------------------------------------------------------------------------------------
# BUG-083: same-day same-form "prior" must be the EARLIER-submitted filing
# ---------------------------------------------------------------------------------------
def test_same_day_prior_resolves_to_the_earlier_accession(sf):
    from datetime import date

    from sqlalchemy import select

    from ingest.edgar_pipeline import _prior_same_form
    from schemas.models.filing import Filing, FormType

    day = date(2026, 7, 8)
    earlier = Filing(cik="0000000001", accession_no="0000000001-26-000001",
                     form_type=FormType("8-K"), filing_date=day,
                     detection_ts=CLOCK.now(), raw_ref="a1")
    later = Filing(cik="0000000001", accession_no="0000000001-26-000002",
                   form_type=FormType("8-K"), filing_date=day,
                   detection_ts=CLOCK.now(), raw_ref="a2")
    with sf() as s, s.begin():
        s.add(earlier)
        s.add(later)

    with sf() as s:
        e = s.execute(select(Filing).where(Filing.accession_no.like("%000001"))).scalar_one()
        later_row = s.execute(select(Filing).where(Filing.accession_no.like("%000002"))).scalar_one()

    prior_of_later = _prior_same_form(sf, later_row)
    assert prior_of_later is not None
    assert prior_of_later.accession_no == e.accession_no

    # BUG-083: the earlier filing's "prior" used to resolve to the LATER-submitted one,
    # diffing it against a chronologically-later baseline. It must resolve to nothing.
    assert _prior_same_form(sf, e) is None
