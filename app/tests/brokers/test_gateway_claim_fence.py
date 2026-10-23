"""OrderGateway claim-fence hook split out of CSL-218 / #173.

These tests cover only the gateway.py submit changes: the claim_fence callback,
the re-fence before transport, the pinned broker, the second HALT probe, and the
paper-profile flag. PaperOrderGateway itself stays on the authority-profile PR.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import builtins
import inspect

from sqlalchemy import select

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.gateway import OrderBoundPermit, OrderGateway, _ADMISSION_WAIT_BUDGET_S
from brokers.order_state_machine import create_order, transition
from config.settings import Mode
from core.clock import FrozenClock
from core.ids import new_id
from orchestrator.halt import default_halt_path, set_halt
from orchestrator.state import EVENT_ADJUDICATION
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
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
    """Stand-in for the profile paper subclass, which lives in its own module.

    The generic gateway does not import a concrete broker. This subclass supplies
    the admitted paper type and the submit callable captured at init.
    """

    _admits_paper_profile = True

    def __init__(self, broker, **kwargs):
        self._admitted_paper_type = AlpacaPaperBroker
        self._admitted_paper_submit = AlpacaPaperBroker.submit_order
        super().__init__(broker, **kwargs)


def _permit(scenario, remaining_s=None):
    return OrderBoundPermit(
        order_id=scenario.order.id,
        order_tuple_hash=scenario.tuple_hash,
        remaining_s=remaining_s,
    )


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
        return _permit(scenario)

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
    bound = {}

    def fence():
        return OrderBoundPermit(
            order_id=bound["order"].id,
            order_tuple_hash=bound["hash"],
        )

    gateway = _MarkedPaper(broker, st=st, execution_scope="paper", claim_fence=fence)
    captured = AlpacaPaperBroker.submit_order
    assert gateway._admitted_paper_submit is captured
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    bound["order"] = scenario.order
    bound["hash"] = scenario.tuple_hash

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


def test_profile_subclass_may_omit_the_admitted_type(st):
    """#173 pins the submit callable and the client. The type attribute is optional."""
    broker = _CountingPaper()
    holder = {}

    def fence():
        return _permit(holder["scenario"])

    class Supply(OrderGateway):
        _admits_paper_profile = True

        def __init__(self, broker, **kwargs):
            self._admitted_paper_submit = AlpacaPaperBroker.submit_order
            super().__init__(broker, **kwargs)

    gateway = Supply(broker, st=st, execution_scope="paper", claim_fence=fence)
    assert not hasattr(gateway, "_admitted_paper_type")
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    holder["scenario"] = scenario
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is True
    assert scenario.st.audit.unresolved_intents() == []


def test_profile_without_the_admitted_submit_raises_before_an_intent(st):
    broker = _CountingPaper()

    class Bare(OrderGateway):
        _admits_paper_profile = True

    gateway = Bare(broker, st=st, execution_scope="paper", claim_fence=lambda: None)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    with pytest.raises(ImportError, match="_admitted_paper_submit"):
        scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_generic_gateway_does_not_depend_on_the_paper_broker():
    source = inspect.getsource(inspect.getmodule(OrderGateway))
    assert "alpaca_paper" not in source
    assert "AlpacaPaperBroker" not in source


def test_paper_transport_import_failure_leaves_no_open_intent(st, monkeypatch):
    """An import while resolving the admitted transport must not leave an intent."""
    broker = _CountingPaper()

    class ImportingPaper(OrderGateway):
        _admits_paper_profile = True

        def _resolve_admitted_paper_transport(self):
            from brokers.alpaca_paper import AlpacaPaperBroker as _Paper

            return {
                "broker": self._broker,
                "broker_type": _Paper,
                "submit": _Paper.submit_order,
                "client": self._broker._client,
                "client_submit": self._broker._client.submit_order,
            }

    gateway = ImportingPaper(
        broker, st=st, execution_scope="paper", claim_fence=lambda: None
    )
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "brokers.alpaca_paper":
            raise ImportError("alpaca_paper unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(ImportError, match="alpaca_paper unavailable"):
        scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_fence_that_blocks_past_the_deadline_refuses(st, monkeypatch):
    scenario, broker = _paper_gateway(st)
    elapsed = {"s": 0.0}

    def monotonic():
        return elapsed["s"]

    def fence():
        elapsed["s"] += _ADMISSION_WAIT_BUDGET_S + 1.0
        return None

    monkeypatch.setattr(time, "monotonic", monotonic)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert "deadline" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_slow_lease_remainder_is_past_its_deadline(st, monkeypatch):
    scenario, broker = _paper_gateway(st)
    elapsed = {"s": 0.0}

    class SlowLease:
        @property
        def remaining_s(self):
            elapsed["s"] += 4.0
            return 3.0

    def monotonic():
        return elapsed["s"]

    monkeypatch.setattr(time, "monotonic", monotonic)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: SlowLease(), **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_profile_rejects_a_permit_for_another_order(st):
    broker = _CountingPaper()
    gateway = _MarkedPaper(
        broker,
        st=st,
        execution_scope="paper",
        claim_fence=lambda: OrderBoundPermit(order_id="other", order_tuple_hash="nope"),
    )
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


@pytest.mark.parametrize("value", [False, object()])
def test_profile_rejects_a_permit_that_is_not_order_bound(st, value):
    broker = _CountingPaper()
    gateway = _MarkedPaper(
        broker, st=st, execution_scope="paper", claim_fence=lambda: value
    )
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_swapped_paper_client_does_not_receive_the_order(st):
    broker = _CountingPaper()
    replacement = _CountingPaper()
    holder = {}

    def fence():
        broker._client = replacement._client
        return _permit(holder["scenario"])

    gateway = _MarkedPaper(broker, st=st, execution_scope="paper", claim_fence=fence)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    holder["scenario"] = scenario
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is False
    assert outcome.stage == "transport_scope"
    assert broker.calls == replacement.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_watch_only_during_the_final_risk_refresh_does_not_submit(st):
    scenario, broker = _paper_gateway(st)
    calls = {"n": 0}
    denial = {"reason": None}
    original = scenario.breaker.watch_only_reason

    def watch():
        return denial["reason"] or original()

    def recheck(_positions):
        calls["n"] += 1
        if calls["n"] >= 4:
            denial["reason"] = "watch_only admitted during the final risk refresh"
        return {"decision": "approved", "failed_checks": []}

    scenario.breaker.watch_only_reason = watch
    outcome = scenario.gateway.submit(
        scenario.order,
        claim_fence=lambda: None,
        **scenario.submit_kwargs(risk_recheck=recheck),
    )
    assert calls["n"] >= 4
    assert outcome.ok is False
    assert outcome.stage == "mode"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def _approved_sibling(st, *, notional="1500"):
    order = create_order(
        st, proposal_id=new_id("p"), risk_decision_id=new_id("rd"),
        account="paper", symbol="MSFT", side="buy", qty="10",
        order_type="limit", limit_price="150", tif="day",
        notional=notional, order_tuple_hash=new_id("h"), actor="claude",
    )
    transition(st, order, "approved", actor="claude", reason="approved")
    return order


def _capped_paper(st, cap="2000"):
    broker = _CountingPaper()
    gateway = OrderGateway(
        broker, st=st, execution_scope="paper",
        pending_exposure_cap=Decimal(cap),
    )
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    return scenario, broker


def test_pending_sibling_admitted_during_the_fence_does_not_submit(st):
    """A sibling that lands while the fence blocks is inside the post-fence cap."""
    scenario, broker = _capped_paper(st)

    def fence():
        _approved_sibling(st)

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "pending_exposure"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_pending_sibling_admitted_during_the_halt_probe_does_not_submit(st, monkeypatch):
    """The HALT probe sits after the refresh. Exposure is recomputed after it."""
    scenario, broker = _capped_paper(st)
    fences = {"n": 0}

    def fence():
        fences["n"] += 1

    def halt(_path):
        if fences["n"] > 0:
            _approved_sibling(st)
            fences["n"] = 0
        return False

    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "pending_exposure"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_mode_changed_during_the_halt_probe_does_not_submit(st, monkeypatch):
    """A transport-mode change during the HALT probe is re-read before send."""
    scenario, broker = _paper_gateway(st)
    fences = {"n": 0}

    def fence():
        fences["n"] += 1

    def halt(_path):
        if fences["n"] > 0:
            scenario.ladder.set_mode(Mode.review_live, actor="op")
            fences["n"] = 0
        return False

    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "mode"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_halt_probe_past_the_lease_does_not_submit(st, monkeypatch):
    """The lease measurement includes the HALT probe that follows the re-fence loop."""
    scenario, broker = _paper_gateway(st)
    elapsed = {"s": 0.0}
    fences = {"n": 0}

    def monotonic():
        return elapsed["s"]

    def fence():
        fences["n"] += 1
        return type("Lease", (), {"remaining_s": 3.0})()

    def halt(_path):
        if fences["n"] > 0:
            elapsed["s"] += 4.0
        return False

    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert "lease" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_refuses_an_approval_rebound_during_the_fence(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        with st.session_factory() as session, session.begin():
            row = session.get(ApprovalRecord, scenario.approval.id)
            row.order_tuple_hash = "not-this-order"

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "approval"
    assert "order_tuple_hash" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_refuses_an_approval_repointed_during_the_fence(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        with st.session_factory() as session, session.begin():
            row = session.get(ApprovalRecord, scenario.approval.id)
            row.proposal_id = "other-proposal"

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "approval"
    assert "proposal" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_refuses_a_tampered_adjudication_during_the_fence(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        with st.session_factory() as session, session.begin():
            rows = session.execute(
                select(AuditEntry).where(AuditEntry.event_type == EVENT_ADJUDICATION)
            ).scalars().all()
            for row in rows:
                if (row.refs or {}).get("row_id") == scenario.approval.id:
                    row.payload = {**(row.payload or {}), "order_tuple_hash": "tampered"}

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "approval"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_refuses_a_mode_outside_the_transport_during_the_fence(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        scenario.ladder.set_mode(Mode.review_live, actor="op")

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "mode"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_refuses_a_breaker_tripped_during_the_fence(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        scenario.breaker.trip("manual", "tripped during the fence")

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "breaker"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_refuses_injection_flagged_during_the_fence(st):
    scenario, broker = _paper_gateway(st)

    def fence():
        scenario.order.injection_detected = True

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "breaker"
    assert "SEC-01" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_refresh_refuses_a_calendar_that_lapses_during_the_fence(st):
    broker = _CountingPaper()
    gateway = OrderGateway(
        broker, st=st, execution_scope="paper", approval_ttl_seconds=10**9
    )
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)

    def fence():
        scenario.clock._at = datetime(2029, 1, 2, 15, 0, tzinfo=timezone.utc)

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "calendar"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


@pytest.mark.parametrize("value", [False, "", object(), {"ok": False}])
def test_generic_fence_rejection_does_not_submit(st, value):
    """False, an empty string, a bare object, and {ok: False} are not admission."""
    scenario, broker = _paper_gateway(st)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: value, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert "admission" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_replaced_paper_broker_cannot_supply_its_own_type(st):
    """Omitting _admitted_paper_type must not let a later broker pick the pin."""
    original = _CountingPaper()

    class Supply(OrderGateway):
        _admits_paper_profile = True

        def __init__(self, broker, **kwargs):
            self._admitted_paper_submit = AlpacaPaperBroker.submit_order
            super().__init__(broker, **kwargs)

    class Foreign:
        def __init__(self):
            self.calls = 0
            self._client = _CountingPaper()._client

        def submit_order(self, canonical):
            self.calls += 1
            return {
                "broker_order_id": "foreign",
                "status": "accepted",
                "filled_qty": "0",
                "avg_fill_price": None,
            }

    foreign = Foreign()
    holder = {}

    def fence():
        return _permit(holder["scenario"])

    gateway = Supply(original, st=st, execution_scope="paper", claim_fence=fence)
    gateway._broker = foreign
    scenario = build_scenario(st, mode=Mode.paper, broker=foreign, gateway=gateway)
    holder["scenario"] = scenario
    with pytest.raises(ImportError, match="not resolved"):
        scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert foreign.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_halt_probe_keyboard_interrupt_covers_the_intent(st, monkeypatch):
    """An interrupt in the post-intent HALT probe must not leave the intent open."""
    scenario, broker = _paper_gateway(st)

    def halt(_path):
        if scenario.st.audit.unresolved_intents():
            raise KeyboardInterrupt
        return False

    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    with pytest.raises(KeyboardInterrupt):
        scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_final_admission_holds_the_lock_through_send(st):
    """The last admission check keeps the lock until the broker call returns."""
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
    seen = {}
    real_submit = broker.submit_order

    def submit_order(canonical):
        seen["held"] = lock.held
        return real_submit(canonical)

    broker.submit_order = submit_order
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert outcome.ok is True
    assert seen["held"] is True
    assert lock.held is False
    assert broker.calls == 1
    assert scenario.st.audit.unresolved_intents() == []


def test_quote_that_expires_during_the_halt_probe_does_not_submit(st, monkeypatch):
    """Quote age is part of the admission check after the HALT probe."""
    scenario, broker = _paper_gateway(st)
    fences = {"n": 0}

    class Guard:
        def __init__(self):
            self.reason = None

        def stale_reason(self, symbol, *, now, limit_s):
            return self.reason

    guard = Guard()
    scenario.st.market_data = guard

    def fence():
        fences["n"] += 1

    def halt(_path):
        if fences["n"] > 0:
            guard.reason = "quote expired during the halt probe"
            fences["n"] = 0
        return False

    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "market_data"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_authority_revoked_during_the_halt_probe_does_not_submit(st, monkeypatch):
    """Authority is re-read after the probe, with the intent covered."""
    scenario, broker = _paper_gateway(st)
    original = scenario.gateway._final_audit_authority
    calls = {"n": 0}

    def authority(state):
        calls["n"] += 1
        if calls["n"] >= 2:
            return False, "authority revoked during the halt probe"
        return original(state)

    monkeypatch.setattr(scenario.gateway, "_final_audit_authority", authority)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert calls["n"] >= 2
    assert outcome.ok is False
    assert outcome.stage == "audit_integrity"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_halt_at_the_last_probe_with_a_failed_abort_write_returns(st, monkeypatch):
    """A failed abort under the final probe must not stick the admission lock.

    The kill switch takes that same lock. A hang here stalls shutdown too.
    """
    scenario, broker = _paper_gateway(st)
    lock = scenario.st.safety_admission_lock

    def halt(_path):
        return lock.locked()

    def result(*_args, **_kwargs):
        raise RuntimeError("abort write failed")

    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    monkeypatch.setattr(scenario.st.audit, "result", result)
    box: dict = {}

    def run():
        try:
            box["outcome"] = scenario.gateway.submit(
                scenario.order,
                claim_fence=lambda: None,
                **scenario.submit_kwargs(),
            )
        except BaseException as exc:  # noqa: BLE001 - the bound is the assertion
            box["exc"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(2.0)
    assert not worker.is_alive()
    assert "exc" not in box
    outcome = box["outcome"]
    assert outcome.ok is False
    assert outcome.stage == "halt"
    assert broker.calls == 0

    done = threading.Event()

    def shutdown():
        scenario.st.begin_safety_shutdown()
        done.set()

    killer = threading.Thread(target=shutdown, daemon=True)
    killer.start()
    assert done.wait(2.0)
    killer.join(0.5)
    assert not killer.is_alive()


def test_failed_success_result_stays_visible_to_reconcile(st, monkeypatch):
    """A landed send whose success result fails must not be closed as a halt."""
    from orchestrator.reconcile import reconcile_startup

    scenario, broker = _paper_gateway(st)
    real_result = scenario.st.audit.result

    def result(intent_entry, op, refs=None, payload=None):
        refs = refs or {}
        if refs.get("broker_order_id") and not refs.get("aborted"):
            raise RuntimeError("success result write failed")
        return real_result(intent_entry, op, refs=refs, payload=payload)

    monkeypatch.setattr(scenario.st.audit, "result", result)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert broker.calls == 1
    assert outcome.ok is False
    assert outcome.stage == "broker_error"
    with scenario.st.session_factory() as session:
        rows = session.execute(select(AuditEntry)).scalars().all()
    assert not any(
        (row.refs or {}).get("aborted") == "halt"
        or "before transport" in str((row.payload or {}).get("aborted", ""))
        for row in rows
    )
    alerts: list[str] = []
    report = reconcile_startup(
        scenario.st, broker_history_fn=lambda _oid: [], alerter=alerts.append
    )
    assert report.ok is False
    assert report.ambiguous_aborts or any("NO covering" in alert for alert in alerts)


def test_broker_exception_with_a_failed_result_write_does_not_stick_the_lock(st, monkeypatch):
    """A broker failure after the send writes its record only once the lock is free."""
    scenario, broker = _paper_gateway(st)
    real_result = scenario.st.audit.result
    calls = {"n": 0}

    def submit_order(_canonical):
        raise RuntimeError("broker down")

    def result(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("broker error write failed")
        return real_result(*args, **kwargs)

    monkeypatch.setattr(broker, "submit_order", submit_order)
    monkeypatch.setattr(scenario.st.audit, "result", result)
    box: dict = {}

    def run():
        try:
            box["outcome"] = scenario.gateway.submit(
                scenario.order,
                claim_fence=lambda: None,
                **scenario.submit_kwargs(),
            )
        except BaseException as exc:  # noqa: BLE001 - the bound is the assertion
            box["exc"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(2.0)
    assert not worker.is_alive()
    assert isinstance(box.get("exc"), RuntimeError)
    with scenario.st.session_factory() as session:
        rows = session.execute(select(AuditEntry)).scalars().all()
    assert not any(
        (row.refs or {}).get("aborted") == "halt"
        or "before transport" in str((row.payload or {}).get("aborted", ""))
        for row in rows
    )
    broker_error = any((row.refs or {}).get("aborted") == "broker_error" for row in rows)
    assert broker_error or scenario.st.audit.unresolved_intents()

    done = threading.Event()

    def shutdown():
        scenario.st.begin_safety_shutdown()
        done.set()

    killer = threading.Thread(target=shutdown, daemon=True)
    killer.start()
    assert done.wait(2.0)
    killer.join(0.5)
    assert not killer.is_alive()


def test_quote_stale_only_during_final_admission_enters_watch_only(st):
    """A quote that expires on the locked re-read still trips watch-only."""
    scenario, broker = _paper_gateway(st)
    lock = scenario.st.safety_admission_lock

    class Guard:
        def stale_reason(self, symbol, *, now, limit_s):
            if lock.locked():
                return "quote expired during final admission"
            return None

    scenario.st.market_data = Guard()
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert outcome.ok is False
    assert outcome.stage == "market_data"
    assert "expired" in outcome.reason
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []
    assert scenario.breaker.watch_only_reason() is not None


def test_mode_changed_at_the_final_halt_probe_does_not_submit(st, monkeypatch):
    """A mode change at the last HALT probe is re-read before the send hold."""
    scenario, broker = _paper_gateway(st)
    calls = {"n": 0}

    def halt(_path):
        calls["n"] += 1
        if calls["n"] == 6:
            scenario.ladder.set_mode(Mode.review_live, actor="op")
        return False

    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert calls["n"] >= 6
    assert outcome.ok is False
    assert outcome.stage == "mode"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_sibling_approved_at_the_final_halt_probe_does_not_submit(st, monkeypatch):
    """An approved sibling at the last HALT probe is inside the cap check before send."""
    scenario, broker = _capped_paper(st)
    calls = {"n": 0}

    def halt(_path):
        calls["n"] += 1
        if calls["n"] == 6:
            _approved_sibling(st)
        return False

    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert calls["n"] >= 6
    assert outcome.ok is False
    assert outcome.stage == "pending_exposure"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


@pytest.mark.parametrize("target", ["approval", "order"])
def test_post_send_local_write_failure_stays_counted_and_visible(st, monkeypatch, target):
    """A failed approval or order-state write after a landed send stays visible.

    Reconcile must flag it, and pending exposure must still count the order.
    """
    from orchestrator.reconcile import reconcile_startup
    from orchestrator.state import EVENT_ORDER_TRANSITION

    scenario, broker = _paper_gateway(st)
    real = scenario.st.audit.record_state_change
    seen = {"n": 0}

    def record(obj, event_type, *args, **kwargs):
        hit = broker.calls >= 1 and (
            (
                target == "approval"
                and event_type == EVENT_ADJUDICATION
                and obj.__class__.__name__ == "ApprovalRecord"
            )
            or (target == "order" and event_type == EVENT_ORDER_TRANSITION)
        )
        if hit:
            seen["n"] += 1
            if seen["n"] == 1:
                raise OSError(f"{target} write failed")
        return real(obj, event_type, *args, **kwargs)

    monkeypatch.setattr(scenario.st.audit, "record_state_change", record)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert broker.calls == 1
    assert outcome.ok is False
    assert outcome.stage == "broker_error"
    with scenario.st.session_factory() as session:
        row = session.get(Order, scenario.order.id)
        assert row.state in {"approved", "submitted", "partially_filled"}
        rows = session.execute(select(AuditEntry)).scalars().all()
    assert not any(
        (entry.refs or {}).get("aborted") == "halt"
        or "before transport" in str((entry.payload or {}).get("aborted", ""))
        for entry in rows
    )
    probe = create_order(
        scenario.st, proposal_id=new_id("p"), risk_decision_id=new_id("rd"),
        account="paper", symbol="MSFT", side="buy", qty="1",
        order_type="limit", limit_price="1", tif="day",
        notional="1", order_tuple_hash=new_id("h"), actor="claude",
    )
    _ok, total, _cap = scenario.gateway._pending_ok(scenario.st, probe)
    assert total >= Decimal("1500")
    alerts: list[str] = []
    report = reconcile_startup(
        scenario.st, broker_history_fn=lambda _oid: [], alerter=alerts.append
    )
    assert report.ok is False
    assert report.ambiguous_aborts or report.alerted or any(
        "AMBIGUOUS" in alert or "NO covering" in alert for alert in alerts
    )


def _install_gap(scenario, mutate):
    """Apply ``mutate`` once the send hold has started on 031c3c0b.

    Two HALT probes take the admission lock and release it. The next hold is
    the send. ``watch_only_reason`` runs there, after mode and exposure were
    already read. A mutation at that point is the old gap.
    """
    inner = scenario.st.safety_admission_lock
    locked_halts = {"n": 0}
    fired = {"v": False}
    real_watch = scenario.breaker.watch_only_reason
    real_halt = __import__("brokers.gateway", fromlist=["_halt_active"])._halt_active

    def halt(path):
        active = real_halt(path)
        if inner.locked():
            locked_halts["n"] += 1
        return active

    def watch():
        reason = real_watch()
        if inner.locked() and locked_halts["n"] >= 2 and not fired["v"]:
            fired["v"] = True
            mutate()
        return reason

    return halt, watch, fired


def test_mode_changed_in_the_send_hold_gap_does_not_submit(st, monkeypatch):
    """A mode flip while the send hold is entered must not reach the broker.

    On 031c3c0b the flip lands after mode was read and is not read again.
    """
    scenario, broker = _paper_gateway(st)

    def mutate():
        scenario.ladder.set_mode(Mode.review_live, actor="op")

    halt, watch, fired = _install_gap(scenario, mutate)
    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    scenario.breaker.watch_only_reason = watch
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert fired["v"] is True
    assert outcome.ok is False
    assert outcome.stage == "mode"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_sibling_approved_in_the_send_hold_gap_does_not_submit(st, monkeypatch):
    """A sibling approved as the send hold is entered stays inside the cap."""
    scenario, broker = _capped_paper(st)

    def mutate():
        _approved_sibling(st)

    halt, watch, fired = _install_gap(scenario, mutate)
    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    scenario.breaker.watch_only_reason = watch
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert fired["v"] is True
    assert outcome.ok is False
    assert outcome.stage == "pending_exposure"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_halt_set_in_the_send_hold_gap_does_not_submit(st, monkeypatch):
    """HALT set as the send hold is entered must not reach the broker."""
    scenario, broker = _paper_gateway(st)

    def mutate():
        set_halt(default_halt_path(), reason="HALT in the send-hold gap")

    halt, watch, fired = _install_gap(scenario, mutate)
    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    scenario.breaker.watch_only_reason = watch
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert fired["v"] is True
    assert outcome.ok is False
    assert outcome.stage == "halt"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_lease_expired_in_the_send_hold_gap_does_not_submit(st, monkeypatch):
    """A lease that expires after the send hold's first sample must not be sent.

    The first monotonic read after the gap stays fresh, which is the sample
    031c3c0b takes and then does not repeat. A later read is past the lease.
    """
    scenario, broker = _paper_gateway(st)
    real_mono = time.monotonic
    jump = {"v": False, "reads": 0}

    def mono():
        now = real_mono()
        if not jump["v"]:
            return now
        jump["reads"] += 1
        if jump["reads"] == 1:
            return now
        return now + _ADMISSION_WAIT_BUDGET_S + 5

    def mutate():
        jump["v"] = True

    halt, watch, fired = _install_gap(scenario, mutate)
    monkeypatch.setattr("brokers.gateway._halt_active", halt)
    monkeypatch.setattr("brokers.gateway.time.monotonic", mono)
    scenario.breaker.watch_only_reason = watch
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert fired["v"] is True
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_landed_order_whose_local_writes_both_fail_blocks_the_next_buy(st, monkeypatch):
    """A landed $1,500 whose approval and recovery writes both fail sticks.

    The row stays proposed, so the cap alone would admit another $1,500 under
    $2,000. The sticky marker refuses that buy until reconcile can clear it.
    Reconcile leaves the marker in place while the row is still proposed.
    """
    from orchestrator.reconcile import reconcile_startup
    from tests.brokers.test_pending_exposure_chain import _fresh_submittable

    scenario, broker = _capped_paper(st)
    real = scenario.st.audit.record_state_change
    fail = {"v": True}

    def record(obj, event_type, *args, **kwargs):
        if fail["v"] and broker.calls >= 1:
            raise OSError("local write failed")
        return real(obj, event_type, *args, **kwargs)

    monkeypatch.setattr(scenario.st.audit, "record_state_change", record)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    fail["v"] = False
    assert broker.calls == 1
    assert outcome.ok is False
    assert outcome.stage == "broker_error"
    with scenario.st.session_factory() as session:
        row = session.get(Order, scenario.order.id)
        assert row.state == "proposed"
    alerts: list[str] = []
    report = reconcile_startup(
        scenario.st, broker_history_fn=lambda _oid: [], alerter=alerts.append
    )
    assert report.ok is False
    assert any("unreconciled" in alert for alert in alerts)
    second, approval, risk = _fresh_submittable(
        st, scenario, proposal="p-next", nonce="n-next",
    )
    nxt = scenario.gateway.submit(
        second,
        **scenario.submit_kwargs(
            approval=approval, risk_decision=risk, token_nonce="n-next",
        ),
    )
    assert nxt.ok is False
    assert nxt.stage == "pending_exposure"
    assert broker.calls == 1
    assert scenario.st.audit.unresolved_intents() == []


def _fail_writes_after_send(scenario, broker, monkeypatch):
    """Raise on every local state write once the broker has been called."""
    real = scenario.st.audit.record_state_change
    fail = {"v": True}

    def record(obj, event_type, *args, **kwargs):
        if fail["v"] and broker.calls >= 1:
            raise OSError("local write failed")
        return real(obj, event_type, *args, **kwargs)

    monkeypatch.setattr(scenario.st.audit, "record_state_change", record)
    return fail


def _latest_landed_kind(st, order_id):
    from brokers.gateway import UNRECONCILED_LANDED_EXPOSURE

    with st.session_factory() as session:
        rows = session.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == UNRECONCILED_LANDED_EXPOSURE)
            .order_by(AuditEntry.seq)
        ).scalars().all()
    kind = None
    for row in rows:
        payload = row.payload or {}
        marked = str((row.refs or {}).get("order_id") or payload.get("order_id") or "")
        if marked == str(order_id):
            kind = payload.get("kind")
    return kind


def _reload_order(st, order_id):
    with st.session_factory() as session:
        row = session.get(Order, order_id)
        session.expunge(row)
        return row


def test_unreadable_landed_state_still_blocks_the_next_buy(st, monkeypatch):
    """A state read that raises after both local writes failed must still block.

    3b8483d6 reads the row outside the marker try. The raise skips the marker,
    the integrity admission, and the ambiguous-transport record, so the next
    $1,500 buy under a $2,000 cap is sent.
    """
    from tests.brokers.test_pending_exposure_chain import _fresh_submittable

    scenario, broker = _capped_paper(st)
    fail = _fail_writes_after_send(scenario, broker, monkeypatch)
    real_factory = scenario.st.session_factory
    in_persist = {"v": False}
    real_persist = scenario.gateway._persist_unreconciled_landed

    def persist(st_arg, order, actor):
        in_persist["v"] = True
        try:
            return real_persist(st_arg, order, actor)
        finally:
            in_persist["v"] = False

    def factory(*args, **kwargs):
        if in_persist["v"]:
            raise RuntimeError("landed state unreadable")
        return real_factory(*args, **kwargs)

    monkeypatch.setattr(scenario.gateway, "_persist_unreconciled_landed", persist)
    monkeypatch.setattr(scenario.st, "session_factory", factory)
    try:
        scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    except RuntimeError:
        pass
    finally:
        fail["v"] = False
    assert broker.calls == 1
    assert _latest_landed_kind(scenario.st, scenario.order.id) == "block"
    second, approval, risk = _fresh_submittable(
        st, scenario, proposal="p-next", nonce="n-next",
    )
    nxt = scenario.gateway.submit(
        second,
        **scenario.submit_kwargs(
            approval=approval, risk_decision=risk, token_nonce="n-next",
        ),
    )
    assert nxt.ok is False
    assert nxt.stage in {"pending_exposure", "audit_integrity"}
    assert broker.calls == 1
    assert scenario.st.audit.unresolved_intents() == []


def test_local_cancel_with_open_broker_history_keeps_the_landed_block(st, monkeypatch):
    """A local cancel does not clear a landed block while the broker is still open."""
    from orchestrator.reconcile import reconcile_startup
    from tests.brokers.test_pending_exposure_chain import _fresh_submittable

    scenario, broker = _capped_paper(st)
    fail = _fail_writes_after_send(scenario, broker, monkeypatch)
    outcome = scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    fail["v"] = False
    assert broker.calls == 1
    assert outcome.stage == "broker_error"
    row = _reload_order(scenario.st, scenario.order.id)
    assert row.state == "proposed"
    transition(scenario.st, row, "cancelled", actor="operator", reason="local cancel")
    alerts: list[str] = []
    report = reconcile_startup(
        scenario.st,
        broker_history_fn=lambda oid: [{"order_id": oid, "status": "accepted"}],
        alerter=alerts.append,
    )
    assert report.ok is False
    assert _latest_landed_kind(scenario.st, scenario.order.id) == "block"
    assert any("unreconciled" in alert for alert in alerts)
    second, approval, risk = _fresh_submittable(
        st, scenario, proposal="p-next", nonce="n-next",
    )
    nxt = scenario.gateway.submit(
        second,
        **scenario.submit_kwargs(
            approval=approval, risk_decision=risk, token_nonce="n-next",
        ),
    )
    assert nxt.ok is False
    assert nxt.stage == "pending_exposure"
    assert broker.calls == 1


def test_local_cancel_with_terminal_broker_history_clears_the_landed_block(st, monkeypatch):
    """A local cancel keeps the block until broker history shows a terminal status.

    3b8483d6 stops counting a terminal row on its own, so the buy before
    reconcile is admitted. A cancelled broker history is what clears the marker.
    """
    from orchestrator.reconcile import reconcile_startup
    from tests.brokers.test_pending_exposure_chain import _fresh_submittable

    scenario, broker = _capped_paper(st)
    fail = _fail_writes_after_send(scenario, broker, monkeypatch)
    scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    fail["v"] = False
    assert broker.calls == 1
    row = _reload_order(scenario.st, scenario.order.id)
    transition(scenario.st, row, "cancelled", actor="operator", reason="local cancel")
    probe, approval, risk = _fresh_submittable(
        st, scenario, proposal="p-probe", nonce="n-probe",
    )
    pre = scenario.gateway.submit(
        probe,
        **scenario.submit_kwargs(
            approval=approval, risk_decision=risk, token_nonce="n-probe",
        ),
    )
    assert pre.ok is False
    assert pre.stage == "pending_exposure"
    assert broker.calls == 1
    alerts: list[str] = []
    reconcile_startup(
        scenario.st,
        broker_history_fn=lambda oid: [{"order_id": oid, "status": "cancelled"}],
        alerter=alerts.append,
    )
    assert _latest_landed_kind(scenario.st, scenario.order.id) == "clear"
    third, approval3, risk3 = _fresh_submittable(
        st, scenario, proposal="p-next", nonce="n-next",
    )
    nxt = scenario.gateway.submit(
        third,
        **scenario.submit_kwargs(
            approval=approval3, risk_decision=risk3, token_nonce="n-next",
        ),
    )
    assert nxt.ok is True
    assert broker.calls == 2


def test_quote_that_expires_during_the_send_hold_exposure_scan_does_not_submit(
    st, monkeypatch,
):
    """A quote that goes stale during the locked exposure scan must not be sent.

    The cheap quote sample runs before that scan. 3b8483d6 does not sample it
    again before transport.
    """
    scenario, broker = _paper_gateway(st)
    scanned = {"v": False}
    real_pending = scenario.gateway._pending_ok

    def pending(st_arg, order):
        result = real_pending(st_arg, order)
        if st_arg.safety_admission_lock.locked():
            scanned["v"] = True
        return result

    monkeypatch.setattr(scenario.gateway, "_pending_ok", pending)

    class Guard:
        def stale_reason(self, symbol, *, now, limit_s):
            if scanned["v"]:
                return "quote expired during the send-hold exposure scan"
            return None

    scenario.st.market_data = Guard()
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert scanned["v"] is True
    assert outcome.ok is False
    assert outcome.stage == "market_data"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []
    assert scenario.breaker.watch_only_reason() is not None


def test_quote_stall_past_the_lease_does_not_submit(st, monkeypatch):
    """A 6s stall in the final quote read must not outrun the claim lease.

    63473731 samples HALT and the lease, then reads the quote, and sends
    with no check after that read.
    """
    scenario, broker = _paper_gateway(st)
    scanned = {"v": False}
    real_pending = scenario.gateway._pending_ok

    def pending(st_arg, order):
        result = real_pending(st_arg, order)
        if st_arg.safety_admission_lock.locked():
            scanned["v"] = True
        return result

    monkeypatch.setattr(scenario.gateway, "_pending_ok", pending)

    class Guard:
        def stale_reason(self, symbol, *, now, limit_s):
            if scanned["v"] and scenario.st.safety_admission_lock.locked():
                time.sleep(6)
            return None

    scenario.st.market_data = Guard()
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
    assert scanned["v"] is True
    assert outcome.ok is False
    assert outcome.stage == "claim_fence"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []


def test_locally_terminal_landed_row_still_writes_the_block(st, monkeypatch):
    """A local cancel during a landed send must still leave the exposure block.

    63473731 returns before writing the marker when the mutable row is already
    terminal, with no broker confirmation. The next $1,500 buy is sent.
    """
    from sqlalchemy import update

    from tests.brokers.test_pending_exposure_chain import _fresh_submittable

    scenario, broker = _capped_paper(st)
    fail = _fail_writes_after_send(scenario, broker, monkeypatch)
    real_persist = scenario.gateway._persist_unreconciled_landed

    def persist(st_arg, order, actor):
        with scenario.st.session_factory() as session, session.begin():
            session.execute(
                update(Order).where(Order.id == order.id).values(state="cancelled")
            )
        return real_persist(st_arg, order, actor)

    monkeypatch.setattr(scenario.gateway, "_persist_unreconciled_landed", persist)
    scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    fail["v"] = False
    assert broker.calls == 1
    assert _latest_landed_kind(scenario.st, scenario.order.id) == "block"
    second, approval, risk = _fresh_submittable(
        st, scenario, proposal="p-next", nonce="n-next",
    )
    nxt = scenario.gateway.submit(
        second,
        **scenario.submit_kwargs(
            approval=approval, risk_decision=risk, token_nonce="n-next",
        ),
    )
    assert nxt.ok is False
    assert nxt.stage == "pending_exposure"
    assert broker.calls == 1


def test_mutable_open_state_does_not_clear_the_landed_block(st, monkeypatch):
    """A keyless state edit must not discharge a landed block.

    63473731 clears when the mutable column says approved, then a restore to
    proposed lets the next $1,500 buy through a $2,000 cap.
    """
    from sqlalchemy import update

    from orchestrator.reconcile import reconcile_startup
    from tests.brokers.test_pending_exposure_chain import _fresh_submittable

    scenario, broker = _capped_paper(st)
    fail = _fail_writes_after_send(scenario, broker, monkeypatch)
    scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    fail["v"] = False
    assert broker.calls == 1
    with scenario.st.session_factory() as session, session.begin():
        session.execute(
            update(Order).where(Order.id == scenario.order.id).values(state="approved")
        )
    history_reads = {"n": 0}

    def history(oid):
        history_reads["n"] += 1
        return [{"order_id": oid, "status": "accepted"}]

    report = reconcile_startup(
        scenario.st, broker_history_fn=history, alerter=lambda _msg: None,
    )
    assert report.ok is False
    assert _latest_landed_kind(scenario.st, scenario.order.id) == "block"
    with scenario.st.session_factory() as session, session.begin():
        session.execute(
            update(Order).where(Order.id == scenario.order.id).values(state="proposed")
        )
    second, approval, risk = _fresh_submittable(
        st, scenario, proposal="p-next", nonce="n-next",
    )
    nxt = scenario.gateway.submit(
        second,
        **scenario.submit_kwargs(
            approval=approval, risk_decision=risk, token_nonce="n-next",
        ),
    )
    assert nxt.ok is False
    assert nxt.stage == "pending_exposure"
    assert broker.calls == 1
    assert history_reads["n"] == 0 or _latest_landed_kind(
        scenario.st, scenario.order.id
    ) == "block"


def test_cancel_then_reopen_in_broker_history_keeps_the_landed_block(st, monkeypatch):
    """The latest broker status decides. A cancel followed by a reopen does not.

    63473731 treats any terminal record as confirmation, so this history clears
    the block. A later fill is terminal only when it is the latest status.
    """
    from orchestrator.reconcile import _broker_order_is_terminal, reconcile_startup
    from tests.brokers.test_pending_exposure_chain import _fresh_submittable

    scenario, broker = _capped_paper(st)
    fail = _fail_writes_after_send(scenario, broker, monkeypatch)
    scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    fail["v"] = False
    row = _reload_order(scenario.st, scenario.order.id)
    transition(scenario.st, row, "cancelled", actor="operator", reason="local cancel")
    oid = scenario.order.id
    reopened = [
        {"order_id": oid, "status": "cancelled"},
        {"order_id": oid, "status": "accepted"},
    ]
    filled = [
        {"order_id": oid, "status": "cancelled"},
        {"order_id": oid, "status": "filled"},
    ]
    assert _broker_order_is_terminal(reopened, oid) is False
    assert _broker_order_is_terminal(filled, oid) is True
    reconcile_startup(
        scenario.st,
        broker_history_fn=lambda _oid: reopened,
        alerter=lambda _msg: None,
    )
    assert _latest_landed_kind(scenario.st, oid) == "block"
    probe, approval, risk = _fresh_submittable(
        st, scenario, proposal="p-probe", nonce="n-probe",
    )
    pre = scenario.gateway.submit(
        probe,
        **scenario.submit_kwargs(
            approval=approval, risk_decision=risk, token_nonce="n-probe",
        ),
    )
    assert pre.ok is False
    assert pre.stage == "pending_exposure"
    assert broker.calls == 1
    reconcile_startup(
        scenario.st,
        broker_history_fn=lambda _oid: filled,
        alerter=lambda _msg: None,
    )
    assert _latest_landed_kind(scenario.st, oid) == "clear"
    third, approval3, risk3 = _fresh_submittable(
        st, scenario, proposal="p-next", nonce="n-next",
    )
    nxt = scenario.gateway.submit(
        third,
        **scenario.submit_kwargs(
            approval=approval3, risk_decision=risk3, token_nonce="n-next",
        ),
    )
    assert nxt.ok is True
    assert broker.calls == 2


def test_failed_landed_block_write_blocks_after_restart(st, monkeypatch):
    """A marker write that fails must still refuse the next buy after restart.

    63473731 only remembers the failure on the in-memory usage tracker. A new
    state on the same database admits the next $1,500 buy.
    """
    from orchestrator.halt import default_halt_path, halt_is_set
    from orchestrator.mode_ladder import ModeLadder
    from orchestrator.state import OrchestratorState
    from tests.brokers.conftest import KEY, approved_risk_recheck
    from tests.brokers.test_pending_exposure_chain import _fresh_submittable

    scenario, broker = _capped_paper(st)
    fail = _fail_writes_after_send(scenario, broker, monkeypatch)
    real_append = scenario.st.audit.append

    def append(event_type, *args, **kwargs):
        if event_type == "unreconciled_landed_exposure":
            raise OSError("marker write failed")
        return real_append(event_type, *args, **kwargs)

    monkeypatch.setattr(scenario.st.audit, "append", append)
    try:
        scenario.gateway.submit(scenario.order, **scenario.submit_kwargs())
    except OSError:
        pass
    finally:
        fail["v"] = False
    assert broker.calls == 1
    restarted = OrchestratorState.build(
        scenario.st.session_factory,
        hmac_key=KEY,
        settings=scenario.st.settings,
        clock=scenario.st.clock,
    )
    assert restarted.usage.integrity_error is None
    broker2 = _CountingPaper()
    gateway = OrderGateway(
        broker2, st=restarted, execution_scope="paper",
        pending_exposure_cap=Decimal("2000"),
    )
    ladder = ModeLadder(restarted)
    ladder.set_mode(Mode.paper, actor="op")
    holder = type("Holder", (), {"clock": restarted.clock, "gateway": gateway})()
    second, approval, risk = _fresh_submittable(
        restarted, holder, proposal="p-restart", nonce="n-restart",
    )
    nxt = gateway.submit(
        second,
        approval=approval,
        risk_decision=risk,
        ladder=ladder,
        breaker_engine=scenario.breaker.__class__(restarted),
        risk_recheck=approved_risk_recheck,
        positions={},
        token_nonce="n-restart",
        producing_runtime="claude",
        clock=restarted.clock,
    )
    assert nxt.ok is False
    assert broker2.calls == 0
    assert halt_is_set(default_halt_path()) or _latest_landed_kind(
        restarted, scenario.order.id
    ) == "block"


def test_quote_that_expires_during_the_final_risk_refresh_does_not_submit(st):
    scenario, broker = _paper_gateway(st)
    calls = {"n": 0}

    class Guard:
        def __init__(self):
            self.reason = None

        def stale_reason(self, symbol, *, now, limit_s):
            return self.reason

    guard = Guard()
    scenario.st.market_data = guard

    def recheck(_positions):
        calls["n"] += 1
        if calls["n"] >= 4:
            guard.reason = "quote expired during the final risk refresh"
        return {"decision": "approved", "failed_checks": []}

    outcome = scenario.gateway.submit(
        scenario.order,
        claim_fence=lambda: None,
        **scenario.submit_kwargs(risk_recheck=recheck),
    )
    assert calls["n"] >= 4
    assert outcome.ok is False
    assert outcome.stage == "market_data"
    assert broker.calls == 0
    assert scenario.st.audit.unresolved_intents() == []
