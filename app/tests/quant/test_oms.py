"""QT-02 — order management gates.

The properties pinned:

  * POSITION MOVES ON FILLS AND ONLY ON FILLS. An intent is a wish, a fill is a fact. Every
    "my position drifted" bug in a trading system traces back to something that updated
    inventory on the wish.
  * An in-flight order counts as LIVE. Treating PENDING_NEW as not-live is how a strategy
    sends a duplicate: it checks its open orders, sees nothing, and quotes again.
  * Overfill and illegal transitions RAISE. They mean the venue and the OMS disagree about
    what was live, and clamping would leave the position quietly short of reality.
  * Client ids are deterministic, so two runs of one replay are diffable.
"""
from __future__ import annotations

from copy import copy
from dataclasses import replace

import pytest

from quant.events import Fill, Liquidity, Side
from quant.oms import (
    OMS,
    IllegalTransition,
    NewOrder,
    OrderIdSequence,
    OrderState,
    legal_transition,
)


def _fill(order_id: str, side: Side, qty: int, price: int = 100, ts: int = 10) -> Fill:
    return Fill(symbol="TEST", seq=1, exchange_ts=ts, recv_ts=ts, ingest_ts=ts,
                order_id=order_id, side=side, price_ticks=price, qty=qty,
                liquidity=Liquidity.MAKER)


def _new(client_id: str = "c1", side: Side = Side.BUY, qty: int = 100, price: int | None = 100) -> NewOrder:
    return NewOrder(client_id=client_id, symbol="TEST", side=side, qty=qty, price_ticks=price)


@pytest.fixture
def oms() -> OMS:
    return OMS()


# ---- position accounting -------------------------------------------------------------


def test_registering_an_intent_does_not_move_position(oms):
    """The single most important property in this file. An intent is a wish."""
    oms.register(_new(qty=500), strategy="mm")
    assert oms.position("TEST") == 0


def test_a_fill_moves_position_by_the_signed_quantity(oms):
    oms.register(_new("buy1", Side.BUY, 300), strategy="mm")
    oms.on_fill(_fill("buy1", Side.BUY, 300))
    assert oms.position("TEST") == 300

    oms.register(_new("sell1", Side.SELL, 500), strategy="mm")
    oms.on_fill(_fill("sell1", Side.SELL, 500))
    assert oms.position("TEST") == -200


def test_partial_fills_accumulate_and_the_order_stays_live(oms):
    oms.register(_new("c1", Side.BUY, 300), strategy="mm")
    order = oms.on_fill(_fill("c1", Side.BUY, 100))
    assert order.state is OrderState.PARTIALLY_FILLED
    assert order.remaining_qty == 200
    assert order.is_live
    oms.on_fill(_fill("c1", Side.BUY, 200))
    assert oms.get("c1").state is OrderState.FILLED
    assert not oms.get("c1").is_live
    assert oms.position("TEST") == 300


def test_average_fill_price_is_volume_weighted(oms):
    oms.register(_new("c1", Side.BUY, 300), strategy="mm")
    oms.on_fill(_fill("c1", Side.BUY, 100, price=100))
    oms.on_fill(_fill("c1", Side.BUY, 200, price=103))
    assert oms.get("c1").avg_fill_ticks == pytest.approx((100 * 100 + 200 * 103) / 300)


def test_positions_omits_flat_symbols_so_a_round_trip_leaves_no_residue(oms):
    oms.register(_new("b", Side.BUY, 100), strategy="mm")
    oms.on_fill(_fill("b", Side.BUY, 100))
    oms.register(_new("s", Side.SELL, 100), strategy="mm")
    oms.on_fill(_fill("s", Side.SELL, 100))
    assert oms.positions() == {}
    assert oms.position("TEST") == 0


# ---- liveness ---------------------------------------------------------------------------


def test_an_in_flight_order_counts_as_live(oms):
    """Otherwise a strategy checks its open orders, sees nothing, and double-sends. Under
    any nonzero latency that is two resting orders where one was intended."""
    oms.register(_new("c1"), strategy="mm")
    assert oms.get("c1").state is OrderState.PENDING_NEW
    assert oms.live_orders() and oms.live_orders()[0].client_id == "c1"


def test_terminal_orders_drop_out_of_live_orders(oms):
    oms.register(_new("c1"), strategy="mm")
    oms.on_cancelled("c1")
    assert oms.live_orders() == []


def test_live_orders_filter_by_strategy_and_symbol(oms):
    oms.register(NewOrder(client_id="a", symbol="AAA", side=Side.BUY, qty=1, price_ticks=1), strategy="mm")
    oms.register(NewOrder(client_id="b", symbol="BBB", side=Side.BUY, qty=1, price_ticks=1), strategy="mom")
    assert [o.client_id for o in oms.live_orders(strategy="mm")] == ["a"]
    assert [o.client_id for o in oms.live_orders(symbol="BBB")] == ["b"]


def test_exposure_counts_in_flight_orders_because_that_risk_is_already_committed(oms):
    """A limit that only counts acknowledged orders is a limit you can exceed by sending fast."""
    oms.register(_new("c1", Side.BUY, 100, price=50), strategy="mm")
    assert oms.exposure_ticks() == 5_000
    oms.on_acknowledged("c1")
    assert oms.exposure_ticks() == 5_000
    oms.on_fill(_fill("c1", Side.BUY, 100, price=50))
    assert oms.exposure_ticks() == 0


# ---- failure modes are loud ---------------------------------------------------------------


def test_overfilling_an_order_raises_rather_than_clamping(oms):
    """Clamping would paper over a venue/OMS disagreement and leave the position quietly
    short of reality — the failure that makes a live desync undetectable until reconciliation."""
    oms.register(_new("c1", Side.BUY, 100), strategy="mm")
    with pytest.raises(IllegalTransition, match="exceeds"):
        oms.on_fill(_fill("c1", Side.BUY, 101))
    assert oms.position("TEST") == 0


def test_filling_a_terminal_order_raises(oms):
    oms.register(_new("c1", Side.BUY, 100), strategy="mm")
    oms.on_cancelled("c1")
    with pytest.raises(IllegalTransition):
        oms.on_fill(_fill("c1", Side.BUY, 100))


def test_reusing_a_client_id_while_live_is_refused(oms):
    """An ambiguous cancel in a simulator is a silently wrong fill sequence, not an error."""
    oms.register(_new("c1"), strategy="mm")
    with pytest.raises(ValueError, match="already live"):
        oms.register(_new("c1"), strategy="mm")


def test_a_client_id_may_be_reused_once_the_previous_order_is_terminal(oms):
    """CONTROL for the duplicate test: the constraint is on LIVE orders, not on ids forever.
    A strategy that recycles ids after completion must keep working."""
    oms.register(_new("c1"), strategy="mm")
    oms.on_cancelled("c1")
    oms.register(_new("c1"), strategy="mm")
    assert oms.get("c1").state is OrderState.PENDING_NEW


def test_an_unknown_client_id_raises_on_every_callback(oms):
    for call in (
        lambda: oms.on_acknowledged("nope"),
        lambda: oms.on_cancelled("nope"),
        lambda: oms.on_rejected("nope", reason="x"),
        lambda: oms.on_fill(_fill("nope", Side.BUY, 1)),
    ):
        with pytest.raises(KeyError):
            call()


def test_the_transition_table_forbids_resurrection():
    assert legal_transition(OrderState.PENDING_NEW, OrderState.WORKING)
    assert legal_transition(OrderState.WORKING, OrderState.PARTIALLY_FILLED)
    assert legal_transition(OrderState.PARTIALLY_FILLED, OrderState.CANCELLED)
    assert not legal_transition(OrderState.FILLED, OrderState.WORKING)
    assert not legal_transition(OrderState.CANCELLED, OrderState.PARTIALLY_FILLED)
    assert not legal_transition(OrderState.REJECTED, OrderState.WORKING)


def test_rejection_records_the_reason(oms):
    oms.register(_new("c1"), strategy="mm")
    order = oms.on_rejected("c1", reason="over inventory limit")
    assert order.state is OrderState.REJECTED
    assert order.reason == "over inventory limit"


# ---- determinism ----------------------------------------------------------------------------


def test_client_ids_are_deterministic_so_two_runs_of_one_replay_are_diffable():
    """UUIDs would make every run produce different ids, and diffing two runs is the main
    way a change's effect is actually measured."""
    sequence = OrderIdSequence(prefix="mm")
    assert [sequence.next() for _ in range(3)] == ["mm-1", "mm-2", "mm-3"]

    # Two independently-constructed sequences must produce the same stream — that is what
    # makes run N and run N+1 of the same replay comparable.
    first = OrderIdSequence(prefix="mm")
    second = OrderIdSequence(prefix="mm")
    assert [first.next() for _ in range(3)] == [second.next() for _ in range(3)]


# ---- a replace must reach the OMS, not just the book ---------------------------------------


def test_a_replace_that_grows_the_order_does_not_blow_up_the_next_fill(oms):
    """THE OMS HAD NO on_replaced AT ALL. Both Runner._replace and AutoLoop._route called
    ``book.replace`` and left ManagedOrder.qty / .price_ticks at their original values, so the
    OMS and the book disagreed about the order from that moment on.

    The visible failure is a fill larger than the OMS believes is outstanding, which raises
    IllegalTransition and kills the run. The quieter one is worse: exposure_ticks() is
    computed from the stale price, so every limit derived from it — including the gross
    exposure guard on a subsequent replace — is measured against a number that no longer
    describes any order that exists.
    """
    oms.register(_new(qty=100, price=50), strategy="s", ts=0)
    oms.on_acknowledged("c1", ts=0)

    oms.on_replaced("c1", new_qty=5_000, new_price_ticks=60, ts=1)

    order = oms.get("c1")
    assert order.qty == 5_000 and order.price_ticks == 60
    assert oms.exposure_ticks() == 5_000 * 60

    # The fill that used to raise: 3,000 against an OMS that still believed qty=100.
    oms.on_fill(_fill("c1", Side.BUY, 3_000, price=60))
    assert oms.get("c1").filled_qty == 3_000
    assert oms.position("TEST") == 3_000


def test_an_omitted_field_in_a_replace_keeps_its_current_value(oms):
    """A pure reprice leaves new_qty None, and a pure resize leaves new_price_ticks None.
    Treating either as zero would silently retire the order's size or its price."""
    oms.register(_new(qty=100, price=50), strategy="s", ts=0)
    oms.on_acknowledged("c1", ts=0)

    oms.on_replaced("c1", new_price_ticks=55, ts=1)
    assert oms.get("c1").qty == 100 and oms.get("c1").price_ticks == 55

    oms.on_replaced("c1", new_qty=250, ts=2)
    assert oms.get("c1").qty == 250 and oms.get("c1").price_ticks == 55


def test_replacing_a_terminal_order_raises_rather_than_resurrecting_it(oms):
    """A replace arriving after a cancel must not quietly bring the order back."""
    oms.register(_new(qty=100, price=50), strategy="s", ts=0)
    oms.on_acknowledged("c1", ts=0)
    oms.on_cancelled("c1", ts=1)
    with pytest.raises(IllegalTransition):
        oms.on_replaced("c1", new_qty=200, ts=2)


def test_a_replace_below_the_filled_quantity_is_refused(oms):
    """Shrinking an order below what already traded would make remaining_qty negative and
    corrupt every exposure number derived from it."""
    oms.register(_new(qty=100, price=50), strategy="s", ts=0)
    oms.on_acknowledged("c1", ts=0)
    oms.on_fill(_fill("c1", Side.BUY, 60, price=50))
    with pytest.raises(IllegalTransition):
        oms.on_replaced("c1", new_qty=10, ts=2)


def test_a_fill_on_a_terminal_order_leaves_no_trace_of_itself(oms):
    """CHECK BEFORE MUTATE. ``on_fill`` incremented filled_qty, fill_notional_ticks and the
    POSITION, and only then called ``_transition``, which raises for a terminal order. The
    caller sees an exception and reasonably assumes nothing happened — but the position had
    already moved, so the OMS's inventory now reflects a fill it simultaneously refuses to
    acknowledge, and every downstream number is silently wrong.

    ``test_overfilling_an_order_raises_rather_than_clamping`` covers the sibling path, which
    was already correct because its guard sits above the mutations. This is the one that was
    not, and it is reachable whenever a strategy's on_fill cancels an order that has further
    fills later in the same batch.
    """
    oms.register(_new(qty=100), strategy="s", ts=0)
    oms.on_acknowledged("c1", ts=0)
    oms.on_cancelled("c1", ts=1)

    before_position = oms.position("TEST")
    before_filled = oms.get("c1").filled_qty
    before_notional = oms.get("c1").fill_notional_ticks

    with pytest.raises(IllegalTransition):
        oms.on_fill(_fill("c1", Side.BUY, 100))

    assert oms.position("TEST") == before_position, "the refused fill moved the position"
    assert oms.get("c1").filled_qty == before_filled
    assert oms.get("c1").fill_notional_ticks == before_notional
    assert oms.live_orders() == []


def test_live_orders_match_a_full_scan_including_a_reused_id(oms):
    """The index replaces the scan. A reused client id keeps the slot it had the
    first time it was registered, which is where ``_orders`` still yields it."""
    oms.register(_new("a", Side.BUY, 10), strategy="mm")
    oms.register(NewOrder(client_id="b", symbol="BBB", side=Side.SELL, qty=10, price_ticks=20), strategy="mom")
    oms.on_cancelled("a")
    oms.register(_new("a", Side.BUY, 4, price=7), strategy="mm")
    oms.on_acknowledged("a")
    oms.on_fill(_fill("a", Side.BUY, 1, price=7))
    scanned = [
        order.client_id for order in oms._orders.values() if order.is_live
    ]
    assert [order.client_id for order in oms.live_orders()] == scanned == ["a", "b"]
    assert [order.client_id for order in oms.live_orders(strategy="mm")] == ["a"]
    assert oms.live_orders()[0].state is OrderState.PARTIALLY_FILLED
    oms.on_rejected("b", reason="no")
    assert [order.client_id for order in oms.live_orders()] == ["a"]


def test_an_orders_overlay_is_what_exposure_reads(oms):
    """AutoLoop's guard view copies ``_orders`` and writes the worst pending
    replace onto that copy. Exposure has to see those terms, and the original
    OMS must keep the acknowledged ones."""
    oms.register(_new("a", qty=5, price=10), strategy="mm")
    view = copy(oms)
    view._orders = dict(oms._orders)
    view._orders["a"] = replace(oms.get("a"), qty=10)
    assert view.exposure_ticks() == 100
    assert view.open_side_qty("TEST", Side.BUY) == 10
    assert oms.exposure_ticks() == 50
