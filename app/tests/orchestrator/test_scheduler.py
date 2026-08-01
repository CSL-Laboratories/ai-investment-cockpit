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
