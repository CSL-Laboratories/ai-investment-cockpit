"""WP7 unit tests — the OrderGateway order path (Inv 1; SEC-01/03/04/07/08).

Each locked-submit gate (a)-(h) is proven to refuse INDEPENDENTLY and fail CLOSED (nothing
reaches the private broker on any refusal), review provably precedes place, and no capability
dispatch trick reaches a broker submit without a live single-use token.
"""
from __future__ import annotations

import functools
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

import brokers.gateway as gateway_mod
from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.gateway import (
    BYPASS_APPROVAL_AGAINST_FAILED_RISK,
    BYPASS_HARD_CHECK_FAILED,
    BYPASS_RESUBMIT_AFTER_DENIED,
    OrderGateway,
)
from brokers.robinhood_mcp import RobinhoodMCPBroker
from config.settings import Mode
from core.clock import FrozenClock
from orchestrator.gate import GateResult
from orchestrator.halt import default_halt_path, set_halt
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from core.ids import new_id

from tests.brokers.conftest import build_scenario


class RecordingBroker:
    """A broker seam that counts submit_order calls so a refusal can be proven to never reach
    the broker (fail closed)."""

    def __init__(self):
        self.calls = 0

    def submit_order(self, canonical):
        self.calls += 1
        return {"broker_order_id": "rec-1", "status": "accepted",
                "filled_qty": Decimal("0"), "avg_fill_price": None}


def _events(st, event_type):
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
    return [r for r in rows if r.event_type == event_type]


def _arm_auto_veto(scenario):
    """Arm auto_veto with a stubbed good gate + claude pins (so mode == auto_veto)."""
    res = scenario.ladder.arm_auto_mode(
        Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
        gate_result=GateResult(unlockable=True, target="auto_veto"), operator="op")
    assert res.armed and scenario.ladder.current() is Mode.auto_veto


# =======================================================================================
# HAPPY PATH + two-phase submit
# =======================================================================================
def test_happy_path_submit_advances_and_audits(scenario):
    out = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert out.ok and out.stage == "submitted" and out.broker_order_id
    with scenario.st.session_factory() as s:
        row = s.get(Order, scenario.order.id)
        assert row.state == "submitted" and row.broker_order_id == out.broker_order_id
    # approval + token both consumed single-use; chain verifies.
    assert scenario.approval.single_use_consumed is True
    assert scenario.gateway.live_token_count() == 0
    assert scenario.st.audit.verify_chain().ok
    # two-phase: an intent BEFORE and a result AFTER, fully resolved.
    assert _events(scenario.st, "intent:order_submit")
    assert _events(scenario.st, "result:order_submit")
    assert scenario.st.audit.unresolved_intents() == []


def test_review_is_pure_dry_run_no_submit_no_consume(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    out = sc.gateway.review_equity_order(sc.order, **sc.review_kwargs())
    assert out.ok and out.stage == "reviewed"
    assert broker.calls == 0                      # no submit
    assert sc.gateway.live_token_count() == 1     # token NOT consumed
    with sc.st.session_factory() as s:
        assert s.get(Order, sc.order.id).state == "proposed"


# =======================================================================================
# (a) HALT
# =======================================================================================
def test_a_halt_set_refuses(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    set_halt(default_halt_path(), reason="test")
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "halt" and broker.calls == 0
    assert sc.gateway.live_token_count() == 1     # token never consumed on an early refusal


def test_a_unreadable_halt_fails_closed(scenario, monkeypatch):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)

    def boom(_path):
        raise OSError("permission denied reading HALT")

    monkeypatch.setattr(gateway_mod, "halt_is_set", boom)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "halt" and broker.calls == 0


# =======================================================================================
# (b) mode
# =======================================================================================
@pytest.mark.parametrize("mode", [Mode.read_only, Mode.paper])
def test_b_mode_without_order_authority_refuses(st, mode):
    # a CLEAN state db (current() == read_only) so read_only is genuinely exercised — the
    # ladder helper only walks UP, so a shared review_live db could not represent read_only.
    broker = RecordingBroker()
    sc = build_scenario(st, broker=broker, mode=mode)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "mode" and broker.calls == 0


def test_b_paper_scoped_gateway_allows_paper_but_not_live_modes(st):
    paper_broker = RecordingBroker()
    paper_gateway = OrderGateway(paper_broker, st=st, execution_scope="paper")
    paper = build_scenario(st, broker=paper_broker, gateway=paper_gateway, mode=Mode.paper)
    out = paper.gateway.submit(paper.order, **paper.submit_kwargs())
    assert out.ok and paper_broker.calls == 1

    paper.ladder.set_mode(Mode.review_live, actor="op")
    refused = paper.gateway.submit(paper.order, **paper.submit_kwargs())
    assert not refused.ok and refused.stage == "mode" and paper_broker.calls == 1


def test_b_unknown_execution_scope_is_rejected():
    with pytest.raises(ValueError, match="execution_scope"):
        OrderGateway(RecordingBroker(), execution_scope="anything")


def test_b_watch_only_refuses(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    sc.breaker.enter_watch_only("claude", "429 exhausted")
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "mode" and broker.calls == 0


def test_b_unknown_calendar_year_refuses_review_and_submit_before_token_or_broker(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    unknown = FrozenClock(datetime(2029, 1, 2, 15, 0, tzinfo=timezone.utc))

    reviewed = sc.gateway.review_equity_order(
        sc.order, **sc.review_kwargs(clock=unknown)
    )
    submitted = sc.gateway.submit(sc.order, **sc.submit_kwargs(clock=unknown))

    assert not reviewed.ok and reviewed.stage == "calendar"
    assert not submitted.ok and submitted.stage == "calendar"
    assert broker.calls == 0
    assert sc.gateway.live_token_count() == 1
    refusals = _events(sc.st, gateway_mod.EVENT_ORDER_REFUSED)
    assert len(refusals) == 2
    assert all("calendar" in row.payload["reason"].lower() for row in refusals)
    assert all(row.payload["stage"] == "calendar" for row in refusals)


# =======================================================================================
# (c) SEC-01 injection/anomaly breaker
# =======================================================================================
def test_c_injection_breaker_trip_refuses(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    # the default injection predicate reads context.injection_detected; the gateway passes the
    # order as context, so flagging the order trips the synchronous SEC-01 breaker.
    sc.order.injection_detected = True
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "breaker" and broker.calls == 0
    # the breaker force-dropped the mode to review_live (already there) and audited a trip.
    assert _events(sc.st, "breaker_trip")


# =======================================================================================
# (d) risk RE-RUN (TOCTOU)
# =======================================================================================
def test_d_toctou_risk_flip_to_fail_voids_and_records_bypass(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)

    def flipped(_positions):
        return {"decision": "rejected", "failed_checks": ["buying_power"]}

    out = sc.gateway.submit(sc.order, **sc.submit_kwargs(risk_recheck=flipped))
    assert not out.ok and out.stage == "risk" and broker.calls == 0
    # a hard-check failure records a BYPASS event the gate watches (SEC-20).
    assert _events(sc.st, BYPASS_HARD_CHECK_FAILED)


def test_d_risk_flip_to_needs_human_refuses_without_bypass(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)

    def needs_human(_positions):
        return {"decision": "needs_human", "failed_checks": []}

    out = sc.gateway.submit(sc.order, **sc.submit_kwargs(risk_recheck=needs_human))
    assert not out.ok and out.stage == "risk" and broker.calls == 0
    assert not _events(sc.st, BYPASS_HARD_CHECK_FAILED)   # not a hard-check failure


# =======================================================================================
# (e) approval validity
# =======================================================================================
def test_e_consumed_approval_replay_refused(scenario):
    broker = RecordingBroker()
    sc = build_scenario(scenario.st, broker=broker, consumed=True)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "approval" and broker.calls == 0
    assert _events(sc.st, BYPASS_RESUBMIT_AFTER_DENIED)


def test_e_binding_mismatch_risk_decision_refused(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    # pass a DIFFERENT (approved) risk decision than the one the approval binds to.
    other = _approved_rd(sc.st)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs(risk_decision=other))
    assert not out.ok and out.stage == "approval" and broker.calls == 0


def test_e_approval_against_failed_risk_records_bypass(scenario):
    broker = RecordingBroker()
    sc = build_scenario(scenario.st, broker=broker, risk_decision_state="rejected")
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "approval" and broker.calls == 0
    assert _events(sc.st, BYPASS_APPROVAL_AGAINST_FAILED_RISK)


def test_e_tuple_hash_mismatch_modify_refused(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    # a Modify / re-price: the resolved tuple now hashes differently than the approved hash.
    sc.order.limit_price = Decimal("160")
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "approval" and broker.calls == 0
    assert "order_tuple_hash mismatch" in (out.reason or "")


def test_e_expired_approval_voided(scenario):
    broker = RecordingBroker()
    gw = OrderGateway(broker, approval_ttl_seconds=10)
    sc = build_scenario(scenario.st, gateway=gw, broker=broker)
    # Age the PERSISTED approval row past the 10s TTL — the gateway trusts the DB row, not a
    # mutated in-memory object (SEC-03 hardening: caller-supplied fields are never trusted).
    with sc.st.session_factory() as s, s.begin():
        row = s.get(ApprovalRecord, sc.approval.id)
        row.decided_at = sc.clock.now() - timedelta(seconds=100)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "approval" and "expired" in (out.reason or "")
    assert broker.calls == 0


def test_e_uncovered_approval_refused(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    # a fabricated approval row with a VALID binding but NO covering adjudication AuditEntry.
    ghost = ApprovalRecord(
        id=new_id("appr"), proposal_id="p1", risk_decision_id=sc.risk_decision.id,
        actor_user_id="attacker", user_decision="approve", surface="discord",
        order_tuple_hash=sc.tuple_hash, single_use_consumed=False,
        decided_at=sc.clock.now(), created_at=sc.clock.now(),
    )
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs(approval=ghost))
    assert not out.ok and out.stage == "approval" and broker.calls == 0
    assert "not covered" in (out.reason or "")


# =======================================================================================
# (f) runtime pin (SEC-08)
# =======================================================================================
def test_f_auto_mode_non_claude_runtime_refused(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    _arm_auto_veto(sc)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs(producing_runtime="codex"))
    assert not out.ok and out.stage == "runtime" and broker.calls == 0
    # claude is allowed under the same auto mode.
    ok = sc.gateway.submit(sc.order, **sc.submit_kwargs(producing_runtime="claude"))
    assert ok.ok and broker.calls == 1


# =======================================================================================
# (g) capability token — dispatch tricks cannot reach the broker without a live token
# =======================================================================================
def test_g_no_token_refuses_at_capability(scenario):
    broker = RecordingBroker()
    sc = build_scenario(scenario.st, broker=broker, mint=False)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "capability" and broker.calls == 0


def test_g_wrong_binding_token_refused(scenario):
    broker = RecordingBroker()
    sc = build_scenario(scenario.st, broker=broker, mint=False)
    sc.gateway.mint_submit_token(nonce=sc.nonce, proposal_id="p1",
                                 risk_decision_id=sc.risk_decision.id,
                                 order_tuple_hash="a-different-hash")
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "capability" and broker.calls == 0
    assert sc.gateway.live_token_count() == 0     # the wrong-binding nonce was burned


def test_g_getattr_and_partial_dispatch_cannot_reach_broker(scenario):
    broker = RecordingBroker()
    sc = build_scenario(scenario.st, broker=broker, mint=False)   # NO live token
    # attacker holding the gateway tries every dispatch trick — all hit the token consume.
    getattr(sc.gateway, "submit")(sc.order, **sc.submit_kwargs())
    functools.partial(sc.gateway.submit, sc.order)(**sc.submit_kwargs())
    shim = {"submit": sc.gateway.submit}["submit"]                # string-keyed shim dispatch
    shim(sc.order, **sc.submit_kwargs())
    assert broker.calls == 0                                      # never reached the broker


# =======================================================================================
# (h) pending-exposure cap (SEC-07)
# =======================================================================================
def test_h_pending_exposure_cap_refuses_burst(scenario):
    broker = RecordingBroker()
    gw = OrderGateway(broker, pending_exposure_cap=Decimal("1000"))
    sc = build_scenario(scenario.st, gateway=gw, broker=broker, notional="1500")
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "pending_exposure" and broker.calls == 0


# =======================================================================================
# review precedes place; SEC-03 no module-global broker
# =======================================================================================
def test_place_refuses_when_review_refuses_no_submit(scenario):
    broker = RecordingBroker()
    sc = _fresh(scenario, broker)
    set_halt(default_halt_path(), reason="test")                 # review's (a) fails
    out = sc.gateway.place_equity_order(sc.order, **sc.submit_kwargs())
    assert not out.ok and broker.calls == 0
    assert sc.gateway.live_token_count() == 1                    # token never consumed


def test_place_calls_review_before_submit(scenario, monkeypatch):
    sc = _fresh(scenario, RecordingBroker())
    calls = []
    real_review = sc.gateway.review_equity_order
    real_submit = sc.gateway.submit
    monkeypatch.setattr(sc.gateway, "review_equity_order",
                        lambda *a, **k: (calls.append("review"), real_review(*a, **k))[1])
    monkeypatch.setattr(sc.gateway, "submit",
                        lambda *a, **k: (calls.append("submit"), real_submit(*a, **k))[1])
    sc.gateway.place_equity_order(sc.order, **sc.submit_kwargs())
    assert calls == ["review", "submit"]                         # review PROVABLY first


def test_sec03_no_module_global_broker_client():
    broker_types = (FakeAlpaca, AlpacaPaperBroker, RobinhoodMCPBroker)
    live = [name for name, val in vars(gateway_mod).items()
            if isinstance(val, broker_types)]
    assert live == [], f"reachable module-global broker client(s): {live}"


# =======================================================================================
# helpers
# =======================================================================================
def _approved_rd(st):
    from schemas.models.risk_decision import RiskDecision
    rd = RiskDecision(id=new_id("rd"), proposal_id="p1", decision="approved", checks={},
                      failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
                      human_required=False, evaluated_at=st.clock.now(),
                      created_at=st.clock.now())
    st.audit.record_state_change(rd, "risk_decision", actor="risk",
                                 payload={"decision": "approved"})
    return rd


def _fresh(scenario, broker):
    """A fresh valid scenario on the SAME state db but with the given recording broker."""
    return build_scenario(scenario.st, broker=broker)
