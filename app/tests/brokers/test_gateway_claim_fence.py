"""OrderGateway claim-fence hook split out of CSL-218 / #173.

These tests cover only the gateway.py submit changes: the claim_fence callback,
the re-fence before transport, the pinned broker, the second HALT probe, and the
paper-profile flag. PaperOrderGateway itself stays on the authority-profile PR.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import timedelta

import pytest

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.gateway import OrderGateway, _ADMISSION_WAIT_BUDGET_S
from config.settings import Mode
from core.clock import FrozenClock
from orchestrator.halt import default_halt_path, set_halt
from schemas.models.approval_record import ApprovalRecord
from schemas.models.order import Order
from schemas.models.risk_decision import RiskDecision
from tests.brokers.conftest import build_scenario


class _CountingPaper(AlpacaPaperBroker):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def submit_order(self, canonical):
        self.calls += 1
        return super().submit_order(canonical)


class _ProbeBroker:
    def __init__(self):
        self.calls = 0

    def submit_order(self, canonical):
        self.calls += 1
        return {
            "broker_order_id": "probe-1",
            "status": "accepted",
            "filled_qty": "0",
            "avg_fill_price": None,
        }


class _MarkedPaper(OrderGateway):
    """Stand-in for the profile paper subclass, which lives in its own module."""

    _admits_paper_profile = True


def _paper_gateway(st, broker=None):
    broker = broker or _CountingPaper()
    gateway = OrderGateway(broker, st=st, execution_scope="paper")
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    return scenario, broker


def test_execution_scope_is_pinned_at_construction(st):
    gateway = OrderGateway(_CountingPaper(), st=st, execution_scope="paper")
    assert gateway.execution_scope == "paper"


def test_claim_fence_exception_refuses_before_transport(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        raise RuntimeError("lease lost")

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert "lease lost" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []
    assert scenario.st.audit.verify_chain().ok


def test_claim_fence_runs_again_before_transport(st):
    scenario, broker = _paper_gateway(st)
    calls = []

    def fence():
        calls.append(True)

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is True
    assert len(calls) >= 2
    assert broker.calls == 1
    assert scenario.st.audit.unresolved_intents() == []


def test_halt_during_claim_fence_refuses_and_covers_intent(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        set_halt(default_halt_path(), reason="HALT during claim fence")

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert (outcome.ok, outcome.stage) == (False, "halt")
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []
    assert scenario.st.audit.verify_chain().ok


def test_claim_fence_cannot_swap_the_pinned_broker(st):
    scenario, broker = _paper_gateway(st)
    swapped = _ProbeBroker()

    def fence():
        scenario.gateway._broker = swapped

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert (outcome.ok, outcome.stage) == (False, "transport_scope")
    assert broker.calls == swapped.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_paper_profile_flag_refuses_a_non_paper_scope(st):
    broker = _CountingPaper()
    gateway = _MarkedPaper(
        broker, st=st, execution_scope="live", claim_fence=lambda: object()
    )
    scenario = build_scenario(st, mode=Mode.review_live, broker=broker, gateway=gateway)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert (outcome.ok, outcome.stage) == (False, "transport_scope")
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_admission_wait_over_budget_refuses_closed(st, monkeypatch):
    scenario, broker = _paper_gateway(st)
    clock = {"now": 0.0}

    def monotonic():
        clock["now"] += 10.0
        return clock["now"]

    monkeypatch.setattr(time, "monotonic", monotonic)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert "admission wait" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


@pytest.mark.parametrize("mutation", ["scope", "broker-type"])
def test_refence_loop_rejects_transport_mutation(st, mutation):
    scenario, broker = _paper_gateway(st)
    seen = []

    class OtherPaper(_CountingPaper):
        pass

    def fence():
        seen.append(True)
        if len(seen) == 1:
            return
        if mutation == "scope":
            scenario.gateway._execution_scope = "live"
        else:
            broker.__class__ = OtherPaper

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert len(seen) >= 2
    assert (outcome.ok, outcome.stage) == (False, "transport_scope")
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_profile_transport_calls_the_admitted_paper_submit(st):
    """A fence-swapped method or a subclass override must not be the profile transport."""

    class OverridePaper(AlpacaPaperBroker):
        def __init__(self):
            super().__init__()
            self.override_calls = 0

        def submit_order(self, canonical):
            self.override_calls += 1
            return {
                "broker_order_id": "override",
                "status": "accepted",
                "filled_qty": "0",
                "avg_fill_price": None,
            }

    broker = OverridePaper()

    def fence():
        def swapped(_canonical):
            broker.swapped = True
            return {
                "broker_order_id": "swapped",
                "status": "accepted",
                "filled_qty": "0",
                "avg_fill_price": None,
            }

        broker.submit_order = swapped
        return object()

    gateway = _MarkedPaper(broker, st=st, execution_scope="paper", claim_fence=fence)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is True
    assert outcome.stage == "submitted"
    assert broker.override_calls == 0
    assert getattr(broker, "swapped", False) is False
    assert outcome.broker_order_id not in {"override", "swapped"}
    assert broker._client.submitted
    assert scenario.st.audit.unresolved_intents() == []


def test_profile_gateway_requires_a_claim_fence(st):
    broker = _CountingPaper()
    gateway = _MarkedPaper(broker, st=st, execution_scope="paper")
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert "requires a claim fence" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_reentrant_submit_is_refused_before_the_token_is_consumed(st):
    scenario, broker = _paper_gateway(st)
    scenario.gateway.mint_submit_token(
        nonce="nested",
        proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id,
        order_tuple_hash=scenario.tuple_hash,
    )
    nested = {}

    def fence():
        nested["outcome"] = scenario.gateway.submit(
            scenario.order,
            claim_fence=lambda: None,
            **scenario.submit_kwargs(token_nonce="nested"),
        )

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert nested["outcome"].ok is False
    assert nested["outcome"].stage == "reentrant_submit"
    assert outcome.ok is True
    assert broker.calls == 1
    assert scenario.gateway.live_token_count() == 1
    assert scenario.st.audit.unresolved_intents() == []


class _BudgetLock:
    """Stands in for ``safety_admission_lock``. A timed acquire fails closed; ``with`` does not."""

    def __init__(self):
        self._lock = threading.Lock()
        self.timeouts: list = []

    def acquire(self, blocking=True, timeout=-1):
        self.timeouts.append(timeout)
        if timeout is not None and timeout >= 0:
            return False
        return self._lock.acquire(blocking, timeout)

    def release(self):
        self._lock.release()

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("admission lock context manager acquired nothing")
        return self

    def __exit__(self, *_args):
        self.release()
        return False


def test_admission_lock_acquire_is_bounded(st):
    scenario, broker = _paper_gateway(st)
    lock = _BudgetLock()
    scenario.st.safety_admission_lock = lock
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert (outcome.ok, outcome.stage) == (False, "admission_lock")
    assert "lock" in outcome.reason
    assert _ADMISSION_WAIT_BUDGET_S in lock.timeouts
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_fence_wait_refreshes_a_flipped_risk_recheck(st):
    scenario, broker = _paper_gateway(st)
    state = {"decision": "approved"}

    def recheck(_positions):
        return {"decision": state["decision"], "failed_checks": []}

    def fence():
        state["decision"] = "rejected"

    outcome = scenario.gateway.submit(
        scenario.order,
        claim_fence=fence,
        **scenario.submit_kwargs(risk_recheck=recheck),
    )
    assert (outcome.ok, outcome.stage) == (False, "risk")
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_fence_wait_refreshes_a_stale_feed(st):
    scenario, broker = _paper_gateway(st)

    class Guard:
        def __init__(self):
            self.reason = None

        def stale_reason(self, symbol, *, now, limit_s):
            assert symbol == "AAPL"
            return self.reason

    guard = Guard()
    scenario.st.market_data = guard

    def fence():
        guard.reason = "market quote for AAPL is stale"

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert (outcome.ok, outcome.stage) == (False, "market_data")
    assert "stale" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_fence_wait_refreshes_an_expired_risk_evaluation(st):
    broker = _CountingPaper()
    gateway = OrderGateway(
        broker, st=st, execution_scope="paper", approval_ttl_seconds=900
    )
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    aged = scenario.clock.now() - timedelta(seconds=890)
    with st.session_factory() as session, session.begin():
        row = session.get(RiskDecision, scenario.risk_decision.id)
        row.evaluated_at = aged

    def fence():
        scenario.clock._at = scenario.clock.now() + timedelta(seconds=20)

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "approval"
    assert "expired" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_claim_fence_base_exception_covers_the_intent(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        scenario.gateway.submit(
            scenario.order, claim_fence=fence, **scenario.submit_kwargs()
        )
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refence_base_exception_covers_the_intent(st):
    scenario, broker = _paper_gateway(st)
    calls = {"n": 0}

    def fence():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        scenario.gateway.submit(
            scenario.order, claim_fence=fence, **scenario.submit_kwargs()
        )
    assert calls["n"] >= 2
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_second_gateway_sharing_state_cannot_submit_the_same_order(st):
    scenario, broker = _paper_gateway(st)
    other_broker = _CountingPaper()
    other = OrderGateway(other_broker, st=st, execution_scope="paper")
    other.mint_submit_token(
        nonce="other",
        proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id,
        order_tuple_hash=scenario.tuple_hash,
    )
    nested = {}

    def fence():
        nested["outcome"] = other.submit(
            scenario.order,
            claim_fence=lambda: None,
            **scenario.submit_kwargs(token_nonce="other"),
        )

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert nested["outcome"].ok is False
    assert nested["outcome"].stage == "reentrant_submit"
    assert outcome.ok is True
    assert broker.calls == 1
    assert other_broker.calls == 0
    assert other.live_token_count() == 1
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_refuses_an_approval_consumed_during_the_fence(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        with st.session_factory() as session, session.begin():
            row = session.get(ApprovalRecord, scenario.approval.id)
            row.single_use_consumed = True

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "approval"
    assert "consumed" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_refuses_an_order_left_unsubmittable_during_the_fence(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        with st.session_factory() as session, session.begin():
            row = session.get(Order, scenario.order.id)
            row.state = "cancelled"

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "order_state"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_admitted_paper_submit_is_captured_at_init(st):
    broker = AlpacaPaperBroker()

    def fence():
        return object()

    gateway = _MarkedPaper(broker, st=st, execution_scope="paper", claim_fence=fence)
    captured = AlpacaPaperBroker.submit_order
    assert gateway._admitted_paper_submit is captured
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)

    def evil(_self, _canonical):
        broker.evil_calls = getattr(broker, "evil_calls", 0) + 1
        return {
            "broker_order_id": "evil",
            "status": "accepted",
            "filled_qty": "0",
            "avg_fill_price": None,
        }

    AlpacaPaperBroker.submit_order = evil
    try:
        outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    finally:
        AlpacaPaperBroker.submit_order = captured
    assert outcome.ok is True
    assert outcome.broker_order_id != "evil"
    assert getattr(broker, "evil_calls", 0) == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_slow_post_fence_refresh_is_inside_the_wait_budget(st, monkeypatch):
    """631ffff4 starts the wait timer before the post-fence refresh."""
    scenario, broker = _paper_gateway(st)
    elapsed = {"s": 0.0}
    fences = {"n": 0}

    def monotonic():
        return elapsed["s"]

    def recheck(_positions):
        elapsed["s"] += _ADMISSION_WAIT_BUDGET_S + 1.0
        return {"decision": "approved", "failed_checks": []}

    def fence():
        fences["n"] += 1
        return None

    monkeypatch.setattr(time, "monotonic", monotonic)
    outcome = scenario.gateway.submit(
        scenario.order,
        claim_fence=fence,
        **scenario.submit_kwargs(risk_recheck=recheck),
    )
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert "admission wait" in outcome.reason
    assert fences["n"] >= 2
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_longer_than_the_remaining_lease_does_not_submit(st, monkeypatch):
    scenario, broker = _paper_gateway(st)
    elapsed = {"s": 0.0}
    fences = {"n": 0}

    def monotonic():
        return elapsed["s"]

    def recheck(_positions):
        elapsed["s"] += 4.0
        return {"decision": "approved", "failed_checks": []}

    def fence():
        fences["n"] += 1
        return type("Lease", (), {"remaining_s": 3.0})()

    monkeypatch.setattr(time, "monotonic", monotonic)
    outcome = scenario.gateway.submit(
        scenario.order,
        claim_fence=fence,
        **scenario.submit_kwargs(risk_recheck=recheck),
    )
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert "admission wait" in outcome.reason
    assert fences["n"] >= 2
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_noop_submit_fence_does_not_admit_a_profile_gateway(st):
    broker = _CountingPaper()
    gateway = _MarkedPaper(broker, st=st, execution_scope="paper")
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert "bound at setup" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_bound_noop_fence_does_not_admit_a_profile_gateway(st):
    broker = _CountingPaper()
    gateway = _MarkedPaper(
        broker, st=st, execution_scope="paper", claim_fence=lambda: None
    )
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert "no admission" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_post_fence_refresh_uses_the_state_clock(st):
    broker = _CountingPaper()
    gateway = OrderGateway(
        broker, st=st, execution_scope="paper", approval_ttl_seconds=900
    )
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    aged = scenario.clock.now() - timedelta(seconds=890)
    with st.session_factory() as session, session.begin():
        row = session.get(RiskDecision, scenario.risk_decision.id)
        row.evaluated_at = aged
    frozen = FrozenClock(scenario.clock.now())

    def fence():
        scenario.clock._at = scenario.clock.now() + timedelta(seconds=40)

    outcome = scenario.gateway.submit(
        scenario.order,
        claim_fence=fence,
        **scenario.submit_kwargs(clock=frozen),
    )
    assert outcome.ok is False
    assert outcome.stage == "approval"
    assert "expired" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_admission_wait_cannot_outlive_the_approval_ttl(st):
    broker = _CountingPaper()
    gateway = OrderGateway(
        broker, st=st, execution_scope="paper", approval_ttl_seconds=900
    )
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    aged = scenario.clock.now() - timedelta(seconds=896)
    with st.session_factory() as session, session.begin():
        row = session.get(RiskDecision, scenario.risk_decision.id)
        row.evaluated_at = aged

    class AdvancingLock:
        def __init__(self):
            self.acquires = 0

        def acquire(self, blocking=True, timeout=-1):
            self.acquires += 1
            scenario.clock._at = scenario.clock._at + timedelta(seconds=3)
            return True

        def release(self):
            return None

    lock = AdvancingLock()
    scenario.st.safety_admission_lock = lock
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert lock.acquires >= 2
    assert outcome.ok is False
    assert outcome.stage == "approval"
    assert "expired" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_admission_acquire_interrupt_covers_the_intent(st):
    scenario, broker = _paper_gateway(st)

    class Boom:
        def acquire(self, blocking=True, timeout=-1):
            raise KeyboardInterrupt

        def release(self):
            raise AssertionError("lock was not held")

    scenario.st.safety_admission_lock = Boom()
    with pytest.raises(KeyboardInterrupt):
        scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_admission_read_exception_covers_the_intent(st):
    scenario, broker = _paper_gateway(st)

    class HeldLock:
        def __init__(self):
            self.held = False

        def acquire(self, blocking=True, timeout=-1):
            self.held = True
            return True

        def release(self):
            self.held = False

    lock = HeldLock()
    scenario.st.safety_admission_lock = lock
    original = scenario.breaker.watch_only_reason

    def watch():
        if lock.held:
            raise RuntimeError("admission read failed")
        return original()

    scenario.breaker.watch_only_reason = watch
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is False
    assert outcome.stage == "admission_lock"
    assert "admission read failed" in outcome.reason
    assert lock.held is False
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []
