"""G5 ACCEPTANCE SUITE — the mode-ladder guard (playbook §5 G5; Inv 1/2/7).

Authored by an INDEPENDENT test-author (playbook §9: the implementer must never be the
sole author of the tests that guard the mode ladder). Every test is deterministic — a
tmp SQLite DB + a ``FrozenClock`` — with no wall-clock and no network.

Mapping (criterion -> test_id) is reported in g5_coverage. The core criteria are:
  1. illegal transitions rejected / legal accepted / any rung -> review_live
  2. performance gate enforced (below-threshold refused; reads the VERIFIED chain, not
     mutable domain tables; broken chain fails closed) — SEC-20
  3. auto-arm Claude-runtime pin (Inv 2) + SEC-08 execution-runtime refusal
  4. every breaker trip -> review_live (+event/row/audit); usage exhaustion -> watch-only
  5. fail-closed recovery -> review_live, never a silent re-arm (SEC-23)
  6. scheduler on a simulated clock (market-open gating + daily disarm)
  7. killswitch sets the HALT sentinel + disarms + audits (SEC-04)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from config.settings import Mode, Settings
from core.clock import FrozenClock
from core.ids import new_id
from orchestrator import breakers as bk
from orchestrator import market_calendar as mc
from orchestrator import scheduler as sch
from orchestrator.gate import GateResult, GateThresholds, compute_gate
from orchestrator.killswitch import halt_is_set, killswitch
from orchestrator.mode_ladder import (
    ExecutionRuntimeError,
    IllegalTransition,
    ModeLadder,
    assert_execution_runtime,
    default_decision_stage_runtimes,
    legal_transition,
)
from orchestrator.state import (
    EVENT_ADJUDICATION,
    EVENT_BREAKER_TRIP,
    EVENT_KILLSWITCH,
    EVENT_MODE_CHANGE,
    EVENT_WATCH_ONLY,
    OrchestratorState,
)
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.breaker_state import BreakerState
from storage.engine import create_all, make_engine, make_session_factory
from storage.repositories import Repo

KEY = b"g5-acceptance-key"
# 2026-07-07 is a Tuesday; 14:00 UTC == 10:00 ET (EDT), inside the regular session.
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


def _clock(dt=None):
    return FrozenClock(dt or OPEN_UTC)


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'g5.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=_clock())


# ---- helpers ---------------------------------------------------------------------------
def _good_gate(target="auto_veto"):
    return GateResult(unlockable=True, target=target)


def _to_review_live(lad):
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")


def _arm_veto(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate("auto_veto"), operator="op")
    assert res.armed and lad.current() is Mode.auto_veto
    return lad


def _arm_full(st):
    lad = _arm_veto(st)
    res = lad.arm_auto_mode(Mode.auto_full, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate("auto_full"), operator="op")
    assert res.armed and lad.current() is Mode.auto_full
    return lad


def _audit(st, event_type=None):
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
    return [r for r in rows if event_type is None or r.event_type == event_type]


def _breaker_rows(st):
    with st.session_factory() as s:
        return s.execute(select(BreakerState).order_by(BreakerState.created_at)).scalars().all()


# =======================================================================================
# CRITERION 1 — illegal transitions rejected; legal accepted; any rung -> review_live
# =======================================================================================
def test_g5_1_illegal_transitions_pure_predicate():
    # every legal single-rung step up
    assert legal_transition(Mode.read_only, Mode.paper)
    assert legal_transition(Mode.paper, Mode.review_live)
    assert legal_transition(Mode.review_live, Mode.auto_veto)
    assert legal_transition(Mode.auto_veto, Mode.auto_full)
    # every non-adjacent skip-up is illegal
    assert not legal_transition(Mode.read_only, Mode.review_live)
    assert not legal_transition(Mode.read_only, Mode.auto_veto)
    assert not legal_transition(Mode.read_only, Mode.auto_full)
    assert not legal_transition(Mode.paper, Mode.auto_veto)
    assert not legal_transition(Mode.paper, Mode.auto_full)
    assert not legal_transition(Mode.review_live, Mode.auto_full)
    # equal is not a transition
    assert not legal_transition(Mode.paper, Mode.paper)
    # any drop DOWN is legal (breakers / killswitch / recovery)
    for src in (Mode.paper, Mode.review_live, Mode.auto_veto, Mode.auto_full):
        assert legal_transition(src, Mode.read_only)
    assert legal_transition(Mode.auto_full, Mode.review_live)


def test_g5_1_set_mode_rejects_skip_up(st):
    lad = ModeLadder(st)
    assert lad.current() is Mode.read_only
    with pytest.raises(IllegalTransition):
        lad.set_mode(Mode.review_live, actor="op")     # read_only -> review_live skips paper
    assert lad.current() is Mode.read_only              # nothing persisted


def test_g5_1_set_mode_refuses_auto_entry(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    with pytest.raises(IllegalTransition):
        lad.set_mode(Mode.auto_veto, actor="op")        # auto entry only via arm_auto_mode
    assert lad.current() is Mode.review_live


def test_g5_1_legal_walk_up_accepted(st):
    lad = ModeLadder(st)
    assert lad.set_mode(Mode.paper, actor="op") is Mode.paper
    assert lad.set_mode(Mode.review_live, actor="op") is Mode.review_live
    assert lad.current() is Mode.review_live


def test_g5_1_arm_refuses_skip_up_to_auto_full(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    # review_live -> auto_full skips auto_veto; refused even with a good gate + claude pins.
    res = lad.arm_auto_mode(Mode.auto_full, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate("auto_full"), operator="op")
    assert not res.armed and lad.current() is Mode.review_live


def test_g5_1_downshift_to_review_live_from_any_rung(st):
    # from the very top rung, a drop lands on review_live.
    lad = _arm_full(st)
    assert lad.drop_to_review_live("test drop") is Mode.review_live
    assert lad.current() is Mode.review_live
    # re-arm to auto_veto and drop again — a mid-rung down-shift also lands on review_live.
    lad2 = _arm_veto(st)
    assert lad2.drop_to_review_live("test drop") is Mode.review_live


def test_g5_1_manual_downshift_can_skip_rungs(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    # review_live -> read_only skips paper on the way DOWN — legal.
    assert lad.set_mode(Mode.read_only, actor="op") is Mode.read_only


# =======================================================================================
# CRITERION 2 — performance gate enforced (SEC-20)
# =======================================================================================
def test_g5_2_gate_refuses_below_threshold(st, wire_anchor):
    # a barely-seeded state (0 adjudications, 0 weeks) WITH a satisfied anchor -> refused with
    # metric-backed reasons. The anchor is required just to REACH the metric logic (SEC-11:
    # compute_gate hard-requires the external witness for an auto unlock).
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    _seed_lower_history(st, start, approvals=0)
    wire_anchor(st)
    res = compute_gate(st, Mode.auto_veto)
    assert not res.unlockable
    assert any("weeks" in r for r in res.reasons)
    assert any("adjudicated" in r for r in res.reasons)


def _seed_lower_history(st, start, *, approvals, rejects=0):
    """Seed the VERIFIED chain: paper/review_live at ``start`` + N audited adjudications."""
    st.clock = FrozenClock(start)
    st.audit._clock = st.clock
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op", clock=FrozenClock(start))
    lad.set_mode(Mode.review_live, actor="op", clock=FrozenClock(start))
    for _ in range(approvals):
        st.audit.append(EVENT_ADJUDICATION, actor="op", payload={"decision": "approve"})
    for _ in range(rejects):
        st.audit.append(EVENT_ADJUDICATION, actor="op", payload={"decision": "reject"})
    return lad


def test_g5_2_gate_unlockable_when_all_thresholds_met(st, wire_anchor):
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    _seed_lower_history(st, start, approvals=28, rejects=2)   # 28/30 = 93% >= 80%
    wire_anchor(st)                                           # SEC-11 external witness satisfied
    now = FrozenClock(start + timedelta(weeks=5)).now()       # >= 4 weeks in lower
    res = compute_gate(st, Mode.auto_veto, now=now, thresholds=GateThresholds())
    assert res.unlockable, res.reasons
    assert res.metrics["adjudicated"] == 30 and res.metrics["chain_ok"] is True
    assert res.metrics["anchor_ok"] is True


def test_g5_2_gate_refuses_low_approval_rate(st, wire_anchor):
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    _seed_lower_history(st, start, approvals=20, rejects=15)  # 20/35 = 57% < 80%
    wire_anchor(st)
    now = FrozenClock(start + timedelta(weeks=5)).now()
    res = compute_gate(st, Mode.auto_veto, now=now)
    assert not res.unlockable and any("approval rate" in r for r in res.reasons)


def test_g5_2_gate_refuses_drawdown_over_limit(st, wire_anchor):
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    _seed_lower_history(st, start, approvals=30)
    st.audit.append("drawdown_mark", actor="op", payload={"drawdown": "0.35"})  # > 0.20
    wire_anchor(st)
    now = FrozenClock(start + timedelta(weeks=5)).now()
    res = compute_gate(st, Mode.auto_veto, now=now)
    assert not res.unlockable and any("drawdown" in r for r in res.reasons)


def test_g5_2_gate_blocks_on_bypass_event(st, wire_anchor):
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    _seed_lower_history(st, start, approvals=30)
    st.audit.append("hard_check_failed", actor="op", payload={"proposal_id": "p1"})
    wire_anchor(st)
    now = FrozenClock(start + timedelta(weeks=5)).now()
    res = compute_gate(st, Mode.auto_veto, now=now)
    assert not res.unlockable and any("bypass" in r for r in res.reasons)


def test_g5_2_gate_ignores_fabricated_domain_rows(st, wire_anchor):
    """SEC-20: rows fabricated in a MUTABLE domain table (approval_record) do NOT move the
    gate — it reads only the audited, HMAC-anchored adjudication events."""
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    _seed_lower_history(st, start, approvals=20)   # only 20 audited -> below the 30 floor
    wire_anchor(st)
    now = FrozenClock(start + timedelta(weeks=5)).now()
    before = compute_gate(st, Mode.auto_veto, now=now)
    assert not before.unlockable and before.metrics["adjudicated"] == 20

    # fabricate 30 "approved" rows directly in the domain table (no audit coverage).
    repo = Repo(st.session_factory, ApprovalRecord)
    when = FrozenClock(start).now()
    for i in range(30):
        repo.add(id=new_id("appr"), proposal_id=f"p{i}", risk_decision_id=f"r{i}",
                 actor_user_id="attacker", user_decision="approve", surface="discord",
                 decided_at=when, created_at=when)

    after = compute_gate(st, Mode.auto_veto, now=now)
    assert not after.unlockable                       # still refused
    assert after.metrics["adjudicated"] == 20         # fabrication moved NOTHING
    assert any("adjudicated" in r for r in after.reasons)


def test_g5_2_gate_fails_closed_on_broken_chain(st):
    st.audit.append("mode_change", actor="op",
                    payload={"kind": "manual", "to": "paper", "from": "read_only"})
    with st.session_factory() as s, s.begin():
        r = s.execute(select(AuditEntry).where(AuditEntry.seq == 1)).scalar_one()
        r.payload = {**r.payload, "injected": "x"}     # tamper -> verify_chain fails
    res = compute_gate(st, Mode.auto_veto)
    assert not res.unlockable and res.metrics.get("chain_ok") is False


def test_g5_2_auto_full_requires_weeks_in_auto_veto(st, wire_anchor):
    # meets all auto_veto criteria but has 0 weeks in auto_veto -> auto_full refused.
    start = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    _seed_lower_history(st, start, approvals=30)
    wire_anchor(st)
    now = FrozenClock(start + timedelta(weeks=5)).now()
    res = compute_gate(st, Mode.auto_full, now=now)
    assert not res.unlockable and any("auto_veto" in r for r in res.reasons)


# =======================================================================================
# CRITERION 3 — auto-arm Claude-runtime pin (Inv 2) + SEC-08
# =======================================================================================
def test_g5_3_arm_refuses_non_claude_decision_stage_explicit(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "codex"],
                            gate_result=_good_gate("auto_veto"), operator="op")
    assert not res.armed and any("Inv 2" in r for r in res.reasons)
    assert lad.current() is Mode.review_live           # nothing persisted


def test_g5_3_arm_refuses_deployment_configured_for_codex_paper_decisions(st):
    from config.settings import DecisionRuntime

    lad = ModeLadder(st)
    _to_review_live(lad)
    # Simulate changing provider config while a prior Claude deployment left review_live
    # persisted. Arm-time resolution must still use the current Codex selection and refuse.
    st.settings.decision_runtime = DecisionRuntime.codex
    res = lad.arm_auto_mode(
        Mode.auto_veto, gate_result=_good_gate("auto_veto"), operator="op"
    )
    assert not res.armed
    assert any("not claude" in reason for reason in res.reasons)
    assert lad.current() is Mode.review_live


def test_g5_3_arm_refuses_when_model_map_configures_codex_decision_stage(st, monkeypatch):
    """Simulate a codex-configured decision stage in the model map: the default runtime
    resolver returns codex for 'deep'. arm_auto_mode (default runtimes) must refuse."""
    from orchestrator import mode_ladder as ml
    real = ml.runtime_for

    def fake(alias, selected_runtime=None):
        return "codex" if alias == "deep" else real(alias, selected_runtime)

    monkeypatch.setattr(ml, "runtime_for", fake)
    assert "codex" in default_decision_stage_runtimes()   # sanity: the sim took effect
    lad = ModeLadder(st)
    _to_review_live(lad)
    res = lad.arm_auto_mode(Mode.auto_veto, gate_result=_good_gate("auto_veto"), operator="op")
    assert not res.armed and any("Inv 2" in r for r in res.reasons)
    assert lad.current() is Mode.review_live


def test_g5_3_arm_accepted_when_both_stages_claude(st):
    lad = ModeLadder(st)
    _to_review_live(lad)
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate("auto_veto"), operator="lucas")
    assert res.armed and lad.current() is Mode.auto_veto
    row = lad.current_row()
    assert row.armed_by == "lucas" and row.armed_until is not None


def test_g5_3_default_decision_stage_runtimes_are_claude():
    assert default_decision_stage_runtimes() == ["claude", "claude"]


def test_g5_3_sec08_execution_runtime_refused_even_after_valid_arm(st):
    lad = _arm_veto(st)                                 # a fully valid arm
    # SEC-08: an auto-submit whose producing runtime != claude is still refused.
    with pytest.raises(ExecutionRuntimeError):
        assert_execution_runtime(Mode.auto_veto, "codex")
    with pytest.raises(ExecutionRuntimeError):
        lad.assert_execution_runtime(Mode.auto_full, "openai")
    assert_execution_runtime(Mode.auto_full, "claude")  # claude allowed
    with pytest.raises(ExecutionRuntimeError):
        assert_execution_runtime(Mode.review_live, "codex")  # live remains Claude-only


def test_g5_3_codex_configuration_cannot_enter_review_live(st):
    from config.settings import DecisionRuntime

    st.settings.decision_runtime = DecisionRuntime.codex
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    with pytest.raises(IllegalTransition, match="requires COCKPIT_DECISION_RUNTIME=claude"):
        lad.set_mode(Mode.review_live, actor="op")
    assert lad.current() is Mode.paper


# =======================================================================================
# CRITERION 4 — every breaker trip -> review_live; usage exhaustion -> watch-only
# =======================================================================================
_BREAKER_CASES = [
    (bk.BRK_DAILY_LOSS, bk.BreakerSignals(realized_loss_today=Decimal("600"))),
    (bk.BRK_CONSECUTIVE_LOSSES, bk.BreakerSignals(consecutive_losses=3)),
    (bk.BRK_SCHEMA_FAILURES, bk.BreakerSignals(schema_failures=5, schema_validations=10)),
    (bk.BRK_BROKER_ERRORS, bk.BreakerSignals(broker_errors=5)),
]


@pytest.mark.parametrize("name,signals", _BREAKER_CASES, ids=[c[0] for c in _BREAKER_CASES])
def test_g5_4_each_breaker_trip_drops_to_review_live(st, name, signals):
    lad = _arm_veto(st)
    assert lad.current() is Mode.auto_veto
    eng = bk.BreakerEngine(st)

    events = eng.evaluate_and_trip(signals)

    # exactly this breaker tripped, with a #system event carrying the post-drop mode.
    tripped_names = {e.breaker for e in events}
    assert tripped_names == {name}, tripped_names
    ev = events[0]
    assert ev.kind == "breaker_trip" and ev.mode_after == "review_live"
    # instant drop to review_live.
    assert lad.current() is Mode.review_live
    # a persisted BreakerState row.
    rows = [r for r in _breaker_rows(st) if r.breaker_name == name]
    assert rows and rows[-1].tripped is True
    # a covering AuditEntry that references that row (Inv 6).
    trip_audits = [a for a in _audit(st, EVENT_BREAKER_TRIP)
                   if (a.payload or {}).get("breaker") == name]
    assert trip_audits
    assert any((a.refs or {}).get("row_id") == rows[-1].id for a in trip_audits)
    # the chain remains verifiable across the state mutation.
    assert st.audit.verify_chain().ok


def test_g5_4_injection_breaker_synchronous_drop(st):
    lad = _arm_veto(st)
    eng = bk.BreakerEngine(st)
    assert eng.check_order_path(predicate=lambda c: False) is None    # clean, no drop
    assert lad.current() is Mode.auto_veto
    ev = eng.check_order_path(predicate=lambda c: True)               # anomaly detected
    assert ev is not None and ev.breaker == bk.BRK_INJECTION
    assert lad.current() is Mode.review_live
    assert st.audit.verify_chain().ok


def test_g5_4_stale_market_data_goes_watch_only_not_review_live(st):
    lad = _arm_veto(st)
    eng = bk.BreakerEngine(st)

    events = eng.evaluate_and_trip(bk.BreakerSignals(max_data_age_s=121.0))

    assert len(events) == 1 and events[0].kind == "watch_only"
    assert eng.is_watch_only("market_data") is True
    assert lad.current() is Mode.auto_veto
    assert not [r for r in _breaker_rows(st) if r.breaker_name == bk.BRK_STALE_DATA]
    assert st.audit.verify_chain().ok


def test_g5_4_usage_exhaustion_goes_watch_only_not_review_live(st):
    """L8 (usage-window exhaustion) is DISTINCT from L6: it disables the order path and
    bumps the generation but does NOT drop the mode; the degradation is audited (never
    silently dropped) and pre-exhaustion analysis is refused (SEC-23)."""
    lad = _arm_veto(st)
    eng = bk.BreakerEngine(st)
    gen0 = eng.current_generation("claude")
    assert not eng.is_watch_only()

    ev = eng.enter_watch_only("claude", "429 rate cap hit mid-loop")

    assert ev.kind == "watch_only"
    assert eng.is_watch_only("claude") and eng.is_watch_only()        # order path disabled
    assert eng.current_generation("claude") == gen0 + 1              # generation bumped
    assert lad.current() is Mode.auto_veto                           # NOT dropped (L8, not L6)
    # the degradation was recorded (never dropped) and the chain still verifies.
    watch_audits = _audit(st, EVENT_WATCH_ONLY)
    assert watch_audits and (watch_audits[-1].payload or {}).get("watch_only") is True
    assert st.audit.verify_chain().ok
    # pre-exhaustion analysis is refused after the bump (SEC-23).
    with pytest.raises(bk.StaleAnalysisError):
        eng.assert_fresh_analysis("claude", gen0)
    eng.assert_fresh_analysis("claude", gen0 + 1)                    # current gen accepted


# =======================================================================================
# CRITERION 5 — fail-closed recovery -> review_live, never a silent re-arm (SEC-23)
# =======================================================================================
def test_g5_5_recover_lands_review_live_never_prior_auto(st):
    lad = _arm_full(st)
    assert lad.current() is Mode.auto_full
    landed = lad.recover(prev_state=Mode.auto_full)
    assert landed is Mode.review_live and lad.current() is Mode.review_live
    # the refused-to-restore mode is only RECORDED, never applied.
    rec = [a for a in _audit(st, EVENT_MODE_CHANGE)
           if (a.payload or {}).get("kind") == "recover"]
    assert rec and rec[-1].payload.get("refused_restore") == "auto_full"


def test_g5_5_recover_from_watch_only_fails_closed(st):
    lad = _arm_veto(st)
    eng = bk.BreakerEngine(st)
    eng.enter_watch_only("claude", "exhausted")
    _, mode_after = eng.recover_from_watch_only("claude", prev_state=Mode.auto_veto)
    assert mode_after is Mode.review_live and lad.current() is Mode.review_live
    assert not eng.is_watch_only("claude")


def test_g5_5_recovery_requires_explicit_rearm(st):
    """After a breaker drop, getting back to an auto mode requires an explicit gated
    arm — a bare set_mode into auto is refused (no silent re-arm)."""
    lad = _arm_veto(st)
    bk.BreakerEngine(st).trip(bk.BRK_DAILY_LOSS, "loss limit")
    assert lad.current() is Mode.review_live
    with pytest.raises(IllegalTransition):
        lad.set_mode(Mode.auto_veto, actor="op")       # cannot silently re-arm
    # an explicit gated arm is the only way back up.
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=_good_gate("auto_veto"), operator="op")
    assert res.armed and lad.current() is Mode.auto_veto


# =======================================================================================
# CRITERION 6 — scheduler on a simulated clock
# =======================================================================================
def test_g5_6_intraday_dispatches_during_session(st):
    out = sch.run_intraday_loop(st, FrozenClock(OPEN_UTC), service=lambda s, c: "ran")
    assert out == "ran"


@pytest.mark.parametrize("dt,label", [
    (datetime(2026, 7, 7, 13, 0, tzinfo=timezone.utc), "pre_open_0900_ET"),
    (datetime(2026, 7, 7, 20, 30, tzinfo=timezone.utc), "after_close_1630_ET"),
    (datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc), "saturday"),
    (datetime(2026, 12, 25, 15, 0, tzinfo=timezone.utc), "christmas_holiday"),
])
def test_g5_6_intraday_skips_when_market_closed(st, dt, label):
    out = sch.run_intraday_loop(st, FrozenClock(dt), service=lambda s, c: "ran")
    assert out["skipped"] == "market_closed", label


def test_g5_6_daily_disarm_drops_auto_at_close(st):
    lad = _arm_veto(st)
    assert sch.run_daily_disarm(st, FrozenClock(OPEN_UTC)) is Mode.review_live
    assert lad.current() is Mode.review_live


def test_g5_6_daily_disarm_noop_outside_auto(st):
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    assert sch.run_daily_disarm(st, FrozenClock(OPEN_UTC)) is Mode.paper


def test_g5_6_build_scheduler_registers_the_base_cron_jobs(st):
    """The BASE job set — no ingest registry passed, so no ``ingest_*`` job is registered
    (RW-01 adds those per PROVISIONED feed; see test_scheduler.py). ``anchor_head`` joined the
    base set in the WP6/WP7 SEC-11 hardening pass."""
    pytest.importorskip("apscheduler")
    scheduler = sch.build_scheduler(st, FrozenClock(OPEN_UTC))
    jobs = {j.id: j for j in scheduler.get_jobs()}
    assert set(jobs) == {"premarket_brew", "intraday_loop", "eod_review", "daily_disarm",
                         "anchor_head"}
    assert not scheduler.running                        # returned UN-started
    # expected cron triggers.
    assert "hour='8'" in str(jobs["premarket_brew"].trigger)
    assert "minute='30'" in str(jobs["premarket_brew"].trigger)
    assert "minute='*/15'" in str(jobs["intraday_loop"].trigger)
    assert "hour='16'" in str(jobs["daily_disarm"].trigger)


def test_g5_6_market_calendar_requires_tz_aware():
    with pytest.raises(ValueError):
        mc.is_market_open(datetime(2026, 7, 7, 14, 0))  # naive rejected


# =======================================================================================
# CRITERION 7 — killswitch sets the HALT sentinel + disarms + audits (SEC-04)
# =======================================================================================
def test_g5_7_killswitch_sets_halt_disarms_and_audits(st, tmp_path):
    lad = _arm_veto(st)
    assert lad.current() is Mode.auto_veto
    halt = tmp_path / "HALT"
    calls = []
    res = killswitch(st, actor="lucas", reason="panic", halt_path=halt,
                     cancel_hook=lambda: (calls.append(1) or 3))

    # 1) the durable HALT sentinel is set (order path disabled).
    assert halt_is_set(halt) and res.halt_path == halt
    # 2) in-flight proposals cancelled via the injected hook.
    assert res.cancelled_count == 3 and calls == [1]
    # 3) auto modes disarmed to review_live.
    assert res.mode_after is Mode.review_live and lad.current() is Mode.review_live
    # 4) an AuditEntry records the killswitch (Inv 6), carrying the halt path.
    ks = _audit(st, EVENT_KILLSWITCH)
    assert ks and (ks[-1].payload or {}).get("halt_path") == str(halt)
    assert st.audit.verify_chain().ok
