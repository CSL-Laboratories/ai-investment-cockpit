"""Independent BUG-148 probes: reservation overlap, ambiguous submits and bad positions."""
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from brokers.order_state_machine import transition
from brokers.sell_reservations import pending_sell_quantities
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from tests.brokers.test_bug148_sell_backing import _risk, _sell, _service


def _position(qty="100", **extra):
    return {"symbol": "AAPL", "qty": qty, "market_value": "1000", **extra}


def test_external_and_local_unsubmitted_reservations_are_additive(st):
    # Broker holds 30 shares for an outside order. A separate locally approved sale
    # reserves 40 more, but has never reached the broker and is absent from qty_available.
    service = _service(st, [_position(qty_available="70")])
    _sell(st, qty=40, status="approved")
    assert _risk(service, 30)["decision"] == "approved"
    result = _risk(service, 31)
    assert result["decision"] == "rejected", result
    assert "sell_backed" in result["failed_checks"]


def test_submitted_reservation_is_not_subtracted_twice(st):
    service = _service(st, [_position(qty_available="40")])
    _sell(st, qty=60, status="submitted")
    assert _risk(service, 40)["decision"] == "approved"
    assert "sell_backed" in _risk(service, 41)["failed_checks"]


def test_unknown_submission_does_not_claim_overlap_with_external_broker_reservation(st):
    service = _service(st, [_position(qty_available="70")])
    order = _sell(st, qty=40, status="approved")
    st.audit.intent("order_submit", refs={"order_id": order.id},
        payload={"symbol": "AAPL", "side": "sell", "qty": "40",
                 "execution_scope": "paper", "mode": "paper"})
    assert _risk(service, 30)["decision"] == "approved"
    result = _risk(service, 31)
    assert result["decision"] == "rejected", result


def test_confirmed_submission_can_use_broker_available_without_double_counting(st):
    service = _service(st, [_position(qty_available="60")])
    _sell(st, qty=40, status="submitted")
    assert _risk(service, 60)["decision"] == "approved"
    assert "sell_backed" in _risk(service, 61)["failed_checks"]


@pytest.mark.parametrize("terminal", ["rejected", "cancelled", "expired"])
def test_local_terminal_after_lost_submit_response_does_not_release_shares(st, terminal):
    service = _service(st)
    order = _sell(st, qty=60, status="proposed")
    intent = st.audit.intent("order_submit", refs={"order_id": order.id},
        payload={"symbol": "AAPL", "side": "sell", "qty": "60",
                 "execution_scope": "paper", "mode": "paper"})
    st.audit.result(intent, "order_submit", refs={"order_id": order.id, "aborted": "broker_error"},
        payload={"aborted": "synthetic lost response: execution outcome unknown"})
    # Rejection/veto is a local operator decision, not a broker cancellation ACK.
    transition(st, order, terminal, actor="local_api:operator", reason="operator veto")
    assert st.audit.verify_chain().ok
    result = _risk(service, 41)
    assert result["decision"] == "rejected", result


def test_positive_quantity_with_explicit_short_side_is_not_long_backing(st):
    service = _service(st, [_position(side="short")])
    result = _risk(service, 1)
    assert result["decision"] == "rejected", result


def test_delayed_terminal_result_for_old_intent_does_not_release_newer_unknown_submit(st):
    service = _service(st)
    order = _sell(st, qty=60, status="proposed")
    old = st.audit.intent("order_submit", refs={"order_id": order.id},
        payload={"symbol": "AAPL", "side": "sell", "qty": "60",
                 "execution_scope": "paper", "mode": "paper"})
    st.audit.intent("order_submit", refs={"order_id": order.id},
        payload={"symbol": "AAPL", "side": "sell", "qty": "60",
                 "execution_scope": "paper", "mode": "paper"})
    st.audit.result(old, "order_submit", refs={"order_id": order.id,
        "broker_order_id": "old-rejected-request"}, payload={"status": "rejected"})
    assert st.audit.verify_chain().ok
    result = _risk(service, 41)
    assert result["decision"] == "rejected", result


@pytest.mark.parametrize("value", [True, False, None, "NaN", "Infinity", "-0.1", "bad"])
def test_malformed_available_quantity_fails_closed(st, value):
    service = _service(st, [_position(qty_available=value)])
    assert _risk(service, 1)["decision"] == "rejected"


def test_fractional_long_holding_does_not_round_up(st):
    service = _service(st, [_position(qty="100.5")])
    assert _risk(service, 100)["decision"] == "approved"
    assert "sell_backed" in _risk(service, 101)["failed_checks"]


def test_existing_short_does_not_back_another_sale(st):
    service = _service(st, [_position(qty="-100", side="short")])
    assert _risk(service, 1)["decision"] == "rejected"


def test_fractional_outstanding_reservation_does_not_round_down(st):
    service = _service(st)
    _sell(st, qty=Decimal("60.5"), status="approved")
    assert pending_sell_quantities(st)["AAPL"] == Decimal("60.5")
    assert _risk(service, 39)["decision"] == "approved"
    assert "sell_backed" in _risk(service, 40)["failed_checks"]


def test_excluding_current_approval_keeps_other_approved_order_reserved(st):
    service = _service(st)
    _sell(st, qty=60, status="approved")
    current = _sell(st, qty=40, status="approved")
    assert _risk(service, 40, order_id=current.id)["decision"] == "approved"
    assert "sell_backed" in _risk(service, 41, order_id=current.id)["failed_checks"]


@pytest.mark.parametrize("damage", ["side", "symbol", "erase_transition"])
def test_keyless_damage_never_moves_reservation_to_another_symbol_or_side(st, damage):
    service = _service(st)
    order = _sell(st, qty=60, status="submitted")
    with st.session_factory() as session, session.begin():
        if damage == "erase_transition":
            latest = session.execute(select(AuditEntry).where(
                AuditEntry.refs["row_id"].as_string() == order.id
            ).order_by(AuditEntry.seq.desc())).scalars().first()
            session.execute(delete(AuditEntry).where(AuditEntry.id == latest.id))
        else:
            session.execute(update(Order).where(Order.id == order.id).values({
                damage: "buy" if damage == "side" else "MSFT",
            }))
    assert _risk(service, 1)["decision"] == "rejected"
