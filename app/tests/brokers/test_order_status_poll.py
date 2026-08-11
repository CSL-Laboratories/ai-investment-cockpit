"""RW-15 (minimal) / RW-17 finding D1 — the SEC-07 exhaustion trap, and its drain.

THE TRAP, reproduced in ``test_resting_limit_order_wedges_the_cap_until_the_poll_settles_it``:
a paper LIMIT order that does not cross returns ``accepted``, which ``gateway
._BROKER_STATUS_TO_STATE`` deliberately does not map, so the row stays ``submitted`` — an OPEN
state for ``_pending_ok``. Nothing drained it, so the pending-exposure sum only ever grew and
the SEC-07 cap turned into a one-way budget that eventually refused every order forever. RW-16
prices its copy orders as LIMIT at the gate quote, so that is the NORMAL path.

The rest of the file pins the fail-closed contract: the poll settles ONLY on a definitive
broker status with consistent fill data, never advances an order toward a broker (Inv 1: it is
not a second order path), and leaves the order OPEN — the over-counting, trade-refusing
direction — on every ambiguity. ``_pending_ok`` is neither weakened nor touched; the drain
happens because the FSM genuinely reaches a terminal state through a SIGNED transition.

Hermetic: the brokers conftest (FrozenClock, tmp SQLite, monkeypatched COCKPIT_HALT_PATH).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import update

from brokers.gateway import OrderGateway
from brokers.order_state_machine import transition
from brokers.order_status_poll import (
    ALL_POLL_OUTCOMES,
    POLLABLE_STATES,
    SETTLE_STATES,
    OrderStatusReport,
    reconcile_order_status,
    run_order_status_poll,
)
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order

from tests.brokers.conftest import build_scenario
from tests.brokers.test_pending_exposure_chain import _fresh_submittable

# order1 (1500) + order2 (1500) = 3000 > CAP, so order2 fits ONLY once order1 has drained.
CAP = Decimal("2000")


class RestingPaperBroker:
    """A paper broker that ACKs every order ``accepted`` and unfilled — exactly what Alpaca's
    paper API (and ``alpaca_paper.FakeAlpaca``) returns for a limit order that does not cross.
    ``submit_calls`` pins that the poll never submits anything."""

    def __init__(self):
        self.submit_calls: list[dict] = []
        self.status_calls: list[str] = []
        self.status_by_id: dict[str, dict] = {}

    def submit_order(self, canonical):
        oid = f"brk-{len(self.submit_calls) + 1}"
        self.submit_calls.append(dict(canonical))
        return {"broker_order_id": oid, "status": "accepted",
                "filled_qty": Decimal("0"), "avg_fill_price": None}

    # the INJECTED read seam — never an order write.
    def status_fn(self, broker_order_id):
        self.status_calls.append(broker_order_id)
        return self.status_by_id.get(
            broker_order_id,
            {"broker_order_id": broker_order_id, "status": "accepted",
             "filled_qty": Decimal("0"), "avg_fill_price": None},
        )


def _state(st, order_id) -> str | None:
    with st.session_factory() as s:
        row = s.get(Order, order_id)
        return None if row is None else row.state


def _resting_scenario(st, cap=CAP):
    """Submit ONE limit order through the real gateway; it comes back ``accepted`` and rests."""
    broker = RestingPaperBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker, pending_exposure_cap=cap),
                        broker=broker)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert out.ok and out.broker_status == "accepted"
    assert _state(st, sc.order.id) == "submitted"        # OPEN for _pending_ok
    return broker, sc


def _filled(broker_order_id, qty="10", price="150"):
    return {"broker_order_id": broker_order_id, "status": "filled",
            "filled_qty": Decimal(qty), "avg_fill_price": Decimal(price)}


# =======================================================================================
# (1) THE REGRESSION TEST for finding D1 — fails without brokers/order_status_poll.py
# =======================================================================================
def test_resting_limit_order_wedges_the_cap_until_the_poll_settles_it(st):
    """D1: a resting ``accepted`` limit order never drains, so the SEC-07 cap fills
    monotonically and the NEXT order is refused at gate (h) — permanently, with no human in the
    RW-17 queue to notice. One reconcile tick settles it and the lane trades again."""
    broker, sc = _resting_scenario(st)

    # THE TRAP: a second, fully-valid order is refused purely on the resting order's exposure.
    o2, appr2, rd2 = _fresh_submittable(st, sc)
    blocked = sc.gateway.submit(o2, **sc.submit_kwargs(approval=appr2, risk_decision=rd2,
                                                       token_nonce="n3"))
    assert not blocked.ok and blocked.stage == "pending_exposure"
    assert len(broker.submit_calls) == 1                  # order2 never reached the broker

    # THE DRAIN: the broker is now definitive about order1.
    broker.status_by_id["brk-1"] = _filled("brk-1")
    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)

    assert report.ok and report.polled == 1
    assert [o.outcome for o in report.outcomes] == ["settled"]
    assert report.settled[0].settled_to == "filled"
    assert _state(st, sc.order.id) == "filled"            # terminal -> no longer open
    assert st.audit.verify_chain().ok                     # settled through a SIGNED transition

    # ... and the cap has real headroom again, so the same order now passes gate (h).
    o3, appr3, rd3 = _fresh_submittable(st, sc, proposal="p4", symbol="TSLA", nonce="n4")
    ok_out = sc.gateway.submit(o3, **sc.submit_kwargs(approval=appr3, risk_decision=rd3,
                                                      token_nonce="n4"))
    assert ok_out.ok and ok_out.stage == "submitted"
    assert len(broker.submit_calls) == 2


# =======================================================================================
# (2) fail-closed: only a DEFINITIVE status with CONSISTENT fill data may drain the cap
# =======================================================================================
def test_indeterminate_status_never_settles_and_never_invents_a_fill(st):
    """``accepted`` / ``new`` / ``pending_new`` say nothing about the fill. The order stays
    OPEN (over-counted — the trade-REFUSING direction), tick after tick."""
    broker, sc = _resting_scenario(st)
    for status in ("accepted", "new", "pending_new", "weird_vendor_status"):
        broker.status_by_id["brk-1"] = {"broker_order_id": "brk-1", "status": status,
                                        "filled_qty": Decimal("0"), "avg_fill_price": None}
        report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
        assert [o.outcome for o in report.outcomes] == ["indeterminate"], status
        assert report.ok                       # a resting order is not a FAULT
        assert _state(st, sc.order.id) == "submitted", status


def test_filled_status_with_inconsistent_fill_data_is_ignored_and_audited(st):
    """F3, at poll time: a bare status string must not manufacture SEC-07 headroom. ``filled``
    with ``filled_qty=0`` (or a short qty) leaves the order open and appends the SAME
    ``order_ack_ignored`` event the gateway's submit-time branch appends."""
    broker, sc = _resting_scenario(st)
    broker.status_by_id["brk-1"] = {"broker_order_id": "brk-1", "status": "filled",
                                    "filled_qty": Decimal("0"), "avg_fill_price": None}
    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)

    assert [o.outcome for o in report.outcomes] == ["ack_ignored"]
    assert _state(st, sc.order.id) == "submitted"
    with st.session_factory() as s:
        ignored = [e for e in s.query(AuditEntry).all() if e.event_type == "order_ack_ignored"]
    assert len(ignored) == 1
    assert ignored[0].payload["source"] == "order_status_poll"
    assert (ignored[0].refs or {}).get("order_id") == sc.order.id

    # a SHORT fill claimed as complete is refused the same way.
    broker.status_by_id["brk-1"] = _filled("brk-1", qty="4")
    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert [o.outcome for o in report.outcomes] == ["ack_ignored"]
    assert _state(st, sc.order.id) == "submitted"


def test_a_reply_about_a_different_broker_order_is_not_evidence(st):
    """A record for another order id can never settle this one (the fail-closed rule
    ``orchestrator.reconcile._order_landed`` applies when healing)."""
    broker, sc = _resting_scenario(st)
    broker.status_by_id["brk-1"] = _filled("someone-elses-order")
    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert [o.outcome for o in report.outcomes] == ["id_mismatch"]
    assert _state(st, sc.order.id) == "submitted"


def test_unverified_chain_skips_the_whole_tick(st):
    """Never append settlements onto a chain that does not verify. No headroom is lost:
    ``_pending_ok`` already refuses every order while the chain is unhealed."""
    broker, sc = _resting_scenario(st)
    broker.status_by_id["brk-1"] = _filled("brk-1")
    with st.session_factory() as s, s.begin():
        s.execute(update(AuditEntry).where(AuditEntry.seq == 1).values(actor="tamper"))
    assert not st.audit.verify_chain().ok

    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert report.skipped == "chain_unverified" and not report.ok
    assert report.polled == 0
    assert broker.status_calls == []                       # the broker was not even asked
    assert _state(st, sc.order.id) == "submitted"


# =======================================================================================
# (3) resilience: one sick order never strands the others
# =======================================================================================
def test_one_failing_status_read_never_strands_the_orders_behind_it(st):
    """A raising / unusable ``status_fn`` reply is a NAMED, counted outcome and the tick
    continues — the exact isolation the ratchet needs, since a stranded order is what fills the
    cap in the first place."""
    broker, sc = _resting_scenario(st, cap=Decimal("100000"))
    o2, appr2, rd2 = _fresh_submittable(st, sc)
    assert sc.gateway.submit(o2, **sc.submit_kwargs(approval=appr2, risk_decision=rd2,
                                                    token_nonce="n3")).ok
    assert _state(st, o2.id) == "submitted"

    def flaky(broker_order_id):
        broker.status_calls.append(broker_order_id)
        if broker_order_id == "brk-1":
            raise TimeoutError("vendor timeout")
        return _filled("brk-2")

    report = reconcile_order_status(st, status_fn=flaky, clock=sc.clock)
    assert report.polled == 2
    assert sorted(o.outcome for o in report.outcomes) == ["read_failed", "settled"]
    assert not report.ok                                    # a failed read is a FAULT
    assert _state(st, sc.order.id) == "submitted"           # fail-safe: stays open
    assert _state(st, o2.id) == "filled"                    # the one behind it still settled


def test_a_non_dict_broker_reply_is_a_named_read_failure(st):
    """The gateway's ``_sanitize_broker_response`` shape contract, reused: a None/str reply is
    refused BEFORE anything dereferences it."""
    broker, sc = _resting_scenario(st)
    report = reconcile_order_status(st, status_fn=lambda _oid: None, clock=sc.clock)
    assert [o.outcome for o in report.outcomes] == ["read_failed"]
    assert _state(st, sc.order.id) == "submitted"


# =======================================================================================
# (4) Inv 1: this is NOT a second order path
# =======================================================================================
def test_the_poll_never_submits_and_can_never_reach_a_pre_broker_state():
    """Pinned at the type level AND asserted at import: the settle set contains only
    exposure-DRAINING states, so no edit can turn this tick into a path that advances an order
    toward a broker."""
    assert not (SETTLE_STATES & {"proposed", "approved", "submitted"})
    assert POLLABLE_STATES == {"submitted", "partially_filled"}
    assert {o.outcome for o in ()} <= ALL_POLL_OUTCOMES        # the set exists and is a frozenset
    assert isinstance(ALL_POLL_OUTCOMES, frozenset)


def test_a_full_tick_never_calls_the_broker_submit_path(st):
    """The only broker interaction is a status READ — no ``submit_order``, no token mint or
    consume, no approval read."""
    broker, sc = _resting_scenario(st)
    before = len(broker.submit_calls)
    broker.status_by_id["brk-1"] = _filled("brk-1")
    reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert len(broker.submit_calls) == before
    assert broker.status_calls == ["brk-1"]
    # the approval consumed at submit is untouched by the poll.
    assert sc.approval.single_use_consumed is True


def test_terminal_orders_are_never_re_polled(st):
    """A settled order leaves the candidate set entirely; a second tick asks the broker
    nothing and the terminal state is never re-opened."""
    broker, sc = _resting_scenario(st)
    broker.status_by_id["brk-1"] = _filled("brk-1")
    reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert _state(st, sc.order.id) == "filled"

    broker.status_calls.clear()
    broker.status_by_id["brk-1"] = {"broker_order_id": "brk-1", "status": "accepted",
                                    "filled_qty": Decimal("0"), "avg_fill_price": None}
    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert report.polled == 0 and broker.status_calls == []
    assert _state(st, sc.order.id) == "filled"


def test_orders_that_never_reached_a_broker_are_not_polled(st):
    """A ``proposed``/``approved`` order has no broker order to ask about; asking would be the
    first step of a second order path."""
    broker = RestingPaperBroker()
    sc = build_scenario(st, gateway=OrderGateway(broker), broker=broker)
    transition(st, sc.order, "approved", actor="op", reason="approved")
    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert report.polled == 0 and broker.status_calls == []
    assert _state(st, sc.order.id) == "approved"


# =======================================================================================
# (5) the other definitive statuses, partials, and account scoping
# =======================================================================================
@pytest.mark.parametrize("status,expected", [
    ("canceled", "cancelled"),
    ("cancelled", "cancelled"),
    ("expired", "expired"),
    ("rejected", "rejected"),
])
def test_every_definitive_terminal_status_drains_the_order(st, status, expected):
    """A resting DAY order the broker expires (or the operator cancels broker-side) must drain
    too — that is the ordinary end of an unfilled limit order, and the one the cap depends on."""
    broker, sc = _resting_scenario(st)
    broker.status_by_id["brk-1"] = {"broker_order_id": "brk-1", "status": status,
                                    "filled_qty": None, "avg_fill_price": None}
    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert [o.outcome for o in report.outcomes] == ["settled"]
    assert _state(st, sc.order.id) == expected


def test_partial_then_complete_fill_walks_the_fsm_without_re_signing_a_partial(st):
    """A partial advances the FSM once; a REPEATED partial is a legal-move no-op (DEC-04 — the
    remaining-notional refinement needs a new signed field and is NOT attempted here); the
    completing fill then settles it."""
    broker, sc = _resting_scenario(st)
    broker.status_by_id["brk-1"] = {"broker_order_id": "brk-1", "status": "partially_filled",
                                    "filled_qty": Decimal("4"), "avg_fill_price": Decimal("150")}
    r1 = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert [o.outcome for o in r1.outcomes] == ["settled"]
    assert _state(st, sc.order.id) == "partially_filled"

    r2 = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert [o.outcome for o in r2.outcomes] == ["no_change"]
    assert _state(st, sc.order.id) == "partially_filled"

    broker.status_by_id["brk-1"] = _filled("brk-1")
    r3 = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock)
    assert [o.outcome for o in r3.outcomes] == ["settled"]
    assert _state(st, sc.order.id) == "filled"


def test_accounts_filter_scopes_the_tick_to_one_brokers_orders(st):
    """With more than one broker composed, a reply must never be applied to an order the
    reader knows nothing about."""
    broker, sc = _resting_scenario(st)
    broker.status_by_id["brk-1"] = _filled("brk-1")
    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock,
                                    accounts={"some-other-account"})
    assert report.polled == 0 and broker.status_calls == []
    assert _state(st, sc.order.id) == "submitted"

    report = reconcile_order_status(st, status_fn=broker.status_fn, clock=sc.clock,
                                    accounts={"paper"})
    assert [o.outcome for o in report.outcomes] == ["settled"]


# =======================================================================================
# (6) the scheduler-shaped entry point
# =======================================================================================
def test_run_order_status_poll_is_a_named_no_op_without_a_status_reader(st):
    assert run_order_status_poll(st, st.clock)["skipped"] == "no_status_fn"


def test_run_order_status_poll_never_raises(st):
    """A bad tick must not wedge the scheduler job — the same posture as every RW-17 lane job."""
    def boom(_oid):
        raise RuntimeError("unreachable")

    broker, sc = _resting_scenario(st)
    out = run_order_status_poll(st, sc.clock, status_fn=boom)
    assert out["polled"] == 1 and out["settled"] == 0 and out["ok"] is False
    assert out["outcomes"] == ["read_failed"]
    assert _state(st, sc.order.id) == "submitted"


def test_empty_report_is_ok(st):
    assert OrderStatusReport().ok and OrderStatusReport().polled == 0
