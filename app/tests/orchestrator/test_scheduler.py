"""WP5 builder smoke tests for the scheduler wiring — the job callables are exercised
against a FrozenClock (no real time): the intraday guard skips off-hours, daily disarm drops
auto modes, and the not-wired workflows raise until WP8. ``build_scheduler`` registers four
jobs (skipped where APScheduler isn't installed)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator import scheduler as sch
from orchestrator.gate import GateResult
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory

OPEN = FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))    # 10:00 ET, open
CLOSED = FrozenClock(datetime(2026, 7, 7, 3, 0, tzinfo=timezone.utc))   # overnight, closed


@pytest.fixture
def state(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'sch.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=b"k", settings=Settings(), clock=OPEN)


def test_intraday_skips_when_market_closed(state):
    out = sch.run_intraday_loop(state, CLOSED, service=lambda s, c: "ran")
    assert out["skipped"] == "market_closed"


def test_intraday_dispatches_when_open(state):
    assert sch.run_intraday_loop(state, OPEN, service=lambda s, c: "ran") == "ran"


def test_anchor_head_noop_without_store(state):
    # SEC-11 cadence job is a safe no-op when no external witness is configured.
    assert state.anchor_store is None
    assert sch.run_anchor_head(state)["skipped"] == "no_anchor_store"


def test_anchor_head_publishes_verified_head(state):
    from bridge.anchor import InMemoryAnchorStore
    state.anchor_store = InMemoryAnchorStore()
    state.audit.append("mode_change", actor="op",
                       payload={"kind": "manual", "from": "read_only", "to": "paper"})
    out = sch.run_anchor_head(state)
    seq, head_hash = state.audit.head()
    assert out["anchored_seq"] == seq
    assert state.anchor_store.latest() == (seq, head_hash)


def test_not_wired_workflows_raise(state):
    with pytest.raises(NotImplementedError):
        sch.run_premarket_brew(state, OPEN)
    with pytest.raises(NotImplementedError):
        sch.run_eod_review(state, OPEN)
    with pytest.raises(NotImplementedError):
        sch.run_intraday_loop(state, OPEN)      # open but no service -> stub raises


def test_daily_disarm_drops_auto_to_review_live(state):
    lad = ModeLadder(state)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                      gate_result=GateResult(unlockable=True, target="auto_veto"), operator="op")
    assert lad.current() is Mode.auto_veto
    assert sch.run_daily_disarm(state, OPEN) is Mode.review_live
    assert lad.current() is Mode.review_live


def test_daily_disarm_noop_outside_auto(state):
    ModeLadder(state).set_mode(Mode.paper, actor="op")
    assert sch.run_daily_disarm(state, OPEN) is Mode.paper


def test_build_scheduler_registers_the_base_jobs(state):
    """No ingest registry -> the BASE set only. ``anchor_head`` joined it in the SEC-11
    hardening pass; RW-01's ``ingest_*`` jobs are registered per PROVISIONED feed and are
    covered by ``test_build_scheduler_registers_one_job_per_provisioned_feed``."""
    pytest.importorskip("apscheduler")
    scheduler = sch.build_scheduler(state, OPEN)
    ids = {j.id for j in scheduler.get_jobs()}
    assert ids == {"premarket_brew", "intraday_loop", "eod_review", "daily_disarm",
                   "anchor_head"}
    assert not scheduler.running        # returned un-started


def test_scheduler_jobs_have_operational_names(state, tmp_path):
    """Logs must identify the real job instead of seven indistinguishable lambdas."""
    pytest.importorskip("apscheduler")
    runner = _runner(state, tmp_path, ["calendar"])

    jobs = sch.build_scheduler(state, OPEN, ingest=runner).get_jobs()

    assert jobs
    assert {job.name for job in jobs} == {job.id for job in jobs}


# =======================================================================================
# RW-01 — ingest jobs (one per PROVISIONED feed, on its own cadence)
# =======================================================================================
def _runner(state, tmp_path, feeds):
    from ingest.calendars import CalendarPoller
    from orchestrator.ingest_runner import IngestRunner

    runner = IngestRunner(state, parquet_root=tmp_path / "raw", clock=OPEN,
                          monotonic=lambda: 0.0)
    for name in feeds:
        runner.add_poller(name, CalendarPoller(lambda: []))
    return runner


def test_build_scheduler_registers_one_job_per_provisioned_feed(state, tmp_path):
    pytest.importorskip("apscheduler")
    runner = _runner(state, tmp_path, ["finnhub", "sec:edgar"])
    jobs = {j.id: j for j in sch.build_scheduler(state, OPEN, ingest=runner).get_jobs()}

    assert "ingest_finnhub" in jobs and "ingest_sec:edgar" in jobs
    assert "ingest_finra" not in jobs, "an UNPROVISIONED feed must get no job at all"
    assert "ingest_calendar" not in jobs
    # each on its OWN cadence, not one global tick
    assert "minute='*/1'" in str(jobs["ingest_finnhub"].trigger)
    assert "minute='*/5'" in str(jobs["ingest_sec:edgar"].trigger)
    # the local brew read has no vendor quota and filings land at weekends -> not weekday-only
    assert "day_of_week='mon-fri'" in str(jobs["ingest_finnhub"].trigger)
    assert "day_of_week='mon-fri'" not in str(jobs["ingest_sec:edgar"].trigger)


def test_each_ingest_job_polls_its_own_feed(state, tmp_path):
    """Regression: a lambda closing over the LOOP VARIABLE would make every registered job poll
    the last feed. Each job must be bound to its own."""
    pytest.importorskip("apscheduler")
    runner = _runner(state, tmp_path, ["finnhub", "sec:edgar", "calendar"])
    polled = []
    runner.poll = lambda feed: polled.append(feed)       # type: ignore[assignment]

    for job in sch.build_scheduler(state, OPEN, ingest=runner).get_jobs():
        if job.id.startswith("ingest_"):
            job.func()
    assert sorted(polled) == ["calendar", "finnhub", "sec:edgar"]


def test_run_ingest_poll_without_a_runner_is_an_honest_skip(state):
    assert sch.run_ingest_poll(state, OPEN, feed="finnhub") == {
        "feed": "finnhub", "skipped": "no_ingest_runner"}


def test_run_ingest_poll_reports_a_sick_feed_instead_of_raising(state, tmp_path):
    """One sick vendor must not take down the scheduler thread — the fault is REPORTED."""
    from orchestrator.ingest_runner import IngestRunner

    class Sick:
        def poll(self, now, on_event):
            raise RuntimeError("vendor 500")

    runner = IngestRunner(state, parquet_root=tmp_path / "raw", clock=OPEN,
                          monotonic=lambda: 0.0)
    runner.add_poller("finnhub", Sick())
    out = sch.run_ingest_poll(state, OPEN, feed="finnhub", runner=runner)
    assert out["status"] == "error" and "vendor 500" in out["error"]


def test_ingest_poll_runs_even_when_the_market_is_closed(state, tmp_path):
    """Filings, calendar revisions and news land outside the session; ingest is awareness, not
    execution, so it must NOT inherit the intraday market-open guard."""
    from ingest.calendars import CalendarPoller
    from orchestrator.ingest_runner import IngestRunner

    runner = IngestRunner(state, parquet_root=tmp_path / "raw", clock=CLOSED,
                          monotonic=lambda: 0.0)
    runner.add_poller("calendar", CalendarPoller(
        lambda: [{"symbol": "AAPL", "date": "2026-07-30", "note": "Q3"}]))
    out = sch.run_ingest_poll(state, CLOSED, feed="calendar", runner=runner)
    assert out["status"] == "ok" and out["emitted"] == 1
