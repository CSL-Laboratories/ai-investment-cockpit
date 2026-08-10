"""BUG-052 / BUG-062 — the submit path must CONSUME the broker's answer.

BUG-052: a broker response that already reports a fill must advance the FSM past
'submitted'. Before the fix the only production ``transition()`` callers were the two in
``submit()`` ('approved', 'submitted'), so the filled/cancelled edges were test-only and
the SEC-07 pending-exposure ledger never drained — the cap became a one-time budget that
permanently self-blocked the only wired order path.

BUG-062: a broker exception between the two-phase intent and its covering result left the
intent dangling forever (the production reconciler is wired with ``_no_broker_history`` and
can only ALERT a not-landed intent, never heal it) and propagated raw out of the locked
submit.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from schemas.models.order import Order

from tests.brokers.conftest import build_scenario


class FillAtSubmitBroker:
    """A paper-shaped broker whose order fills synchronously at submit — normal Alpaca
    paper behavior for a marketable order."""

    def __init__(self, status="filled", filled_qty=Decimal("10"), avg=Decimal("150")):
        self.status, self.filled_qty, self.avg = status, filled_qty, avg

    def submit_order(self, canonical):
        return {"broker_order_id": "alp-1", "status": self.status,
                "filled_qty": self.filled_qty, "avg_fill_price": self.avg}


class ExplodingBroker:
    """A broker whose submit raises — a transient 5xx/timeout on POST /v2/orders."""

    def submit_order(self, canonical):
        raise RuntimeError("alpaca 503: gateway timeout")


def _row_state(st, order_id):
    with st.session_factory() as s:
        return s.execute(select(Order).where(Order.id == order_id)).scalar_one().state


def test_fill_at_submit_reaches_the_filled_state(st):
    sc = build_scenario(st, broker=FillAtSubmitBroker())
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert out.ok and out.broker_status == "filled"
    assert _row_state(st, sc.order.id) == "filled"
    assert st.audit.verify_chain().ok


def test_partial_fill_at_submit_reaches_partially_filled(st):
    sc = build_scenario(st, broker=FillAtSubmitBroker(status="partially_filled",
                                                      filled_qty=Decimal("4")))
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert out.ok
    assert _row_state(st, sc.order.id) == "partially_filled"


def test_unmapped_broker_status_stays_submitted(st):
    """'accepted'/'new'/anything indeterminate must NOT invent a fill."""
    sc = build_scenario(st, broker=FillAtSubmitBroker(status="accepted",
                                                      filled_qty=Decimal("0"), avg=None))
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert out.ok
    assert _row_state(st, sc.order.id) == "submitted"


def test_filled_orders_drain_the_pending_exposure_cap(st):
    """THE self-block: with the default $10k cap and $1.5k orders that fill at submit,
    the seventh order used to be refused with 'pending exposure ... would exceed cap' —
    and, with nothing ever leaving 'submitted', every order after it, forever."""
    for i in range(8):
        sc = build_scenario(st, broker=FillAtSubmitBroker())
        out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
        assert out.ok, f"order {i}: refused at {out.stage}: {out.reason}"


def test_broker_exception_completes_the_two_phase_pair(st):
    sc = build_scenario(st, broker=ExplodingBroker())
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "broker_error"
    assert st.audit.unresolved_intents() == []          # the dangling-intent hole
    assert _row_state(st, sc.order.id) == "proposed"    # never claimed submitted
    assert st.audit.verify_chain().ok
