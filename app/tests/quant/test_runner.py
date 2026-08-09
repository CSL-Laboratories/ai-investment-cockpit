"""QT-03 — the event loop, including the one result that justifies the whole engine.

The properties pinned:

  * REPORTED AGGRESSION IS REPLAYED THROUGH THE REAL MATCHER, so our resting orders fill if
    and only if they were genuinely ahead in the queue. No probability draw exists anywhere
    in this package to reach for instead.
  * A VENUE ADD THAT CROSSES TRADES, it does not rest. Otherwise the book stays permanently
    crossed and our quotes never receive maker fills.
  * A DESYNC IS COUNTED, NOT SWALLOWED AND NOT FATAL. A replay that aborts on the first
    dropped message tells you nothing; one that silently continues tells you something false.
  * AN IOC REMAINDER CLOSES THE OMS ORDER. Left live forever, every strategy that checks its
    own open orders goes permanently silent — a deadlock that looks like a flat signal.

And the headline result, in ``test_requoting_on_every_move_destroys_queue_priority``: turning
the requote threshold off sends ~90x the order traffic and wins FEWER maker fills. That is
the queue-position effect, and an exp(-k*delta) fill model cannot express it at all.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from quant.book.lob import LimitOrderBook
from quant.events import BookAction, BookUpdate, Liquidity, PriceScale, Side, Trade
from quant.feed import validate_ordering
from quant.feed.synthetic import SyntheticFeed
from quant.oms import CancelOrder, NewOrder
from quant.runner import VENUE_PREFIX, Runner, apply_market_event
from quant.strategies.base import Strategy
from quant.strategies.market_maker import MarketMaker, MarketMakerParams

PENNY = PriceScale(tick_size=Decimal("0.01"))


def _add(seq: int, side: Side, price: int, qty: int, ref: int, ts: int = 1_000) -> BookUpdate:
    return BookUpdate(symbol="S", seq=seq, exchange_ts=ts, recv_ts=ts + 10, ingest_ts=ts + 20,
                      action=BookAction.ADD, side=side, price_ticks=price, qty=qty, order_ref=ref)


def _cancel(seq: int, side: Side, price: int, qty: int, ref: int, ts: int = 2_000) -> BookUpdate:
    return BookUpdate(symbol="S", seq=seq, exchange_ts=ts, recv_ts=ts + 10, ingest_ts=ts + 20,
                      action=BookAction.CANCEL, side=side, price_ticks=price, qty=qty, order_ref=ref)


def _trade(seq: int, aggressor: Side, price: int, qty: int, ts: int = 3_000) -> Trade:
    return Trade(symbol="S", seq=seq, exchange_ts=ts, recv_ts=ts + 10, ingest_ts=ts + 20,
                 price_ticks=price, qty=qty, aggressor=aggressor)


class Scripted(Strategy):
    """Emits a fixed list of intents on the first book update, then nothing."""

    def __init__(self, name: str, intents: list, symbols=("S",)) -> None:
        self.intents = intents
        self.fired = False
        self.fills = []
        Strategy.__init__(self, name=name, symbols=symbols)

    def on_book_update(self, ctx):
        if self.fired:
            return []
        self.fired = True
        return list(self.intents)

    def on_fill(self, fill, ctx):
        self.fills.append(fill)
        return []

    def describe(self):
        return {"strategy": "scripted", "name": self.name}


# ---- applying market events -------------------------------------------------------------------


def test_a_venue_add_rests_when_it_does_not_cross():
    book = LimitOrderBook("S")
    apply_market_event(book, _add(1, Side.BUY, 100, 500, ref=7))
    assert book.best_bid() == 100
    assert f"{VENUE_PREFIX}7" in book


def test_a_venue_add_that_crosses_TRADES_rather_than_resting():
    """Otherwise the book stays crossed forever and every mid computed from it is nonsense."""
    book = LimitOrderBook("S")
    apply_market_event(book, _add(1, Side.SELL, 100, 300, ref=1))
    apply_market_event(book, _add(2, Side.BUY, 101, 300, ref=2))
    assert not book.is_crossed
    assert len(book) == 0


def test_a_crossing_venue_add_fills_our_resting_quote_as_MAKER():
    """The mechanism by which a passive strategy earns anything. If a crossing add were
    parked beside our quote instead of trading against it, a market maker would only ever
    fill by crossing — inverting the strategy it models."""
    book = LimitOrderBook("S")
    book.add(order_id="ours", side=Side.SELL, price_ticks=100, qty=200, owner="mm")
    fills = apply_market_event(book, _add(1, Side.BUY, 101, 200, ref=5))
    assert len(fills) == 1
    assert fills[0].order_id == "ours"
    assert fills[0].liquidity is Liquidity.MAKER
    assert fills[0].price_ticks == 100        # our resting price, not the aggressor's 101


def test_a_reported_trade_fills_our_order_only_if_it_was_ahead_in_the_queue():
    """THE queue-position rule, stated directly. 400 shares rest ahead of ours; a 300-share
    print cannot reach us, a 500-share print can."""
    book = LimitOrderBook("S")
    book.add(order_id="ahead", side=Side.SELL, price_ticks=100, qty=400)
    book.add(order_id="ours", side=Side.SELL, price_ticks=100, qty=200, owner="mm")

    assert apply_market_event(book, _trade(1, Side.BUY, 100, 300)) == []
    fills = apply_market_event(book, _trade(2, Side.BUY, 100, 300))
    assert [f.order_id for f in fills] == ["ours"]
    assert fills[0].qty == 200


def test_a_reported_trade_never_rests_a_remainder():
    """A trade is a completed event. Leaving a remainder would inject liquidity the market
    never had, and that phantom depth would then fill our orders."""
    book = LimitOrderBook("S")
    book.add(order_id="v", side=Side.SELL, price_ticks=100, qty=100)
    apply_market_event(book, _trade(1, Side.BUY, 100, 900))
    assert book.best_bid() is None
    assert len(book) == 0


def test_a_venue_cancel_for_an_unknown_order_raises_out_of_apply():
    book = LimitOrderBook("S")
    with pytest.raises(KeyError):
        apply_market_event(book, _cancel(1, Side.BUY, 100, 100, ref=999))


def test_venue_ids_are_namespaced_so_they_cannot_collide_with_our_client_ids():
    """A collision would silently merge someone else's queue position with ours."""
    book = LimitOrderBook("S")
    book.add(order_id="7", side=Side.BUY, price_ticks=100, qty=100, owner="mm")
    apply_market_event(book, _add(1, Side.BUY, 100, 500, ref=7))
    assert len(book) == 2
    assert book.get("7").is_ours
    assert not book.get(f"{VENUE_PREFIX}7").is_ours


# ---- the loop ------------------------------------------------------------------------------------


def test_a_desync_is_counted_rather_than_being_fatal_or_silent():
    strategy = Scripted("s", [])
    runner = Runner(feed=[_add(1, Side.BUY, 100, 100, ref=1),
                          _cancel(2, Side.BUY, 100, 100, ref=999)],
                    strategies=[strategy], scale=PENNY)
    result = runner.run()
    assert result.desync_cancels == 1
    assert result.events == 2


def test_a_resting_intent_reaches_the_book_and_is_tracked_by_the_oms():
    strategy = Scripted("s", [NewOrder(client_id="c1", symbol="S", side=Side.BUY,
                                       qty=100, price_ticks=99)])
    runner = Runner(feed=[_add(1, Side.SELL, 101, 500, ref=1)], strategies=[strategy], scale=PENNY)
    runner.run()
    assert "c1" in runner.books["S"]
    assert runner.oms.get("c1").is_live


def test_an_ioc_remainder_closes_the_oms_order_instead_of_hanging_forever():
    """Left live, every strategy that checks its own open orders goes permanently silent —
    a deadlock indistinguishable from a strategy with no signal."""
    strategy = Scripted("s", [NewOrder(client_id="c1", symbol="S", side=Side.BUY,
                                       qty=100, price_ticks=99, tif="ioc")])
    runner = Runner(feed=[_add(1, Side.SELL, 101, 500, ref=1)], strategies=[strategy], scale=PENNY)
    runner.run()
    assert not runner.oms.get("c1").is_live
    assert "c1" not in runner.books["S"]


def test_a_crossing_intent_fills_immediately_as_taker_and_moves_position():
    strategy = Scripted("s", [NewOrder(client_id="c1", symbol="S", side=Side.BUY,
                                       qty=200, price_ticks=101)])
    runner = Runner(feed=[_add(1, Side.SELL, 101, 500, ref=1)], strategies=[strategy], scale=PENNY)
    result = runner.run()
    assert len(result.fills) == 1
    assert result.fills[0].liquidity is Liquidity.TAKER
    assert runner.oms.position("S") == 200
    assert strategy.fills == result.fills          # the strategy was told about its own fill


def test_a_cancel_intent_removes_the_order_from_both_book_and_oms():
    place = Scripted("s", [NewOrder(client_id="c1", symbol="S", side=Side.BUY,
                                    qty=100, price_ticks=99)])
    runner = Runner(feed=[_add(1, Side.SELL, 101, 500, ref=1)], strategies=[place], scale=PENNY)
    runner.run()
    runner._cancel(CancelOrder(client_id="c1"))
    assert "c1" not in runner.books["S"]
    assert not runner.oms.get("c1").is_live


def test_a_strategy_only_sees_symbols_it_named():
    listener = Scripted("s", [], symbols=("OTHER",))
    runner = Runner(feed=[_add(1, Side.BUY, 100, 100, ref=1)], strategies=[listener], scale=PENNY)
    runner.run()
    assert not listener.fired


def test_a_strategy_naming_no_symbols_sees_everything():
    """CONTROL for the filter — a runner that dispatched to nobody would pass the test above."""
    listener = Scripted("s", [], symbols=())
    runner = Runner(feed=[_add(1, Side.BUY, 100, 100, ref=1)], strategies=[listener], scale=PENNY)
    runner.run()
    assert listener.fired


def test_guards_veto_an_intent_BEFORE_it_reaches_the_book():
    """Pre-trade means pre-trade. A limit evaluated after the order is live is a report."""

    class Verdict:
        allowed = False
        reason = "test_block"

    class Blocker:
        def check(self, intent, **kwargs):
            return Verdict()

    strategy = Scripted("s", [NewOrder(client_id="c1", symbol="S", side=Side.BUY,
                                       qty=100, price_ticks=99)])
    runner = Runner(feed=[_add(1, Side.SELL, 101, 500, ref=1)], strategies=[strategy],
                    scale=PENNY, guards=Blocker())
    result = runner.run()
    assert result.intents_blocked == 1
    assert result.blocked_reasons == {"test_block": 1}
    assert "c1" not in runner.books["S"]
    assert runner.oms.get("c1") is None


# ---- the headline result --------------------------------------------------------------------------


def _run_maker(threshold: int, *, events: int = 4_000, seed: int = 3) -> tuple[Counter, int]:
    feed = validate_ordering(iter(SyntheticFeed(symbols=("S",), n_events=events, seed=seed)))
    mm = MarketMaker(name="mm", symbols=("S",),
                     params=MarketMakerParams(quote_size=100, max_inventory=1_000,
                                              requote_threshold_ticks=threshold))
    runner = Runner(feed=feed, strategies=[mm], scale=PENNY, timer_interval_ns=5_000_000)
    result = runner.run()
    runner.books["S"].check_integrity()
    return Counter(f.liquidity.value for f in result.fills), result.intents_submitted


def test_requoting_on_every_move_destroys_queue_priority():
    """THE result the whole engine exists to produce.

    Turning the requote threshold off makes the maker chase the target on every tick. Against
    a real FIFO book each reprice is a cancel-and-new that forfeits queue position, so the
    strategy sends vastly more orders and wins FEWER passive fills — it is always at the
    back of a queue it keeps rejoining.

    Under an exp(-k*delta) fill model this comparison is not merely different, it is
    INEXPRESSIBLE: fill probability there depends only on distance from the mid, so
    repricing is free and a zero threshold strictly dominates. Every toy market-making
    backtest that concluded 'requote aggressively' concluded it from that assumption.
    """
    patient, patient_orders = _run_maker(threshold=2)
    frantic, frantic_orders = _run_maker(threshold=0)

    assert frantic_orders > patient_orders * 10, (
        f"a zero threshold should flood the venue: {frantic_orders} vs {patient_orders}"
    )
    assert patient["maker"] > frantic["maker"], (
        f"patient quoting should win MORE passive fills despite sending far fewer orders: "
        f"patient={patient['maker']} frantic={frantic['maker']}"
    )


def test_the_maker_fills_predominantly_as_maker_against_a_realistic_book():
    """A sanity floor on the whole pipeline. If a market maker's fills were mostly TAKER,
    something upstream — a crossed book, a mis-signed quote — has inverted the strategy."""
    liquidity, _ = _run_maker(threshold=2)
    assert liquidity["maker"] > liquidity["taker"]
    assert liquidity["maker"] > 20


def test_an_end_to_end_synthetic_run_leaves_a_consistent_book_and_BOUNDED_divergence():
    """The counterfactual, measured rather than assumed away.

    With no strategy attached the feed replays with ZERO desyncs — that is pinned separately
    in ``tests/quant/feed``. Once our maker participates, a small residue appears and it is
    not a bug: our quotes consume liquidity that still exists in the generator's own book, so
    a later cancel names an order we already traded away. That is precisely the "our presence
    would have changed the day" problem, and it is unavoidable in any replay.

    What matters is that it stays SMALL — the divergence is bounded and reported, not
    unbounded and hidden. A run where this climbed into the percent range would mean our book
    and the venue's had meaningfully parted company and its results should not be trusted.
    """
    feed = validate_ordering(iter(SyntheticFeed(symbols=("S",), n_events=5_000, seed=17)))
    mm = MarketMaker(name="mm", symbols=("S",), params=MarketMakerParams(quote_size=100))
    runner = Runner(feed=feed, strategies=[mm], scale=PENNY, timer_interval_ns=2_000_000)
    result = runner.run()
    runner.books["S"].check_integrity()
    assert result.events == 5_000
    assert result.desync_cancels < result.events * 0.01
    assert not runner.books["S"].is_crossed
