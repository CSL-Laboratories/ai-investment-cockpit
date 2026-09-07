"""Independent adversarial checks for the F1/F2 Order persistence contract."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError

from brokers import order_state_machine as order_fsm
from brokers.order_state_machine import (
    IllegalOrderTransition,
    record_fill_progress,
    transition,
)
from orchestrator.state import OrchestratorState
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from storage.audit import audit_meta
from tests.brokers import test_order_state_machine as fsm_fixture
from tests.storage import test_audit as storage_fixture

st = fsm_fixture.st


def _detached(st, order_id):
    with st.session_factory() as session:
        row = session.get(Order, order_id)
        session.expunge(row)
        return row


def _snapshot(st):
    """Capture every domain/audit field and both signed watermark rows."""
    with st.session_factory() as session:
        return (
            tuple(
                tuple(getattr(row, column.key) for column in Order.__table__.columns)
                for row in session.scalars(select(Order).order_by(Order.id))
            ),
            tuple(
                tuple(getattr(row, column.key) for column in AuditEntry.__table__.columns)
                for row in session.scalars(select(AuditEntry).order_by(AuditEntry.seq))
            ),
            tuple(session.execute(select(audit_meta).order_by(audit_meta.c.k)).all()),
        )


def _latest_payload(st):
    with st.session_factory() as session:
        return session.scalars(
            select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)
        ).one().payload


@pytest.mark.parametrize("tamper", [
    {"state": "approved"},
    {"filled_qty": Decimal("3")},
    {"avg_fill_price": Decimal("155")},
    {"broker_order_id": "unsigned-broker-id"},
])
def test_freshly_loaded_tampered_order_cannot_receive_a_signature(st, tamper):
    original = fsm_fixture._new_order(st, state="submitted")
    with st.session_factory() as session, session.begin():
        session.execute(update(Order).where(Order.id == original.id).values(**tamper))
    supplied = _detached(st, original.id)
    before = _snapshot(st)
    # A fresh detached object agrees with SQLite, but SQLite disagrees with signed history.
    assert st.audit.verify_chain().ok
    with pytest.raises(IllegalOrderTransition):
        transition(st, supplied, "cancelled", actor="independent_review")
    assert _snapshot(st) == before
    assert supplied.state == tamper.get("state", "submitted")


def test_independent_writer_after_authority_reads_refuses_snapshot_upgrade(st):
    supplied = fsm_fixture._new_order(st, state="approved")
    competitor = _detached(st, supplied.id)
    other = OrchestratorState.build(
        st.session_factory, hmac_key=fsm_fixture.KEY, settings=st.settings, clock=st.clock
    )
    committed = []

    def commit_winner_before_loser_flush(session, _flush_context, _instances):
        if committed or not any(
            isinstance(row, Order) and row.id == supplied.id and row.state == "submitted"
            for row in session.dirty
        ):
            return
        # This callback runs after the losing FSM has read its source authority. The
        # independent state container commits while that read snapshot is still open.
        committed.append(None)
        transition(other, competitor, "cancelled", actor="independent_winner")
        committed[0] = _snapshot(st)

    event.listen(st.session_factory, "before_flush", commit_winner_before_loser_flush)
    try:
        with pytest.raises(OperationalError):
            transition(st, supplied, "submitted", actor="independent_loser")
    finally:
        event.remove(st.session_factory, "before_flush", commit_winner_before_loser_flush)
    assert len(committed) == 1
    assert _snapshot(st) == committed[0]
    assert supplied.state == "approved"
    assert competitor.state == "cancelled"
    assert st.audit.verify_chain().ok


def test_postcommit_acknowledgement_loss_cannot_continue_from_old_caller(st):
    supplied = fsm_fixture._new_order(st)
    before = _snapshot(st)

    class AcknowledgementLost(RuntimeError):
        pass

    def mark_order_write(session, _flush_context, _instances):
        if any(isinstance(row, Order) for row in session.dirty | session.new):
            session.info["fsm_review_order_write"] = True

    def lose_acknowledgement(session):
        if session.info.get("fsm_review_order_write"):
            raise AcknowledgementLost("isolated exception after actual COMMIT")

    event.listen(st.session_factory, "before_flush", mark_order_write)
    event.listen(st.session_factory, "after_commit", lose_acknowledgement)
    try:
        with pytest.raises(AcknowledgementLost):
            transition(st, supplied, "approved", actor="independent_review")
    finally:
        event.remove(st.session_factory, "before_flush", mark_order_write)
        event.remove(st.session_factory, "after_commit", lose_acknowledgement)

    committed = _snapshot(st)
    assert committed != before
    assert _detached(st, supplied.id).state == "approved"
    assert _latest_payload(st)["to"] == "approved"
    assert supplied.state == "proposed"
    assert st.audit.verify_chain().ok
    # A postcommit exception cannot undo committed history. Nor may the old caller append
    # a second proposed->approved event and manufacture a new signed predecessor.
    with pytest.raises(IllegalOrderTransition):
        transition(st, supplied, "approved", actor="independent_review")
    assert _snapshot(st) == committed


def test_persisted_numeric_rounding_is_the_signed_and_returned_value(st):
    supplied = fsm_fixture._new_order(st, state="submitted")
    transition(
        st, supplied, "partially_filled", actor="independent_review",
        filled_qty=Decimal("3.123456789123"), avg_fill_price=Decimal("150.123456"),
    )
    durable = _detached(st, supplied.id)
    payload = _latest_payload(st)
    assert Decimal(payload["filled_qty"]) == durable.filled_qty == supplied.filled_qty
    assert Decimal(payload["avg_fill_price"]) == durable.avg_fill_price == supplied.avg_fill_price
    assert durable.filled_qty == Decimal("3.1234567891")
    assert durable.avg_fill_price == Decimal("150.1235")
    transition(st, supplied, "cancelled", actor="independent_review")
    assert st.audit.verify_chain().ok


def test_fill_progress_rounding_cannot_publish_zero_durable_progress(st):
    supplied = fsm_fixture._new_order(st, state="submitted")
    transition(st, supplied, "partially_filled", filled_qty=Decimal("3"),
               avg_fill_price=Decimal("150"))
    before = _snapshot(st)
    with pytest.raises((IllegalOrderTransition, ValueError)):
        record_fill_progress(st, supplied, filled_qty=Decimal("3.00000000001"))
    assert _snapshot(st) == before
    assert supplied.filled_qty == Decimal("3")
    assert supplied.avg_fill_price == Decimal("150")


def test_pending_approval_assignment_preserves_existing_gateway_call_contract(st):
    supplied = fsm_fixture._new_order(st)
    assert supplied.approval_id is None
    # Both existing gateway and reconciler bind this field immediately before transition.
    supplied.approval_id = "approval-bound-before-transition"
    transition(st, supplied, "approved", actor="independent_review")
    durable = _detached(st, supplied.id)
    assert durable.approval_id == supplied.approval_id == _latest_payload(st)["approval_id"]
    transition(st, supplied, "submitted", actor="independent_review")
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("failure", [RuntimeError, storage_fixture.InterruptedStateWrite])
@pytest.mark.parametrize("point", [
    "state_flushed", "audit_flushed", "watermark_deleted", "watermark_unsigned",
    "signed_watermark", "before_commit",
])
@pytest.mark.parametrize("operation", ["transition", "fill_progress"])
def test_fsm_interruptions_leave_every_caller_and_durable_field_unchanged(
    st, monkeypatch, operation, point, failure,
):
    supplied = fsm_fixture._new_order(st)
    if operation == "fill_progress":
        transition(st, supplied, "approved")
        transition(st, supplied, "submitted")
        transition(st, supplied, "partially_filled", filled_qty=Decimal("2"),
                   avg_fill_price=Decimal("150"))
    caller_before = tuple(
        getattr(supplied, column.key) for column in Order.__table__.columns
    )
    before = _snapshot(st)
    with monkeypatch.context() as patch:
        remove = storage_fixture._interrupt_state_change(
            st.session_factory, st.audit, patch, point, failure
        )
        try:
            with pytest.raises(failure, match=f"isolated {point}"):
                if operation == "transition":
                    transition(st, supplied, "approved", reason="must not publish")
                else:
                    record_fill_progress(st, supplied, filled_qty=Decimal("3"),
                                         avg_fill_price=Decimal("151"),
                                         reason="must not publish")
        finally:
            remove()

    assert _snapshot(st) == before
    assert tuple(getattr(supplied, column.key) for column in Order.__table__.columns) == caller_before
    assert st.audit.verify_chain().ok
    if operation == "transition":
        with pytest.raises(IllegalOrderTransition):
            transition(st, supplied, "submitted")
        assert _snapshot(st) == before
    else:
        # A deliberately fresh transaction may retry cumulative progress against the
        # unchanged durable predecessor; the interrupted in-memory fill never escaped.
        record_fill_progress(st, supplied, filled_qty=Decimal("3"))
        assert supplied.filled_qty == Decimal("3")
        assert Decimal(_latest_payload(st)["filled_qty"]) == Decimal("3")
        assert st.audit.verify_chain().ok


@pytest.mark.parametrize("approval_id", [None, "legacy-unsigned-approval"])
def test_legacy_birth_omissions_cannot_authorize_nonnull_binding(st, monkeypatch, approval_id):
    original_payload = order_fsm._transition_payload

    def legacy_payload(row, from_state, reason):
        payload = original_payload(row, from_state, reason)
        if from_state is None:
            payload.pop("approval_id")
            payload.pop("broker_order_id")
        return payload

    # Emit the historical birth shape at original signing time; do not edit/re-sign history.
    with monkeypatch.context() as patch:
        patch.setattr(order_fsm, "_transition_payload", legacy_payload)
        supplied = order_fsm.create_order(
            st, proposal_id="p1", risk_decision_id="rd1", account="paper", symbol="AAPL",
            side="buy", qty=Decimal("10"), order_type="limit", limit_price=Decimal("150"),
            tif="day", notional=Decimal("1500"), order_tuple_hash="h1", approval_id=approval_id,
        )
    before = _snapshot(st)
    if approval_id is None:
        transition(st, supplied, "approved")
        assert supplied.state == "approved"
    else:
        with pytest.raises(IllegalOrderTransition):
            transition(st, supplied, "approved")
        assert _snapshot(st) == before
        assert supplied.state == "proposed"
    assert st.audit.verify_chain().ok
