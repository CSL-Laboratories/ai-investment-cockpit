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

import random

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


# ---- QT-11: the live-order index -------------------------------------------------------------


def test_live_orders_no_longer_scans_every_order_ever_created():
    """The optimization's premise. ``live_orders`` used to filter all orders including
    terminal ones, so cost grew with the run — 12.8M is_live checks for a 20k-event
    simulation, ~85% of total runtime."""
    oms = OMS()
    for i in range(50):
        intent = NewOrder(client_id=f"c{i}", symbol="S", side=Side.BUY, qty=100, price_ticks=100)
        oms.register(intent, strategy="s")
        oms.on_cancelled(f"c{i}", reason="done")

    live = NewOrder(client_id="live", symbol="S", side=Side.BUY, qty=100, price_ticks=100)
    oms.register(live, strategy="s")

    assert [o.client_id for o in oms.live_orders()] == ["live"]
    assert len(oms._live) == 1, "50 terminal orders must not remain in the index"


def test_the_live_index_survives_a_randomized_transition_sweep():
    """DIFFERENTIAL. The index is a cache of a derivable fact, so it is checked against the
    full scan it replaced, over a randomized lifecycle. Drift would mean a strategy silently
    stops seeing one of its own working orders — which presents as the strategy quoting a
    duplicate, not as a bookkeeping error."""
    for seed in range(6):
        rng = random.Random(seed)
        oms = OMS()
        for i in range(120):
            cid = f"c{i}"
            oms.register(
                NewOrder(client_id=cid, symbol=rng.choice(("A", "B")), side=Side.BUY,
                         qty=100, price_ticks=100),
                strategy=rng.choice(("one", "two")),
            )
            roll = rng.random()
            if roll < 0.25:
                oms.on_acknowledged(cid)
            elif roll < 0.45:
                oms.on_cancelled(cid, reason="r")
            elif roll < 0.60:
                oms.on_rejected(cid, reason="r")
            elif roll < 0.75:
                oms.on_acknowledged(cid)
                oms.on_fill(Fill(symbol=oms.get(cid).symbol, seq=i, exchange_ts=i, recv_ts=i,
                                 ingest_ts=i, order_id=cid, side=Side.BUY, price_ticks=100,
                                 qty=100, liquidity=Liquidity.MAKER))
            oms.check_integrity()

        scanned = sorted(o.client_id for o in oms._orders.values() if o.is_live)
        assert sorted(o.client_id for o in oms.live_orders()) == scanned


def test_live_order_iteration_is_deterministic_not_set_ordered():
    """A ``set[str]`` iterates in an order that depends on string hash randomization, so it
    differs BETWEEN PROCESSES unless PYTHONHASHSEED is pinned. Strategies read this list, so
    a set would make 'same seed, same output' true only within one process — the hardest
    kind of reproducibility failure to notice."""
    def build() -> list[str]:
        oms = OMS()
        for i in range(30):
            oms.register(NewOrder(client_id=f"order-{i}", symbol="S", side=Side.BUY,
                                  qty=100, price_ticks=100), strategy="s")
        return [o.client_id for o in oms.live_orders()]

    assert build() == build()
    assert build() == [f"order-{i}" for i in range(30)], "registration order must be preserved"


def test_filtering_by_strategy_is_also_deterministic_and_correct():
    oms = OMS()
    for i in range(20):
        oms.register(NewOrder(client_id=f"c{i}", symbol="S", side=Side.BUY, qty=100,
                              price_ticks=100),
                     strategy="one" if i % 2 == 0 else "two")
    ones = [o.client_id for o in oms.live_orders(strategy="one")]
    assert ones == [f"c{i}" for i in range(0, 20, 2)]
    assert oms.live_orders(strategy="nobody") == []


def test_check_integrity_actually_detects_drift():
    """CONTROL. A consistency check that cannot fail proves nothing about the cache."""
    oms = OMS()
    oms.register(NewOrder(client_id="c1", symbol="S", side=Side.BUY, qty=100, price_ticks=100),
                 strategy="s")
    oms.check_integrity()
    oms._live.pop("c1")                      # simulate a transition path that forgot the index
    with pytest.raises(AssertionError, match="drifted"):
        oms.check_integrity()
