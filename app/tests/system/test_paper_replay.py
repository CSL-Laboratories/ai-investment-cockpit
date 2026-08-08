"""G9 — the 2-WEEK PAPER REPLAY acceptance gate (WP9 flagship).

Drives the REUSABLE ``tests.system.replay.run_paper_replay`` soak driver across ~10 TRADING
days on the fully-composed hermetic cockpit and asserts the whole-replay G9 guarantees, each
with a positive/negative control proving the guarantee is CAUSAL (not vacuously green):

  1. the HMAC audit chain still verifies at the end of the whole replay;
  2. the deep-escalation budget RESETS each day (``spent(day_i)`` is per-day, never cumulative);
  3. NO recorded event is ever dropped — every persisted id lands in a triage disposition;
  4. auto modes disarm at close (``run_daily_disarm``) and never silently re-arm;
  5. approved paper orders round-tripped review->submit — each ``FakePaperBroker`` submit is
     preceded by its two-phase ``intent:order_submit`` audit entry on the signed chain;
  6. the external anchor head advances monotonically across days.

Assertions go ONLY through public entry points, persisted rows, and HMAC-verified audit
entries (mirrors ``tests/workflows/test_g8_acceptance`` style). ``COCKPIT_OPERATOR_IDS`` is set
per-test so the REAL ``bridge.approval.record_decision`` path can mint for the operator; the
autouse ``_isolate_halt`` fixture keeps the SEC-04 sentinel per-test.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from bridge.anchor import AnchorRewind
from bridge.approval import Rejection, record_decision
from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from config.settings import Mode
from core.ids import new_id
from orchestrator.gate import GateResult
from orchestrator.killswitch import killswitch
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from schemas.models.risk_decision import RiskDecision
from workflows.budget import DeepBudget

from tests.system.harness import approved_risk_recheck, build_system, market_days
from tests.system.replay import (
    INTENT_ORDER_SUBMIT,
    RESULT_ORDER_SUBMIT,
    ReplayReport,
    approve_and_place,
    run_paper_replay,
)

OPERATOR = "123456789012345678"
DAYS = 10


@pytest.fixture(autouse=True)
def _allowlist_operator(monkeypatch):
    """Put the replay operator on the allowlist so the REAL approval path mints (SEC actor
    auth reads ``COCKPIT_OPERATOR_IDS`` directly; monkeypatch auto-reverts, staying hermetic)."""
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR)
    yield


@pytest.fixture
def report(system) -> ReplayReport:
    """One full 10-trading-day replay, auto-arming ``auto_veto`` on day 2 (so day 3, a non-auto
    day, proves the ladder does NOT silently re-arm after the daily disarm)."""
    return run_paper_replay(system, days=DAYS, orders_per_day=1, auto_days=(2,), operator=OPERATOR)


def _verified(st, event_type: str) -> list[AuditEntry]:
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == event_type)
            .order_by(AuditEntry.seq)
        ).scalars().all()
    return [e for e in entries if st.audit.verify_entry(e)]


# =======================================================================================
# G9-0 — the replay actually did non-trivial work (guards against a vacuous green suite)
# =======================================================================================
def test_g9_0_replay_did_real_work(report):
    assert len(report.days) == DAYS
    # every day produced a brief, escalated the two corroborated filings, and placed one order
    assert len(report.brief_ids) == DAYS
    assert report.total_orders == DAYS
    assert all(len(d.escalated) == 2 for d in report.days), \
        "each day must escalate exactly the two corroborated (AAPL, MSFT) filings"
    assert report.total_escalations == 2 * DAYS


# =======================================================================================
# G9-1 — the HMAC audit chain still verifies at the end of the whole replay
# =======================================================================================
def test_g9_1_audit_chain_verifies_end_to_end(report, system):
    assert report.audit_ok is True
    assert report.audit_head_seq > 0
    # positive control: re-verify live (not just the cached report flag) over a POPULATED chain.
    assert system.st.audit.verify_chain().ok
    assert report.audit_head_seq > 100, "a 10-day replay must leave a substantial signed ledger"

    # negative control: verify_chain is CAUSAL — a fresh identical replay whose ledger we then
    # tamper flips it to False (so the True above is a real integrity check, not a constant).
    victim = build_system(tmp_path=system.parquet_root.parent / "victim")
    run_paper_replay(victim, days=2, orders_per_day=1, operator=OPERATOR)
    assert victim.st.audit.verify_chain().ok
    with victim.st.session_factory() as s:
        row = s.execute(select(AuditEntry).order_by(AuditEntry.seq).limit(1)).scalar_one()
        row.payload = dict(row.payload or {}, _tampered=True)
        s.add(row)
        s.commit()
    assert victim.st.audit.verify_chain().ok is False, \
        "tampering one signed payload MUST break the chain (proves G9-1 is causal)"


# =======================================================================================
# G9-2 — the deep budget RESETS each day (spent(day_i) is per-day, never cumulative)
# =======================================================================================
def test_g9_2_deep_budget_resets_each_day(report, system):
    st = system.st
    per_day = [d.deep_budget_spent for d in report.days]
    assert per_day == [2] * DAYS, "each day escalates exactly 2 corroborated filings"

    # every day is UNDER the audited daily budget (default 5) — the reset keeps headroom.
    limit = DeepBudget(st).limit
    assert all(spent <= limit for spent in per_day)

    # positive: DeepBudget.spent re-derived per calendar date equals the per-day accumulation.
    for d in report.days:
        assert DeepBudget(st).spent(d.day) == d.deep_budget_spent

    # negative control (the crux): the budget is NOT cumulative. The whole-replay ledger holds
    # every day's escalations (2 * DAYS), yet any single day's spent stays at 2 — so a fresh
    # DeepBudget scoped to one date sees ONLY that date, proving the daily reset is real.
    total_ledger = len(_verified(st, "deep_escalation"))
    assert total_ledger == 2 * DAYS
    assert max(per_day) == 2 < total_ledger, \
        "if the budget were cumulative, a later day's spent would exceed 2 (proves reset causal)"
    # distinct dates, so the per-day counts partition the ledger exactly.
    assert sum(per_day) == total_ledger


# =======================================================================================
# G9-3 — NO recorded event is ever dropped (Inv 7): every persisted id is dispositioned
# =======================================================================================
def test_g9_3_no_events_dropped(report):
    assert report.persisted_event_ids, "the replay must have persisted events"
    assert report.dropped_event_ids == set(), \
        "every recorded event MUST land in a triage disposition (Inv 7 — nothing dropped)"
    assert report.persisted_event_ids <= report.dispositioned_event_ids

    # positive control: the benign NOISE items are RECORDED (as a disposition), not silently
    # discarded — every day persists exactly 3 events (2 filings + 1 noise).
    assert len(report.persisted_event_ids) == 3 * DAYS
    for d in report.days:
        assert len(d.events_persisted) == 3
        assert set(d.events_persisted) <= report.dispositioned_event_ids

    # negative control: a fabricated id that was NEVER persisted is NOT in the disposition set
    # — so "subset" is a real membership test, not a vacuous always-true over a universal set.
    assert "no-such-event-id" not in report.dispositioned_event_ids


# =======================================================================================
# G9-4 — auto modes disarm at close and NEVER silently re-arm (SEC-23)
# =======================================================================================
def test_g9_4_auto_disarms_and_never_rearms(report, system):
    # every day ends at review_live — the disarm floor; no day ever ends in an auto mode.
    assert all(d.mode_close == "review_live" for d in report.days), \
        "run_daily_disarm must drop every auto day back to review_live at close (SEC-23)"

    # day 2 was armed: it OPENED in auto_veto (proving the real arm took effect)...
    day2 = report.days[2]
    assert day2.mode_open == "auto_veto"
    assert day2.mode_close == "review_live"

    # ...and day 3 (a NON-auto day following it) OPENED at review_live — the ladder did NOT
    # silently re-arm overnight. This is the causal core of "never re-arm".
    day3 = report.days[3]
    assert day3.mode_open == "review_live"
    assert all(d.mode_open == "review_live" for d in report.days if d.index != 2), \
        "only the explicitly-armed day may open in an auto mode; all others stay review_live"

    # positive/negative control on the mechanism itself: arming makes current() read auto_veto;
    # WITHOUT a disarm it STAYS auto_veto (so the disarm above is causal, not a coincidence),
    # and daily_disarm then drops it — while a second read never spontaneously re-arms.
    ctrl = build_system(tmp_path=system.parquet_root.parent / "disarm-ctrl", mode=Mode.review_live)
    day = market_days(1)[0]
    clk = day.clock()
    armed = ctrl.ladder.arm_auto_mode(
        Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
        gate_result=GateResult(unlockable=True, target="auto_veto"), operator=OPERATOR, clock=clk)
    assert armed.armed
    ctrl.anchor(clock=clk)
    assert ctrl.ladder.current() is Mode.auto_veto           # arm took effect
    assert ctrl.ladder.current() is Mode.auto_veto           # stays armed WITHOUT a disarm
    ctrl.disarm(clock=clk)
    assert ctrl.ladder.current() is Mode.review_live         # disarm dropped it (causal)
    assert ctrl.ladder.current() is Mode.review_live         # never spontaneously re-arms


# =======================================================================================
# G9-5 — approved paper orders round-tripped review->submit, each with a preceding two-phase
# intent EVENT; a refused order NEVER reaches the broker (the gate is load-bearing)
# =======================================================================================
def test_g9_5_orders_round_trip_review_then_submit(report, system):
    st = system.st
    submits = system.broker.submits

    # one submit per placed order; the broker acks match what the report accumulated.
    assert len(submits) == report.total_orders == DAYS
    assert report.broker_order_ids == [f"paper-{i}" for i in range(1, DAYS + 1)]

    # each placed order reached the terminal 'submitted' row state (via the FSM under the lock).
    with st.session_factory() as s:
        states = {o.id: o.state for o in s.execute(select(Order)).scalars().all()}
    for oid in report.placed_order_ids:
        assert states.get(oid) == "submitted"

    # SEC-13 two-phase: for EACH placed order an HMAC-verified intent:order_submit precedes its
    # result:order_submit on the chain (intent BEFORE the private broker call, result AFTER).
    intents = _verified(st, INTENT_ORDER_SUBMIT)
    results = _verified(st, RESULT_ORDER_SUBMIT)
    assert len(intents) == len(results) == DAYS
    intent_by_order = {(e.refs or {}).get("order_id"): e for e in intents}
    for oid in report.placed_order_ids:
        it = intent_by_order.get(oid)
        assert it is not None, f"order {oid} has no signed order_submit intent"
        res = [e for e in results if (e.refs or {}).get("intent_seq") == it.seq]
        assert len(res) == 1, "exactly one result must pair with the intent (two-phase)"
        assert it.seq < res[0].seq, "the intent EVENT must PRECEDE its result (intent-before-call)"

    # negative control: an order approved by a FOREIGN (non-allowlisted) actor mints no token
    # and binds no adjudication -> the gateway refuses it and it NEVER reaches the broker.
    n_before = len(submits)
    day = market_days(1)[0]
    clk = day.clock()
    rd = RiskDecision(
        id=new_id("rd"), proposal_id="attack", decision="approved", checks={},
        failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
        human_required=False, evaluated_at=clk.now(), created_at=clk.now())
    st.audit.record_state_change(rd, "risk_decision", actor="risk",
                                 payload={"decision": "approved", "proposal_id": "attack"})
    resolved = {"symbol": "AAPL", "side": "buy", "qty": "1", "order_type": "limit",
                "limit_price": "150", "tif": "day"}
    h = order_tuple_hash(canonical_order_tuple(**resolved))
    foreign = record_decision(
        st, proposal_id="attack", risk_decision=rd, resolved_order=resolved,
        actor_user_id="mallory", user_decision="approve", surface="discord",
        interaction_id="ix-attack", clock=clk, modal_confirmed=True, notional="150",
        token_registry=system.token_registry, mode=system.ladder.current())
    assert isinstance(foreign, Rejection) and foreign.code == "unauthorized"

    order = create_order(
        st, proposal_id="attack", risk_decision_id=rd.id, approval_id="appr-forged",
        account="paper", symbol="AAPL", side="buy", qty="1", order_type="limit", tif="day",
        order_tuple_hash=h, limit_price="150", notional="150", actor="claude", clock=clk)
    system.anchor(clock=clk)

    class _ForgedAppr:  # a caller-supplied object the gateway must NOT trust (never persisted)
        id = "appr-forged"
        risk_decision_id = rd.id
        proposal_id = "attack"
        order_tuple_hash = h
        single_use_consumed = False
        decided_at = clk.now()

    outcome = system.gateway.place_equity_order(
        order, approval=_ForgedAppr(), risk_decision=rd, ladder=system.ladder,
        breaker_engine=system.breaker, risk_recheck=approved_risk_recheck, positions={},
        token_nonce="subtok_appr-forged", producing_runtime="claude", clock=clk)
    assert outcome.ok is False and outcome.stage == "approval"
    assert len(submits) == n_before, "a foreign-approved order MUST NEVER reach the broker"
    assert st.audit.verify_chain().ok


# =======================================================================================
# G9-6 — the external anchor head advances MONOTONICALLY across the replay
# =======================================================================================
def test_g9_6_anchor_head_advances_monotonically(report, system):
    seqs = report.anchor_seq_timeline
    assert len(seqs) == DAYS
    assert all(s > 0 for s in seqs), "every day must anchor a real head"
    assert seqs == sorted(seqs), "anchor seq must never move backward"
    assert all(b > a for a, b in zip(seqs, seqs[1:])), \
        "each day's EOD anchor must STRICTLY advance the witness head (new signed entries daily)"

    # the live witness matches the last recorded anchor, and the verified head still covers it.
    store = system.anchor_store
    latest = store.latest()
    assert latest is not None and latest[0] == seqs[-1]
    assert system.st.audit.verify_against_anchor(latest[0], latest[1]).ok

    # negative control: the store is genuinely MONOTONIC — a rewind (or a fork at the same seq)
    # is REFUSED. So the strictly-increasing timeline above reflects a real guard, not luck.
    with pytest.raises(AnchorRewind):
        store.append(1, "deadbeef")
    with pytest.raises(AnchorRewind):
        store.append(latest[0], "0" * 64)   # same seq, different hash -> fork refused


# =======================================================================================
# G9-7 — the runner is reusable as a soak driver: a fresh short replay stands alone + a
# killswitch mid-replay HALTS the order path (a real containment control on the runner path)
# =======================================================================================
def test_g9_7_runner_reusable_and_halt_contains(system, tmp_path):
    # reusable: a fresh 3-day replay on a fresh system verifies independently.
    fresh = build_system(tmp_path=tmp_path / "fresh")
    short = run_paper_replay(fresh, days=3, orders_per_day=1, operator=OPERATOR)
    assert short.audit_ok and short.total_orders == 3
    assert short.dropped_event_ids == set()
    assert short.anchor_seq_timeline == sorted(short.anchor_seq_timeline)

    # containment control on the runner's order path: after a killswitch sets the SEC-04 HALT
    # sentinel, the REAL approve+place round-trip is refused at the 'halt' gate and the broker
    # is never called — proving the round-trip in the happy-path tests was gated all along.
    n_before = len(fresh.broker.submits)
    day = market_days(1)[0]
    killswitch(fresh.st, actor="op", reason="containment drill")
    outcome, _order = approve_and_place(
        fresh, clock=day.clock(), symbol="AAPL", proposal_id="post-halt",
        interaction_id="ix-halt", operator=OPERATOR)
    assert outcome.ok is False and outcome.stage == "halt", \
        "the REAL approve+place round-trip must refuse at the SEC-04 HALT gate"
    assert len(fresh.broker.submits) == n_before, "no order may reach the broker after HALT"
    assert fresh.st.audit.verify_chain().ok
