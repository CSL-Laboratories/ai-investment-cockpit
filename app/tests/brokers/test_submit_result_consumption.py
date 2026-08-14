"""BUG-052 / BUG-062 — the submit path must CONSUME the broker's answer.

BUG-052: a broker response that already reports a fill must advance the FSM past
'submitted'. Before the fix the only production ``transition()`` callers were the two in
``submit()`` ('approved', 'submitted'), so the filled/cancelled edges were test-only and
the SEC-07 pending-exposure ledger never drained — the cap became a one-time budget that
permanently self-blocked the only wired order path.

BUG-062: a broker exception between the two-phase intent and its covering result left the
intent dangling forever (before RW-15 the production reconciler had no broker-history reader and
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


def test_broker_exception_feeds_the_breaker_signal(st):
    """BUG-058: the failed submit is the BRK_BROKER_ERRORS production source — each one
    increments the counter the triage tick samples."""
    assert getattr(st, "broker_errors_since_sample", 0) == 0
    sc = build_scenario(st, broker=ExplodingBroker())
    sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert st.broker_errors_since_sample == 1


# ---- adversarial-round hardening (BUG-052/062 round 2) ---------------------------------
class GarbageBroker:
    """A broker that RETURNS (does not raise) a non-dict — F1's crash-after-the-try."""

    def __init__(self, garbage):
        self._garbage = garbage

    def submit_order(self, canonical):
        return self._garbage


class ExoticTypesBroker:
    """JSON-hostile field types that used to crash inside the audit-result construction."""

    def submit_order(self, canonical):
        return {"broker_order_id": Decimal("12345"), "status": b"accepted",
                "filled_qty": "not-a-number", "avg_fill_price": float("nan")}


def _events(st, event_type):
    from sqlalchemy import select as _select

    from schemas.models.audit_entry import AuditEntry

    with st.session_factory() as s:
        rows = s.execute(_select(AuditEntry)).scalars().all()
    return [r for r in rows if r.event_type == event_type]


def test_none_response_takes_the_covered_abort_path(st):
    sc = build_scenario(st, broker=GarbageBroker(None))
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "broker_error"
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


def test_string_response_takes_the_covered_abort_path(st):
    sc = build_scenario(st, broker=GarbageBroker("HTTP 502 Bad Gateway"))
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "broker_error"
    assert st.audit.unresolved_intents() == []


def test_exotic_field_types_are_sanitized_not_crashed(st):
    sc = build_scenario(st, broker=ExoticTypesBroker())
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert out.ok                                       # the submit itself succeeded
    assert st.audit.unresolved_intents() == []
    assert _row_state(st, sc.order.id) == "submitted"   # garbage fill data drains nothing
    assert st.audit.verify_chain().ok


def test_filled_status_with_zero_fill_does_not_drain_the_cap(st):
    """F3: a bare status string must not manufacture cap headroom."""
    sc = build_scenario(st, broker=FillAtSubmitBroker(status="filled",
                                                      filled_qty=Decimal("0"), avg=None))
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert out.ok
    assert _row_state(st, sc.order.id) == "submitted", "an inconsistent fill must stay open"
    assert _events(st, "order_ack_ignored"), "the ignored ACK must be audited"


def test_filled_status_with_wrong_qty_does_not_terminalize(st):
    sc = build_scenario(st, broker=FillAtSubmitBroker(status="filled",
                                                      filled_qty=Decimal("4")))  # qty is 10
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert out.ok
    assert _row_state(st, sc.order.id) == "submitted"


def test_partial_fill_requires_a_sane_quantity(st):
    sc = build_scenario(st, broker=FillAtSubmitBroker(status="partially_filled",
                                                      filled_qty=Decimal("10")))  # == qty
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert out.ok
    assert _row_state(st, sc.order.id) == "submitted"


def test_broker_error_abort_is_alerted_as_ambiguous_at_reconcile(st):
    """F2: the covering abort result must not erase the landed-after-timeout ambiguity —
    reconcile re-alerts it (and stays not-ok) until the order row is settled."""
    from orchestrator.reconcile import reconcile_startup

    sc = build_scenario(st, broker=ExplodingBroker())
    sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert st.audit.unresolved_intents() == []          # SEC-13 pair is complete...

    alerts: list[str] = []
    report = reconcile_startup(st, broker_history_fn=lambda oid: [], alerter=alerts.append)
    assert report.ambiguous_aborts, "...but the ambiguity must stay visible"
    assert report.ok is False
    assert any("AMBIGUOUS" in a for a in alerts)
