"""QT-02 — order-flow-imbalance momentum gates.

The properties pinned:

  * NO SIGNAL UNTIL WARM, and "no opinion" is distinguishable from "a confident opinion of
    neutral". A strategy that cannot tell those apart trades its own initialisation transient.
  * THE SIGN FOLLOWS THE AGGRESSOR. Buy aggression means a positive score means a buy.
    Asserted in both directions.
  * ENTRY AND EXIT THRESHOLDS ARE ASYMMETRIC. A single threshold makes the strategy flip in
    and out around the boundary, paying the spread on every oscillation — a cost that dwarfs
    the signal and is invisible without a real fee model.
  * IT NEVER STACKS ORDERS. One in flight at a time; a second entry on an unchanged signal
    turns one intended position into several.
  * IT SENDS MARKETABLE LIMITS, NOT MARKET ORDERS. A market order in a book with one share
    at the touch is how a backtest discovers slippage it never modelled.
"""
from __future__ import annotations

from quant.events import Side, Trade
from quant.oms import NewOrder
from quant.strategies.momentum import Momentum, MomentumParams

from .conftest import context, snapshot


def _mom(**kwargs) -> Momentum:
    return Momentum(name="mom", symbols=("TEST",), params=MomentumParams(**kwargs))


def _print(strategy: Momentum, aggressor: Side, qty: int = 100, n: int = 1) -> None:
    for i in range(n):
        strategy.on_trade(
            Trade(symbol="TEST", seq=i, exchange_ts=i, recv_ts=i, ingest_ts=i,
                  price_ticks=10_001, qty=qty, aggressor=aggressor),
            context(snapshot()),
        )


# ---- the signal ---------------------------------------------------------------------------


def test_no_signal_before_the_warmup_count_is_reached():
    mom = _mom(min_warm_prints=20)
    _print(mom, Side.BUY, n=19)
    assert mom.signal(context(snapshot())) is None
    assert mom.on_book_update(context(snapshot())) == []


def test_a_signal_appears_once_warm():
    """CONTROL for the warmup test."""
    mom = _mom(min_warm_prints=20)
    _print(mom, Side.BUY, n=20)
    assert mom.signal(context(snapshot())) is not None


def test_buy_aggression_produces_a_positive_score_and_sell_aggression_a_negative_one():
    buyers, sellers = _mom(min_warm_prints=5), _mom(min_warm_prints=5)
    _print(buyers, Side.BUY, n=10)
    _print(sellers, Side.SELL, n=10)
    assert buyers.signal(context(snapshot())) > 0
    assert sellers.signal(context(snapshot())) < 0


def test_a_heavy_bid_adds_to_the_score_through_the_microprice_term():
    """The second input. Resting imbalance and traded flow are combined rather than
    filtered, because requiring both to agree produces a signal that is right and late."""
    mom = _mom(min_warm_prints=5)
    _print(mom, Side.BUY, n=5)
    balanced = mom.signal(context(snapshot(bid_qty=500, ask_qty=500)))
    bid_heavy = mom.signal(context(snapshot(bid_qty=900, ask_qty=100)))
    assert bid_heavy > balanced


# ---- entry and exit -----------------------------------------------------------------------


def test_a_strong_buy_signal_crosses_the_spread_at_the_far_touch():
    mom = _mom(min_warm_prints=5, entry_threshold=0.3, trade_size=100)
    _print(mom, Side.BUY, n=10)
    intents = mom.on_book_update(context(snapshot(bid=10_000, ask=10_002)))
    assert len(intents) == 1
    order = intents[0]
    assert isinstance(order, NewOrder)
    assert order.side is Side.BUY
    assert order.price_ticks == 10_002        # capped at the ask, not sent as a market order
    assert order.tif == "ioc"


def test_a_strong_sell_signal_crosses_at_the_bid():
    mom = _mom(min_warm_prints=5, entry_threshold=0.3)
    _print(mom, Side.SELL, n=10)
    order = mom.on_book_update(context(snapshot(bid=10_000, ask=10_002)))[0]
    assert order.side is Side.SELL and order.price_ticks == 10_000


def test_a_weak_signal_does_not_trade():
    mom = _mom(min_warm_prints=4, entry_threshold=0.9, microprice_weight=0.0)
    _print(mom, Side.BUY, n=3)
    _print(mom, Side.SELL, n=3)
    assert mom.on_book_update(context(snapshot())) == []


def test_a_decayed_signal_flattens_an_open_position():
    mom = _mom(min_warm_prints=4, entry_threshold=0.9, exit_threshold=0.5,
               microprice_weight=0.0)
    _print(mom, Side.BUY, n=3)
    _print(mom, Side.SELL, n=3)          # net flow ~0, below the exit threshold
    intents = mom.on_book_update(context(snapshot(), positions={"TEST": 300}))
    assert len(intents) == 1
    assert intents[0].side is Side.SELL and intents[0].qty == 300


def test_a_signal_between_the_exit_and_entry_thresholds_holds_the_position():
    """The asymmetric band, stated directly. This is the region where a single-threshold
    strategy would churn, paying the spread on every oscillation."""
    mom = _mom(min_warm_prints=4, entry_threshold=0.8, exit_threshold=0.2,
               microprice_weight=0.0)
    _print(mom, Side.BUY, n=6)
    _print(mom, Side.SELL, n=2)          # OFI = 0.5: inside the band
    assert 0.2 < abs(mom.signal(context(snapshot()))) < 0.8
    assert mom.on_book_update(context(snapshot(), positions={"TEST": 300})) == []


# ---- position and order discipline -----------------------------------------------------------


def test_it_does_not_stack_a_second_order_while_one_is_in_flight():
    from quant.oms import OMS

    mom = _mom(min_warm_prints=5, entry_threshold=0.3)
    _print(mom, Side.BUY, n=10)
    first = mom.on_book_update(context(snapshot()))
    oms = OMS()
    oms.register(first[0], strategy="mom")
    assert mom.on_book_update(context(snapshot(), live_orders=tuple(oms.live_orders()))) == []


def test_it_will_not_exceed_the_maximum_position():
    mom = _mom(min_warm_prints=5, entry_threshold=0.3, trade_size=100, max_position=250)
    _print(mom, Side.BUY, n=10)
    intents = mom.on_book_update(context(snapshot(), positions={"TEST": 200}))
    assert intents[0].qty == 50       # only the headroom


def test_it_sends_nothing_at_the_position_limit():
    mom = _mom(min_warm_prints=5, entry_threshold=0.3, max_position=250)
    _print(mom, Side.BUY, n=10)
    assert mom.on_book_update(context(snapshot(), positions={"TEST": 250})) == []


def test_a_one_sided_book_produces_no_order_because_there_is_no_far_touch():
    mom = _mom(min_warm_prints=5, entry_threshold=0.3)
    _print(mom, Side.BUY, n=10)
    assert mom.on_book_update(context(snapshot(ask=None))) == []


def test_describe_reports_warmth_so_a_quiet_strategy_is_distinguishable_from_a_cold_one():
    mom = _mom(min_warm_prints=5)
    assert mom.describe()["state"]["warm"] is False
    _print(mom, Side.BUY, n=5)
    described = mom.describe()
    assert described["state"]["warm"] is True
    assert described["state"]["prints_seen"] == 5
    assert described["strategy"] == "momentum"
