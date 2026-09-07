"""WP7 unit tests — the order-lifecycle FSM (G7; Inv 6).

Legal/illegal adjacency, terminal immutability, and the load-bearing guarantee that EVERY
transition writes its covering hash-chained AuditEntry BEFORE returning success.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.orm.exc import DetachedInstanceError

from config.settings import Settings
from core.clock import FrozenClock
from brokers.order_state_machine import (
    IllegalOrderTransition,
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    create_order,
    legal_order_transition,
    record_fill_progress,
    transition,
)
from orchestrator.state import EVENT_ORDER_TRANSITION, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from storage.audit import AuditIntegrityError, audit_meta
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"fsm-key"
CLK = FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'fsm.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=CLK)


def _new_order(st, state="proposed"):
    o = create_order(st, proposal_id="p1", risk_decision_id="rd1", account="paper",
                     symbol="AAPL", side="buy", qty=Decimal("10"), order_type="limit",
                     limit_price=Decimal("150"), tif="day", notional=Decimal("1500"),
                     order_tuple_hash="h1", actor="claude")
    if state != "proposed":
        transition(st, o, "approved", actor="claude")
        if state not in ("approved",):
            transition(st, o, state, actor="claude")
    return o


def _order_transitions(st):
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
    return [r for r in rows if r.event_type == EVENT_ORDER_TRANSITION]


def _fields(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _durable_state(st):
    """Compare all domain/audit fields and the exact signed watermark on refusal."""
    with st.session_factory() as session:
        orders = [_fields(row) for row in session.scalars(select(Order).order_by(Order.id))]
        ledger = [_fields(row) for row in session.scalars(
            select(AuditEntry).order_by(AuditEntry.seq)
        )]
        watermark = dict(session.execute(select(audit_meta)).all())
    return orders, ledger, watermark


def _detached_order(st, order_id):
    with st.session_factory() as session:
        row = session.get(Order, order_id)
        session.expunge(row)
        return row


def _assert_signed_row_agreement(st, order_id, from_state):
    with st.session_factory() as session:
        order = session.get(Order, order_id)
        signed = session.scalars(select(AuditEntry).order_by(AuditEntry.seq.desc())).first()
        assert signed.refs["row_id"] == order.id
        payload = signed.payload
        assert payload["from"] == from_state
        assert payload["to"] == order.state
        for field in (
            "proposal_id", "risk_decision_id", "order_tuple_hash", "approval_id",
            "symbol", "side", "broker_order_id",
        ):
            assert payload[field] == getattr(order, field), field
        for field in ("notional", "filled_qty", "avg_fill_price"):
            value = getattr(order, field)
            if value is None:
                assert payload[field] is None, field
            else:
                assert Decimal(payload[field]) == value, field


class InterruptedOrderWrite(BaseException):
    """Exercise transaction unwinding outside the Exception hierarchy."""


def _fail_before_order_commit(st, monkeypatch, failure):
    original = st.audit._bump_hwm

    def mark_order_write(session, seq, **kwargs):
        original(session, seq, **kwargs)
        session.info["fixture_order_audit_write"] = True

    monkeypatch.setattr(st.audit, "_bump_hwm", mark_order_write)

    def stop(session):
        if session.info.get("fixture_order_audit_write"):
            raise failure("isolated order pre-commit interruption")

    event.listen(st.session_factory.class_, "before_commit", stop)
    return lambda: event.remove(st.session_factory.class_, "before_commit", stop)


# ---- pure adjacency predicate ----------------------------------------------------------
def test_legal_edges_match_spec():
    assert legal_order_transition("proposed", "approved")
    assert legal_order_transition("approved", "submitted")
    assert legal_order_transition("submitted", "partially_filled")
    assert legal_order_transition("submitted", "filled")
    assert legal_order_transition("partially_filled", "filled")


@pytest.mark.parametrize("src,dst", [
    ("proposed", "submitted"),        # must go via approved
    ("proposed", "filled"),
    ("proposed", "partially_filled"),
    ("approved", "filled"),           # must go via submitted
    ("approved", "partially_filled"),
    ("partially_filled", "rejected"),  # partially_filled cannot be rejected
    ("submitted", "approved"),        # no going back up
])
def test_illegal_edges_rejected(src, dst):
    assert not legal_order_transition(src, dst)


@pytest.mark.parametrize("term", sorted(TERMINAL_STATES))
def test_terminal_states_have_no_outgoing_edges(term):
    for dst in ("approved", "submitted", "filled", "cancelled"):
        assert not legal_order_transition(term, dst)


def test_canary_breach_reachable_from_every_nonterminal():
    for src in LEGAL_TRANSITIONS:
        assert legal_order_transition(src, "canary_breach")


# ---- create + transition write audit BEFORE returning ----------------------------------
def test_create_order_births_at_proposed_with_audit(st):
    o = _new_order(st)
    assert o.state == "proposed"
    trans = _order_transitions(st)
    assert len(trans) == 1
    p = trans[0].payload
    assert p["from"] is None and p["to"] == "proposed"
    # the covering entry references the order row (Inv 6) and the chain verifies.
    assert (trans[0].refs or {}).get("row_id") == o.id
    assert st.audit.verify_chain().ok


def test_each_transition_writes_a_covering_audit_entry(st):
    o = _new_order(st)
    transition(st, o, "approved", actor="claude", reason="human approved")
    _assert_signed_row_agreement(st, o.id, "proposed")
    transition(st, o, "submitted", actor="claude", reason="sent")
    _assert_signed_row_agreement(st, o.id, "approved")
    transition(st, o, "filled", actor="broker", reason="filled")
    _assert_signed_row_agreement(st, o.id, "submitted")
    trans = _order_transitions(st)
    # birth + 3 transitions = 4 audited steps, each linking from->to.
    assert [t.payload["to"] for t in trans] == ["proposed", "approved", "submitted", "filled"]
    assert [t.payload["from"] for t in trans] == [None, "proposed", "approved", "submitted"]
    # every entry covers THIS order row and carries the SEC-07 hash.
    assert all((t.refs or {}).get("row_id") == o.id for t in trans)
    assert all(t.payload["order_tuple_hash"] == "h1" for t in trans)
    assert st.audit.verify_chain().ok


def test_illegal_transition_raises_and_writes_nothing(st):
    o = _new_order(st)
    before = len(_order_transitions(st))
    with pytest.raises(IllegalOrderTransition):
        transition(st, o, "submitted", actor="claude")     # proposed->submitted illegal
    # nothing persisted; the order is untouched; the chain is unchanged.
    assert len(_order_transitions(st)) == before
    with st.session_factory() as s:
        assert s.get(Order, o.id).state == "proposed"
    assert st.audit.verify_chain().ok


def test_transition_out_of_terminal_raises(st):
    o = _new_order(st, state="approved")
    transition(st, o, "rejected", actor="claude")
    with pytest.raises(IllegalOrderTransition):
        transition(st, o, "submitted", actor="claude")     # rejected is terminal
    with st.session_factory() as s:
        assert s.get(Order, o.id).state == "rejected"


def test_transition_persists_broker_fields(st):
    o = _new_order(st, state="approved")
    transition(st, o, "submitted", actor="claude", broker_order_id="brk-1",
               filled_qty=Decimal("4"), avg_fill_price=Decimal("150.25"))
    with st.session_factory() as s:
        row = s.get(Order, o.id)
        assert row.state == "submitted" and row.broker_order_id == "brk-1"
        assert Decimal(str(row.filled_qty)) == Decimal("4")
        assert Decimal(str(row.avg_fill_price)) == Decimal("150.25")
    _assert_signed_row_agreement(st, o.id, "approved")


# BUG-125 / Astra append-hardening F1/F2: promote the four research refusal contracts.
def test_competing_order_transition_refuses_stale_terminal_source(st):
    original = _new_order(st, state="approved")
    stale = _detached_order(st, original.id)
    caller_before = _fields(stale)
    transition(st, original, "cancelled", actor="fixture")
    before = _durable_state(st)

    with pytest.raises(IllegalOrderTransition):
        transition(st, stale, "submitted", actor="fixture")

    assert _durable_state(st) == before
    assert _fields(stale) == caller_before
    assert st.audit.verify_chain().ok


def test_competing_fill_transition_preserves_signed_row_agreement(st):
    original = _new_order(st, state="submitted")
    stale = _detached_order(st, original.id)
    caller_before = _fields(stale)
    transition(st, original, "partially_filled", actor="fixture", filled_qty=Decimal("3"),
               avg_fill_price=Decimal("150"))
    _assert_signed_row_agreement(st, original.id, "submitted")
    before = _durable_state(st)

    with pytest.raises(IllegalOrderTransition):
        transition(st, stale, "cancelled", actor="fixture")

    assert _durable_state(st) == before
    assert _fields(stale) == caller_before
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("failure", [RuntimeError, InterruptedOrderWrite])
def test_interrupted_commit_does_not_publish_a_detached_transition(st, monkeypatch, failure):
    order = _new_order(st)
    caller_before = _fields(order)
    before = _durable_state(st)
    remove = _fail_before_order_commit(st, monkeypatch, failure)
    try:
        with pytest.raises(failure, match="isolated order pre-commit"):
            transition(st, order, "approved", actor="fixture", reason="pending approval")
    finally:
        remove()

    assert _durable_state(st) == before
    assert st.audit.verify_chain().ok
    try:
        caller_after = _fields(order)
    except DetachedInstanceError:
        return  # Invalidating every failed publication also prevents unsafe continuation.
    assert caller_after == caller_before


@pytest.mark.parametrize("failure", [RuntimeError, InterruptedOrderWrite])
def test_retry_after_interruption_cannot_skip_an_unsigned_transition(st, monkeypatch, failure):
    order = _new_order(st)
    before = _durable_state(st)
    remove = _fail_before_order_commit(st, monkeypatch, failure)
    try:
        with pytest.raises(failure, match="isolated order pre-commit"):
            transition(st, order, "approved", actor="fixture")
    finally:
        remove()
    assert _durable_state(st) == before

    with pytest.raises((IllegalOrderTransition, DetachedInstanceError)):
        transition(st, order, "submitted", actor="fixture")

    assert _durable_state(st) == before
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("operation", ["transition", "fill_progress"])
def test_same_state_fill_progress_refuses_stale_reader(st, operation):
    original = _new_order(st, state="submitted")
    transition(st, original, "partially_filled", filled_qty=Decimal("2"),
               avg_fill_price=Decimal("150"))
    stale = _detached_order(st, original.id)
    caller_before = _fields(stale)
    record_fill_progress(st, original, filled_qty=Decimal("4"),
                         avg_fill_price=Decimal("151"))
    _assert_signed_row_agreement(st, original.id, "partially_filled")
    before = _durable_state(st)

    with pytest.raises(IllegalOrderTransition):
        if operation == "transition":
            transition(st, stale, "cancelled", actor="fixture")
        else:
            record_fill_progress(st, stale, filled_qty=Decimal("3"), actor="fixture")

    assert _durable_state(st) == before
    assert _fields(stale) == caller_before
    assert st.audit.verify_chain().ok


def test_fill_progress_signs_retained_average_price(st):
    order = _new_order(st, state="submitted")
    transition(st, order, "partially_filled", filled_qty=Decimal("2"),
               avg_fill_price=Decimal("150.25"), reason="first fill")
    record_fill_progress(st, order, filled_qty=Decimal("4"))

    _assert_signed_row_agreement(st, order.id, "partially_filled")
    assert _detached_order(st, order.id).avg_fill_price == Decimal("150.25")
    assert st.audit.verify_chain().ok


def test_missing_durable_order_refuses_transition(st):
    order = _new_order(st)
    caller_before = _fields(order)
    with st.session_factory() as session, session.begin():
        session.execute(delete(Order).where(Order.id == order.id))
    before = _durable_state(st)

    with pytest.raises(IllegalOrderTransition):
        transition(st, order, "approved", actor="fixture")

    assert _durable_state(st) == before
    assert _fields(order) == caller_before


@pytest.mark.parametrize("field", ["proposal_id", "risk_decision_id", "order_tuple_hash"])
def test_transition_refuses_mismatched_provenance_override(st, field):
    order = _new_order(st)
    caller_before = _fields(order)
    before = _durable_state(st)

    with pytest.raises(IllegalOrderTransition):
        transition(st, order, "approved", actor="fixture", **{field: "different-authority"})

    assert _durable_state(st) == before
    assert _fields(order) == caller_before
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("field,value", [
    ("state", "approved"),
    ("filled_qty", Decimal("4")),
    ("avg_fill_price", Decimal("149")),
    ("order_tuple_hash", "unsigned-tuple"),
])
def test_fresh_reader_refuses_durable_fields_disagreeing_with_signed_history(st, field, value):
    order = _new_order(st, state="submitted")
    transition(st, order, "partially_filled", filled_qty=Decimal("2"),
               avg_fill_price=Decimal("150"))
    with st.session_factory() as session, session.begin():
        session.execute(update(Order).where(Order.id == order.id).values(**{field: value}))
    fresh = _detached_order(st, order.id)
    caller_before = _fields(fresh)
    before = _durable_state(st)

    with pytest.raises((IllegalOrderTransition, AuditIntegrityError)):
        transition(st, fresh, "cancelled", actor="fixture")

    assert _durable_state(st) == before
    assert _fields(fresh) == caller_before
