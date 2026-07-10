"""W12 — startup reconciliation (security-hardening-backlog SEC-13).

The two-phase submit (``brokers/gateway.py``) writes an ``intent`` AuditEntry BEFORE the
broker call and a ``result`` entry AFTER, sharing the storage layer's two-phase primitives.
These tests simulate the crash window directly (write only the ``intent``, as a process
kill between the broker ACK and the result commit would leave behind) and prove:
  * the startup reconcile backfills the result when the (mocked) broker confirms the order;
  * it never invents a result when the broker has no record — that's an alert, not a guess;
  * an orphan ``RiskDecision`` (no covering AuditEntry) fails the 1:1 completeness gate;
  * a clean chain passes with no alerts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from config.settings import Settings
from core.clock import FrozenClock
from core.ids import new_id
from orchestrator.reconcile import reconcile_startup
from orchestrator.state import OrchestratorState
from schemas.models.risk_decision import RiskDecision
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"w12-reconcile-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'w12-reconcile.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(),
                                   clock=FrozenClock(OPEN_UTC))


def _crash_after_intent(st, *, order_id="ord-1"):
    """Simulate a process kill between the broker ACK and the result commit: only the
    intent lands (exactly what ``gateway.submit`` would have written before crashing)."""
    return st.audit.intent(
        "order_submit",
        refs={"order_id": order_id, "proposal_id": "p1", "risk_decision_id": "rd1",
             "order_tuple_hash": "h1"},
        payload={"symbol": "AAPL", "side": "buy", "qty": "10", "order_type": "market",
                "mode": "review_live", "producing_runtime": "claude"},
    )


def test_clean_chain_reconciles_with_no_alerts(st):
    res = reconcile_startup(st)
    assert res.ok and res.alerts == [] and res.resolved_intents == []


def test_crash_between_ack_and_commit_detected_and_alerted_when_unresolvable(st):
    intent = _crash_after_intent(st)
    res = reconcile_startup(st, broker_order_lookup=None)
    assert not res.ok
    kinds = [a.kind for a in res.alerts]
    assert "unresolved_intent" in kinds
    assert "completeness_gap" in kinds
    assert intent.seq in st.audit.unresolved_intents()   # still open — never silently resolved


def test_crash_between_ack_and_commit_backfilled_from_broker_history(st):
    intent = _crash_after_intent(st, order_id="ord-2")

    def lookup(order_id):
        assert order_id == "ord-2"
        return {"broker_order_id": "alp_9", "status": "filled",
               "filled_qty": "10", "avg_fill_price": "150.25"}

    res = reconcile_startup(st, broker_order_lookup=lookup)
    assert res.ok
    assert res.resolved_intents == [intent.seq]
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


def test_broker_has_no_record_is_an_alert_not_a_silent_resolve(st):
    """``broker_order_lookup`` returning None (the broker never saw this order) must NOT be
    treated as success — an unresolved intent with no broker confirmation stays unresolved
    and alerts, it never defaults to "assume filled" or "assume cancelled"."""
    _crash_after_intent(st, order_id="ord-3")

    def lookup(_order_id):
        return None

    res = reconcile_startup(st, broker_order_lookup=lookup)
    assert not res.ok
    assert any(a.kind == "unresolved_intent" for a in res.alerts)
    assert st.audit.unresolved_intents() != []


def test_orphan_risk_decision_fails_completeness_gate(st):
    """A RiskDecision written WITHOUT the covering AuditEntry (bypassing
    ``record_state_change``) must fail the 1:1 completeness invariant at startup."""
    rd = RiskDecision(
        id=new_id("rd"), proposal_id="p1", decision="approved", checks={},
        failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
        human_required=False, evaluated_at=st.clock.now(), created_at=st.clock.now(),
    )
    with st.session_factory() as s, s.begin():
        s.add(rd)   # bare add — deliberately skips record_state_change's audit pairing

    res = reconcile_startup(st)
    assert not res.ok
    assert any(a.kind == "completeness_gap" for a in res.alerts)


def test_non_order_submit_intent_is_never_auto_resolved(st):
    """Only ``order_submit`` intents consult the broker lookup; any other op is always an
    alert (there is nothing to ask a broker about)."""
    intent = st.audit.intent("some_other_op", refs={}, payload={})

    def lookup(_order_id):
        return {"broker_order_id": "x", "status": "filled", "filled_qty": "1",
               "avg_fill_price": "1"}

    res = reconcile_startup(st, broker_order_lookup=lookup)
    assert not res.ok
    assert intent.seq in st.audit.unresolved_intents()
