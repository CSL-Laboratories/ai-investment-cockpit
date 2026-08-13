"""Regressions for the 2026-08-09 sweep's quant-sim fixes.

BUG-057/074/075/076/077/078/079/089/090/091 — the simulator is the M3/M4 evidence base;
each of these silently flattered or corrupted what it measures.
"""
from __future__ import annotations

import pytest

from quant.book.lob import LimitOrderBook
from decimal import Decimal

from quant.events import Fill, Liquidity, PriceScale, Side, Trade
from quant.execution.sim_venue import LatencyModel, SimVenue
from quant.feed import FeedOrderingError, validate_ordering
from quant.feed.synthetic import SyntheticFeed
from quant.guards import GuardLimits, Guards
from quant.oms import OMS, NewOrder
from quant.strategies.base import StrategyContext
from quant.strategies.market_maker import MarketMaker, MarketMakerParams

PENNY = PriceScale(tick_size=Decimal("0.01"))


def _stamps(seq=1, ts=1_000_000):
    return {"symbol": "S", "seq": seq, "exchange_ts": ts, "recv_ts": ts + 1,
            "ingest_ts": ts + 2}


# ---- BUG-089 / BUG-074: the pre-trade gate's missing bounds ----------------------------
def test_zero_and_negative_qty_are_blocked_at_the_gate():
    guards = Guards()
    for qty in (0, -5):
        v = guards.check(NewOrder(client_id="c", symbol="S", side=Side.BUY,
                                  qty=qty, price_ticks=100))
        assert not v.allowed and "must be positive" in v.reason


def test_market_orders_are_refused_for_exposure_accounting():
    """BUG-074: price_ticks=None contributed exactly 0 to the gross-exposure sum — the cap
    measured nothing for the market-order path. Fail closed beats a bypassable cap."""
    guards = Guards()
    v = guards.check(NewOrder(client_id="c", symbol="S", side=Side.BUY,
                              qty=100, price_ticks=None))
    assert not v.allowed and "exposure accounting" in v.reason


# ---- BUG-091: max_position must project the worst case incl. in-flight -----------------
def test_in_flight_same_side_orders_cannot_stack_past_max_position():
    guards = Guards(limits=GuardLimits(max_position=300, max_orders_per_window=10_000))
    oms = OMS()
    first = NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=200, price_ticks=100)
    assert guards.check(first, oms=oms).allowed
    oms.register(first, strategy="t")
    oms.on_acknowledged("a")
    second = NewOrder(client_id="b", symbol="S", side=Side.BUY, qty=200, price_ticks=100)
    v = guards.check(second, oms=oms)
    assert not v.allowed and "in-flight" in v.reason, (
        "two 200-share buys against a 300 cap each passed individually pre-fix")


# ---- BUG-075: replace must not double-count this order's own fills ---------------------
def _fill(order_id, qty, price=100, seq=2):
    return Fill(order_id=order_id, side=Side.BUY, price_ticks=price, qty=qty,
                liquidity=Liquidity.MAKER, **_stamps(seq=seq))


def test_replace_projection_counts_filled_shares_once():
    from quant.oms import ReplaceOrder

    guards = Guards(limits=GuardLimits(max_position=300, max_orders_per_window=10_000))
    oms = OMS()
    order = NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=200, price_ticks=100)
    oms.register(order, strategy="t")
    oms.on_acknowledged("a")
    oms.on_fill(_fill("a", 50))                      # position +50, remaining 150

    # Resize to a NEW TOTAL of 300: worst case = 50 held + 250 remaining = 300 == cap.
    # Pre-fix the projection was 50 + 300 = 350 and this legitimate resize was refused.
    v = guards.check(ReplaceOrder(client_id="a", new_qty=300), oms=oms)
    assert v.allowed, v.reason

    # and one share above the cap is still refused
    v2 = guards.check(ReplaceOrder(client_id="a", new_qty=301), oms=oms)
    assert not v2.allowed


# ---- BUG-057: the maker keeps tracking a partially-filled quote ------------------------
def _ctx(live_orders=(), ts=1_000):
    return StrategyContext(ts=ts, books={}, positions={}, live_orders=tuple(live_orders),
                           scale=PENNY, primary_symbol="S")


def test_partial_fill_keeps_the_quote_tracked():
    mm = MarketMaker(name="mm", params=MarketMakerParams())
    mm._set_side(True, "bid-1", 100)
    oms = OMS()
    order = NewOrder(client_id="bid-1", symbol="S", side=Side.BUY, qty=100, price_ticks=100)
    oms.register(order, strategy="mm")
    oms.on_acknowledged("bid-1")
    oms.on_fill(_fill("bid-1", 40))                  # partial: 60 still resting

    mm.on_fill(_fill("bid-1", 40, seq=3), _ctx(live_orders=oms.live_orders()))
    assert mm._bid_id == "bid-1", "a partial fill must not orphan the resting remainder"

    oms.on_fill(_fill("bid-1", 60, seq=4))           # now exhausted
    mm.on_fill(_fill("bid-1", 60, seq=5), _ctx(live_orders=oms.live_orders()))
    assert mm._bid_id is None, "an exhausting fill retires the quote"


# ---- BUG-076: no phantom prints ---------------------------------------------------------
class _ForcedRng:
    """Deterministic stand-in: always a BUY aggressor of 100 shares."""

    def random(self):
        return 0.0

    def randrange(self, *_a, **_k):
        return 100


def test_zero_fill_aggressor_prints_nothing():
    feed = SyntheticFeed()
    book = LimitOrderBook("S")
    book.add(order_id="r1", side=Side.BUY, price_ticks=99, qty=500, ts=1, seq=1)  # bids only
    s = {"book": book, "fair": 100.0, "next_ref": 1, "seq": 1}
    trade = feed._trade(_ForcedRng(), s, _stamps())
    assert trade is None, (
        "an aggressive BUY into a book with no asks used to print full size at an "
        "invented price")


def test_synthetic_stream_has_contiguous_per_symbol_seqs():
    events = list(SyntheticFeed(n_events=800, seed=11))
    list(validate_ordering(events))                  # must not raise (BUG-076 x BUG-077)
    assert any(isinstance(e, Trade) for e in events), "the feed still prints real trades"


# ---- BUG-077: forward gaps are refused --------------------------------------------------
def test_a_forward_seq_gap_is_refused():
    a = Trade(price_ticks=100, qty=10, aggressor=Side.BUY, **_stamps(seq=1, ts=1_000))
    b = Trade(price_ticks=100, qty=10, aggressor=Side.BUY, **_stamps(seq=3, ts=2_000))
    with pytest.raises(FeedOrderingError, match="gap"):
        list(validate_ordering([a, b]))


# ---- BUG-078: replacing an in-flight order ---------------------------------------------
def test_replace_reaches_an_order_still_in_flight():
    venue = SimVenue(symbol="S", latency=LatencyModel(order_entry_ns=1_000,
                                                      market_data_ns=0, cancel_ns=0))
    intent = NewOrder(client_id="x", symbol="S", side=Side.BUY, qty=100, price_ticks=100)
    venue.accept_order(intent, owner="t", ts=0)      # in flight until ts=1_000
    assert venue.replace_order("x", new_qty=50, new_price_ticks=90, ts=10) is True
    venue.advance(2_000)                             # lands with the REPLACED terms
    order = venue.book.get("x")
    assert order is not None
    assert order.qty == 50 and order.price_ticks == 90


def test_replace_of_a_vanished_order_reports_the_miss():
    venue = SimVenue(symbol="S", latency=LatencyModel.instant())
    assert venue.replace_order("ghost", new_qty=50, ts=10) is False


# ---- BUG-090: cancels travel ------------------------------------------------------------
def test_cancel_latency_is_applied_not_instant():
    venue = SimVenue(symbol="S", latency=LatencyModel(order_entry_ns=0,
                                                      market_data_ns=0,
                                                      cancel_ns=1_000))
    intent = NewOrder(client_id="x", symbol="S", side=Side.BUY, qty=100, price_ticks=100)
    venue.accept_order(intent, owner="t", ts=0)
    venue.advance(0)                                 # rests immediately (no entry latency)
    assert "x" in venue.book
    venue.cancel_order("x", ts=100)                  # travels until ts=1_100
    venue.advance(1_099)
    assert "x" in venue.book, "the cancel must not apply before it arrives"
    venue.advance(1_100)
    assert "x" not in venue.book
    assert venue.cancelled == 1


def test_cancel_that_arrives_too_late_is_a_counted_miss():
    venue = SimVenue(symbol="S", latency=LatencyModel(order_entry_ns=0,
                                                      market_data_ns=0,
                                                      cancel_ns=1_000))
    venue.cancel_order("never-existed", ts=100)
    venue.advance(2_000)
    assert venue.cancel_misses == 1


def test_cancel_before_release_expires_order_in_same_advance_batch():
    """BUG-098: advance snapshotted ready orders and removed them from ``_pending`` before
    replaying an earlier due cancel. The cancel missed, then the stale snapshot resurrected
    the order onto the book even though wire time says the cancel won."""
    venue = SimVenue(
        symbol="S",
        latency=LatencyModel(order_entry_ns=1_000, market_data_ns=0, cancel_ns=100),
    )
    intent = NewOrder(client_id="x", symbol="S", side=Side.BUY, qty=100, price_ticks=100)
    venue.accept_order(intent, owner="t", ts=0)      # release arrives at 1_000
    venue.cancel_order("x", ts=100)                  # cancel arrives first, at 200

    outcomes = venue.advance(1_000)                  # both actions are due in one call

    assert outcomes == []
    assert "x" not in venue.book
    assert venue.in_flight == 0
    assert venue.expired_in_flight == 1
    assert venue.cancel_misses == 0
    assert venue.accepted == 0


# ---- BUG-079: the visible book is genuinely delayed ------------------------------------
def test_visible_book_serves_delayed_content_not_current():
    venue = SimVenue(symbol="S", latency=LatencyModel(order_entry_ns=0,
                                                      market_data_ns=1_000,
                                                      cancel_ns=0))
    intent = NewOrder(client_id="x", symbol="S", side=Side.BUY, qty=100, price_ticks=100)
    venue.accept_order(intent, owner="t", ts=5_000)
    venue.advance(5_000)                             # book mutates at ts=5_000
    assert venue.book.best_bid() is not None         # the VENUE sees it immediately

    early = venue.visible_book(5_500)                # 5_500 - 1_000 < 5_000: not arrived
    assert early.best_bid_ticks is None, (
        "the strategy used to see the mutation at the instant it happened")
    late = venue.visible_book(6_100)                 # 6_100 - 1_000 >= 5_000: arrived
    assert late.best_bid_ticks == 100
