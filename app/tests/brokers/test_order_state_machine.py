"""WP7 unit tests — the order-lifecycle FSM (G7; Inv 6).

Legal/illegal adjacency, terminal immutability, and the load-bearing guarantee that EVERY
transition writes its covering hash-chained AuditEntry BEFORE returning success.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from config.settings import Settings
from core.clock import FrozenClock
from brokers.order_state_machine import (
    IllegalOrderTransition,
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    create_order,
    legal_order_transition,
    transition,
)
from orchestrator.state import EVENT_ORDER_TRANSITION, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
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
    transition(st, o, "submitted", actor="claude", reason="sent")
    transition(st, o, "filled", actor="broker", reason="filled")
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
