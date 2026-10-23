"""OrderGateway claim-fence hook split out of CSL-218 / #173.

These tests cover only the gateway.py submit changes: the claim_fence callback,
the re-fence before transport, the pinned broker, the second HALT probe, and the
paper-profile flag. PaperOrderGateway itself stays on the authority-profile PR.
"""
from __future__ import annotations

import time

import pytest

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.gateway import OrderGateway
from config.settings import Mode
from orchestrator.halt import default_halt_path, set_halt
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
    gateway = _MarkedPaper(broker, st=st, execution_scope="live")
    scenario = build_scenario(st, mode=Mode.review_live, broker=broker, gateway=gateway)
    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=lambda: None, **scenario.submit_kwargs()
    )
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
