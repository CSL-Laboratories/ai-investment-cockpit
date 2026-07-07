"""WP5 self-tests — proving the deterministic orchestrator core compiles and the happy
paths + the load-bearing refusals hold. These are NOT the full G5 acceptance suite (a
separate agent owns that); they are the builder's own smoke + invariant checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator import breakers as bk
from orchestrator import market_calendar as mc
from orchestrator.gate import GateResult, GateThresholds, compute_gate
from orchestrator.killswitch import halt_is_set, killswitch
from orchestrator.mode_ladder import (
    ArmResult,
    ExecutionRuntimeError,
    IllegalTransition,
    ModeLadder,
    assert_execution_runtime,
    legal_transition,
)
from orchestrator.state import EVENT_ADJUDICATION, OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"wp5-unit-key"


def _clock(dt=None):
    return FrozenClock(dt or datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'wp5.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=_clock())


# ---- market calendar (OPS-04) ---------------------------------------------------------
def test_market_open_regular_and_closed():
    # 2026-07-07 is a Tuesday. 13:30 UTC == 09:30 ET (EDT, open edge).
    assert mc.is_market_open(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))
    assert not mc.is_market_open(datetime(2026, 7, 7, 13, 0, tzinfo=timezone.utc))  # 09:00 ET
    assert not mc.is_market_open(datetime(2026, 7, 7, 20, 0, tzinfo=timezone.utc))  # 16:00 ET edge
    # Saturday
    assert not mc.is_market_open(datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc))
    # Christmas (full holiday)
    assert not mc.is_market_open(datetime(2026, 12, 25, 15, 0, tzinfo=timezone.utc))


def test_early_close_half_day():
    # 2026-12-24 is a half day: closes 13:00 ET (18:00 UTC in EST).
    assert mc.is_market_open(datetime(2026, 12, 24, 17, 0, tzinfo=timezone.utc))       # 12:00 ET
    assert not mc.is_market_open(datetime(2026, 12, 24, 18, 30, tzinfo=timezone.utc))  # 13:30 ET
    assert mc.is_early_close(mc.to_eastern(datetime(2026, 12, 24, 17, 0, tzinfo=timezone.utc)).date())


def test_session_close_disarm_target():
    close = mc.session_close(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))
    assert (close.hour, close.minute) == (16, 0)
    assert mc.to_eastern(close).date().isoformat() == "2026-07-07"
    with pytest.raises(ValueError):
        mc.is_market_open(datetime(2026, 7, 7, 14, 0))  # naive rejected


# ---- ladder adjacency -----------------------------------------------------------------
def test_legal_transition_adjacency():
    assert legal_transition(Mode.read_only, Mode.paper)
    assert legal_transition(Mode.paper, Mode.review_live)
    assert legal_transition(Mode.review_live, Mode.auto_veto)
    assert legal_transition(Mode.auto_veto, Mode.auto_full)
    assert legal_transition(Mode.auto_full, Mode.read_only)      # any drop down
    assert legal_transition(Mode.auto_full, Mode.review_live)
    assert not legal_transition(Mode.read_only, Mode.review_live)  # skip-up illegal
    assert not legal_transition(Mode.review_live, Mode.auto_full)  # skip-up illegal
    assert not legal_transition(Mode.paper, Mode.paper)            # equal is not a move


def test_set_mode_walks_up_and_rejects_illegal(st):
    lad = ModeLadder(st)
    assert lad.current() is Mode.read_only
    assert lad.set_mode(Mode.paper, actor="op") is Mode.paper
    assert lad.set_mode(Mode.review_live, actor="op") is Mode.review_live
    assert lad.current() is Mode.review_live
    with pytest.raises(IllegalTransition):
        lad.set_mode(Mode.auto_veto, actor="op")     # auto entry needs arm_auto_mode
    # drop down is fine, skipping rungs
    assert lad.set_mode(Mode.read_only, actor="op") is Mode.read_only


# ---- arming (Inv 2 + gate) ------------------------------------------------------------
def _good_gate(target):
    return GateResult(unlockable=True, target=target)


def test_arm_refuses_without_gate(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    res = lad.arm_auto_mode(Mode.auto_veto, gate_result=None, operator="op")
    assert isinstance(res, ArmResult) and not res.armed
    assert lad.current() is Mode.review_live         # nothing persisted


def test_arm_refuses_non_claude_decision_stage(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    res = lad.arm_auto_mode(
        Mode.auto_veto, decision_stage_runtimes=["claude", "codex"],
        gate_result=_good_gate("auto_veto"), operator="op",
    )
    assert not res.armed and any("Inv 2" in r for r in res.reasons)
    assert lad.current() is Mode.review_live


def test_arm_happy_path_sets_armed_window(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    res = lad.arm_auto_mode(
        Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
        gate_result=_good_gate("auto_veto"), operator="lucas",
    )
    assert res.armed and lad.current() is Mode.auto_veto
    row = lad.current_row()
    assert row.armed_by == "lucas" and row.armed_at is not None and row.armed_until is not None
    assert (row.armed_until.hour, row.armed_until.minute) == (16, 0)   # disarm at close


def test_arm_full_requires_being_in_auto_veto(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    # cannot jump review_live -> auto_full (skip-up)
    res = lad.arm_auto_mode(Mode.auto_full, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate("auto_full"), operator="op")
    assert not res.armed


# ---- SEC-08 execution runtime pin -----------------------------------------------------
def test_assert_execution_runtime():
    assert_execution_runtime(Mode.review_live, "codex")     # allowed outside auto
    assert_execution_runtime(Mode.auto_full, "claude")      # allowed
    with pytest.raises(ExecutionRuntimeError):
        assert_execution_runtime(Mode.auto_full, "codex")
    with pytest.raises(ExecutionRuntimeError):
        assert_execution_runtime(Mode.auto_veto, "openai")


# ---- SEC-23 fail-closed recovery ------------------------------------------------------
def test_recover_lands_on_review_live_never_prior_auto(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                      gate_result=_good_gate("auto_veto"), operator="op")
    assert lad.current() is Mode.auto_veto
    landed = lad.recover(prev_state=Mode.auto_veto)
    assert landed is Mode.review_live and lad.current() is Mode.review_live


def test_daily_disarm_only_affects_auto(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    assert lad.daily_disarm() is Mode.paper                 # no-op outside auto
    lad.set_mode(Mode.review_live, actor="op")
    lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                      gate_result=_good_gate("auto_veto"), operator="op")
    assert lad.daily_disarm() is Mode.review_live


# ---- breakers (L6) --------------------------------------------------------------------
def test_breaker_trip_drops_to_review_live(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                      gate_result=_good_gate("auto_veto"), operator="op")
    eng = bk.BreakerEngine(st)
    sig = bk.BreakerSignals(realized_loss_today=Decimal("999"))
    events = eng.evaluate_and_trip(sig)
    assert any(e.kind == "breaker_trip" for e in events)
    assert lad.current() is Mode.review_live
    assert events[0].mode_after == "review_live"


def test_pure_predicates_boundaries():
    th = bk.BreakerThresholds()
    assert bk.breach_consecutive_losses(3, 3) and not bk.breach_consecutive_losses(2, 3)
    assert not bk.breach_schema_failures(2, 4, 0.25, 8)      # below min sample
    assert bk.breach_schema_failures(5, 10, 0.25, 8)
    assert bk.breach_stale_data(121.0, th.data_staleness_s)
    assert not bk.breach_stale_data(120.0, th.data_staleness_s)


# ---- SEC-01 synchronous injection breaker ---------------------------------------------
def test_injection_breaker_synchronous_disarm(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                      gate_result=_good_gate("auto_veto"), operator="op")
    eng = bk.BreakerEngine(st)
    assert eng.check_order_path(predicate=lambda c: False) is None       # clean
    ev = eng.check_order_path(predicate=lambda c: True)                  # detected
    assert ev is not None and ev.breaker == bk.BRK_INJECTION
    assert lad.current() is Mode.review_live


# ---- L8 watch-only (separate from L6) + SEC-23 generation -----------------------------
def test_watch_only_does_not_drop_mode_and_bumps_generation(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                      gate_result=_good_gate("auto_veto"), operator="op")
    eng = bk.BreakerEngine(st)
    gen0 = eng.current_generation("claude")
    eng.enter_watch_only("claude", "limit hit mid-loop")
    assert eng.is_watch_only("claude") and eng.is_watch_only()
    assert eng.current_generation("claude") == gen0 + 1
    assert lad.current() is Mode.auto_veto              # L8 does NOT drop mode
    with pytest.raises(bk.StaleAnalysisError):
        eng.assert_fresh_analysis("claude", gen0)       # pre-exhaustion analysis refused
    # recovery fails closed to review_live, never the prior auto mode
    _, mode_after = eng.recover_from_watch_only("claude", prev_state=Mode.auto_veto)
    assert mode_after is Mode.review_live and not eng.is_watch_only("claude")


# ---- killswitch (SEC-04 ordering + sentinel) ------------------------------------------
def test_killswitch_sets_halt_and_disarms(st, tmp_path):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                      gate_result=_good_gate("auto_veto"), operator="op")
    halt = tmp_path / "HALT"
    calls = []
    res = killswitch(st, actor="lucas", reason="panic", halt_path=halt,
                     cancel_hook=lambda: (calls.append(1) or 3))
    assert halt_is_set(halt)
    assert res.cancelled_count == 3
    assert res.mode_after is Mode.review_live and lad.current() is Mode.review_live


# ---- gate (SEC-20) --------------------------------------------------------------------
def _adjudicate(st, decision, n=1):
    for _ in range(n):
        st.audit.append(EVENT_ADJUDICATION, actor="lucas", payload={"decision": decision})


def test_gate_fails_closed_on_broken_chain(st):
    st.audit.append("mode_change", payload={"kind": "manual", "to": "paper", "from": "read_only"})
    # tamper: corrupt a row so verify_chain fails
    from sqlalchemy import select

    from schemas.models.audit_entry import AuditEntry
    with st.session_factory() as s, s.begin():
        r = s.execute(select(AuditEntry).where(AuditEntry.seq == 1)).scalar_one()
        r.payload = {**r.payload, "injected": "x"}
    res = compute_gate(st, Mode.auto_veto)
    assert not res.unlockable and res.metrics.get("chain_ok") is False


def test_gate_unlockable_when_criteria_met(st):
    # advance the clock 5 weeks so the mode-timeline shows >=4 weeks in review_live
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    st.clock = FrozenClock(start)
    st.audit._clock = st.clock
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op", clock=FrozenClock(start))
    lad.set_mode(Mode.review_live, actor="op", clock=FrozenClock(start))
    _adjudicate(st, "approve", 28)
    _adjudicate(st, "reject", 2)                     # 28/30 approvals = 93%
    now = FrozenClock(start + timedelta(weeks=5)).now()
    res = compute_gate(st, Mode.auto_veto, now=now, thresholds=GateThresholds())
    assert res.unlockable, res.reasons
    assert res.metrics["adjudicated"] == 30 and res.metrics["approvals"] == 28


def test_gate_blocks_on_bypass_event(st):
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    st.clock = FrozenClock(start)
    st.audit._clock = st.clock
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op", clock=FrozenClock(start))
    lad.set_mode(Mode.review_live, actor="op", clock=FrozenClock(start))
    _adjudicate(st, "approve", 30)
    st.audit.append("hard_check_failed", payload={"proposal_id": "p1"})   # bypass attempt
    now = FrozenClock(start + timedelta(weeks=5)).now()
    res = compute_gate(st, Mode.auto_veto, now=now)
    assert not res.unlockable and any("bypass" in r for r in res.reasons)
