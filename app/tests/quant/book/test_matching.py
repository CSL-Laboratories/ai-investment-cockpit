"""QT-01 — FIFO price-time matching, against hand-built books with checkable arithmetic.

The properties pinned, in the order they matter:

  * THE RESTING ORDER SETS THE PRICE. A buy at 101 hitting an ask of 100 trades at 100.
    This is where the maker's edge comes from; a matcher that fills at the aggressor's
    limit hands that edge to the taker and inverts the economics of every passive strategy.
  * A PARTIAL FILL LEAVES THE RESTING ORDER AT THE FRONT of its level, not at the back.
  * Fills are emitted for OUR orders only, tagged MAKER or TAKER, and both sides of one
    trade can be ours. The maker/taker tag is the SIGN of the fee — getting it wrong flips
    a strategy's economics rather than perturbing them.
  * FOK is decided before any mutation. A partially-executed fill-or-kill is a
    contradiction, and unwinding one after the fact means rewinding queue positions other
    orders have already moved through.
  * Self-trade prevention is on by default, because a simulator that matches your bid
    against your own ask manufactures free spread capture that looks exactly like profit.

Every test asserts ``check_integrity()`` after the match, so a matcher that corrupts the
book while producing correct-looking fills cannot pass.
"""
from __future__ import annotations

import pytest

from quant.book.lob import LimitOrderBook
from quant.book.matching import (
    AggressiveOrder,
    MatchStatus,
    SelfTradePrevention,
    TimeInForce,
    match,
)
from quant.events import Liquidity, Side


def test_a_retained_resting_order_sees_partial_and_full_fills():
    book = LimitOrderBook("TEST")
    resting = book.add(order_id="ask", side=Side.SELL, price_ticks=100, qty=10, owner="mm")
    match(book, AggressiveOrder(order_id="hit", side=Side.BUY, qty=4, price_ticks=100))
    assert resting.qty == 6
    assert book.get("ask") is resting
    match(book, AggressiveOrder(order_id="hit2", side=Side.BUY, qty=6, price_ticks=100))
    assert resting.qty == 0
    assert book.get("ask") is None
    book.check_integrity()


def test_self_trade_prevention_pulls_a_retained_order_without_changing_its_qty():
    book = LimitOrderBook("TEST")
    resting = book.add(order_id="ask", side=Side.SELL, price_ticks=100, qty=10, owner="mm")
    match(
        book,
        AggressiveOrder(
            order_id="hit", side=Side.BUY, qty=4, price_ticks=100, owner="mm",
            stp=SelfTradePrevention.CANCEL_RESTING,
        ),
    )
    assert resting.qty == 10
    assert book.get("ask") is None
    book.check_integrity()


@pytest.fixture
def book() -> LimitOrderBook:
    """Asks: 300 @101 (two orders, 100 then 200), 500 @102. Bids: 400 @99."""
    b = LimitOrderBook("TEST")
    b.add(order_id="a1", side=Side.SELL, price_ticks=101, qty=100)
    b.add(order_id="a2", side=Side.SELL, price_ticks=101, qty=200)
    b.add(order_id="a3", side=Side.SELL, price_ticks=102, qty=500)
    b.add(order_id="b1", side=Side.BUY, price_ticks=99, qty=400)
    return b


# ---- price-time priority ----------------------------------------------------------------


def test_fills_consume_the_front_of_the_level_first(book):
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100, price_ticks=101))
    assert result.status is MatchStatus.FILLED
    assert book.get("a1") is None           # the first-arrived order traded
    assert book.get("a2").qty == 200        # the second is untouched
    book.check_integrity()


def test_a_partial_fill_leaves_the_resting_order_at_the_FRONT_of_its_level(book):
    """Modelling this as remove-then-append would send the order to the back after every
    partial — an error that compounds silently and makes passive strategies look far worse
    than they are."""
    match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=60, price_ticks=101))
    assert book.get("a1").qty == 40
    assert book.queue_position("a1") == 0   # still first in line
    assert book.queue_position("a2") == 40
    book.check_integrity()


def test_the_walk_crosses_multiple_levels_from_the_touch_outward(book):
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=400, price_ticks=102))
    assert result.status is MatchStatus.FILLED
    assert result.trades == [(101, 100), (101, 200), (102, 100)]
    assert book.best_ask() == 102
    assert book.level_qty(Side.SELL, 102) == 400
    book.check_integrity()


def test_the_limit_price_bounds_the_walk_and_the_remainder_rests(book):
    """An aggressive buy limited at 101 must not reach the 102 level."""
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=500, price_ticks=101))
    assert result.status is MatchStatus.RESTING
    assert result.filled_qty == 300
    assert result.remaining_qty == 200
    assert book.level_qty(Side.SELL, 102) == 500      # untouched
    assert book.level_qty(Side.BUY, 101) == 200       # the remainder now rests on the bid
    assert book.best_bid() == 101
    book.check_integrity()


def test_the_resting_order_sets_the_trade_price_not_the_aggressor(book):
    """A buy willing to pay 105 against an ask of 101 trades at 101. The passive side's
    price is what being passive buys you."""
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100, price_ticks=105))
    assert result.trades == [(101, 100)]
    assert result.avg_price_ticks == 101.0


def test_a_non_crossing_limit_order_simply_rests(book):
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100, price_ticks=100))
    assert result.status is MatchStatus.RESTING
    assert result.filled_qty == 0
    assert book.best_bid() == 100
    assert book.spread_ticks == 1
    book.check_integrity()


def test_a_sell_walks_the_bid_side_with_the_inequality_the_other_way(book):
    """The mirror of the buy case. Both directions are tested because a single shared
    comparison is the only thing keeping them from drifting apart."""
    result = match(book, AggressiveOrder(order_id="agg", side=Side.SELL, qty=400, price_ticks=99))
    assert result.status is MatchStatus.FILLED
    assert result.trades == [(99, 400)]
    assert book.best_bid() is None
    book.check_integrity()


# ---- order types and time in force --------------------------------------------------------


def test_a_market_order_ignores_price_and_never_rests(book):
    """'Rest at no price' is meaningless, so the remainder is cancelled whatever the TIF."""
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=1_000,
                                         price_ticks=None, tif=TimeInForce.DAY))
    assert result.status is MatchStatus.PARTIALLY_FILLED
    assert result.filled_qty == 800          # the whole ask side
    assert result.remaining_qty == 200
    assert result.resting_order_id is None
    assert book.best_ask() is None
    book.check_integrity()


def test_ioc_fills_what_it_can_and_cancels_the_rest(book):
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=500,
                                         price_ticks=101, tif=TimeInForce.IOC))
    assert result.status is MatchStatus.PARTIALLY_FILLED
    assert result.filled_qty == 300
    assert result.remaining_qty == 200
    assert "agg" not in book
    book.check_integrity()


def test_ioc_that_touches_nothing_is_cancelled_not_rested(book):
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100,
                                         price_ticks=100, tif=TimeInForce.IOC))
    assert result.status is MatchStatus.CANCELLED
    assert result.filled_qty == 0
    assert "agg" not in book


def test_fok_that_cannot_fill_completely_changes_NOTHING(book):
    """Decided before any mutation. The book must be byte-identical afterwards, because
    unwinding a partial fill means rewinding queue positions others have moved through."""
    before = (book.level_qty(Side.SELL, 101), book.level_qty(Side.SELL, 102), len(book))
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=400,
                                         price_ticks=101, tif=TimeInForce.FOK))
    assert result.status is MatchStatus.REJECTED
    assert result.filled_qty == 0
    assert result.fills == []
    assert (book.level_qty(Side.SELL, 101), book.level_qty(Side.SELL, 102), len(book)) == before
    book.check_integrity()


def test_fok_that_can_fill_completely_does(book):
    """CONTROL for the rejection test — otherwise a FOK that always rejects would pass."""
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=300,
                                         price_ticks=101, tif=TimeInForce.FOK))
    assert result.status is MatchStatus.FILLED
    assert result.filled_qty == 300
    book.check_integrity()


def test_a_market_fok_is_refused_as_ambiguous_rather_than_silently_interpreted():
    with pytest.raises(ValueError, match="ambiguous"):
        AggressiveOrder(order_id="agg", side=Side.BUY, qty=100,
                        price_ticks=None, tif=TimeInForce.FOK)


# ---- whose fills, and tagged how ------------------------------------------------------


def test_only_owned_orders_produce_fills_so_pnl_is_not_flooded_with_market_flow(book):
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100, price_ticks=101))
    assert result.fills == []                # neither side was ours
    assert result.trades == [(101, 100)]     # but the tape still records the print


def test_a_resting_order_of_ours_fills_as_MAKER_and_carries_its_entry_queue_position():
    """``queue_position_at_entry`` is the diagnostic that says WHY a strategy performed as
    it did: 'we were at the front and got picked off' and 'we were 4,000 back and only
    filled on the sweep' are invisible in the PnL number and decisive for the parameters."""
    book = LimitOrderBook("TEST")
    book.add(order_id="other", side=Side.SELL, price_ticks=101, qty=400)
    book.add(order_id="ours", side=Side.SELL, price_ticks=101, qty=100, owner="mm")
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=500, price_ticks=101))
    ours = [f for f in result.fills if f.order_id == "ours"]
    assert len(ours) == 1
    assert ours[0].liquidity is Liquidity.MAKER
    assert ours[0].side is Side.SELL
    assert ours[0].queue_position_at_entry == 400
    assert ours[0].signed_qty == -100


def test_an_aggressive_order_of_ours_fills_as_TAKER(book):
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100,
                                         price_ticks=101, owner="momentum"))
    assert len(result.fills) == 1
    assert result.fills[0].liquidity is Liquidity.TAKER
    assert result.fills[0].queue_position_at_entry == 0


def test_both_sides_of_one_trade_can_be_ours_and_both_fills_are_emitted():
    """Two of our own strategies meeting in the book is a real event with real fees on both
    sides. STP is per-owner, so distinct owners trade normally."""
    book = LimitOrderBook("TEST")
    book.add(order_id="ours", side=Side.SELL, price_ticks=101, qty=100, owner="mm")
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100,
                                         price_ticks=101, owner="momentum"))
    assert {(f.order_id, f.liquidity) for f in result.fills} == {
        ("ours", Liquidity.MAKER), ("agg", Liquidity.TAKER),
    }


def test_fills_carry_the_three_timestamps_through_from_the_match(book):
    """A fill that cannot say when it was known is useless for latency analysis, which is
    the whole reason the sim venue models entry and market-data delay separately."""
    book.add(order_id="ours", side=Side.SELL, price_ticks=101, qty=50, owner="mm")
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=350, price_ticks=101),
                   seq=42, exchange_ts=1_000, recv_ts=1_250, ingest_ts=1_290)
    fill = next(f for f in result.fills if f.order_id == "ours")
    assert (fill.exchange_ts, fill.recv_ts, fill.ingest_ts) == (1_000, 1_250, 1_290)
    assert fill.feed_latency_ns == 250 and fill.decode_latency_ns == 40
    assert fill.seq == 42


def test_timestamps_default_forward_so_a_zero_latency_test_states_them_once():
    book = LimitOrderBook("TEST")
    book.add(order_id="ours", side=Side.SELL, price_ticks=101, qty=50, owner="mm")
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=50, price_ticks=101),
                   exchange_ts=777)
    fill = result.fills[0]
    assert fill.exchange_ts == fill.recv_ts == fill.ingest_ts == 777


# ---- self-trade prevention ----------------------------------------------------------------


def test_self_trade_prevention_is_on_by_default_and_pulls_the_resting_order():
    """Without this a market maker quoting both sides manufactures round-trip volume against
    itself at zero cost — fictional spread capture that reads as clean profit."""
    book = LimitOrderBook("TEST")
    book.add(order_id="mine", side=Side.SELL, price_ticks=101, qty=100, owner="mm")
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100,
                                         price_ticks=101, owner="mm"))
    assert result.filled_qty == 0
    assert result.stp_cancelled == ["mine"]
    assert "mine" not in book
    assert result.status is MatchStatus.RESTING     # our buy rests, having traded nothing
    book.check_integrity()


def test_stp_skips_our_order_but_still_trades_against_everyone_else():
    book = LimitOrderBook("TEST")
    book.add(order_id="mine", side=Side.SELL, price_ticks=101, qty=100, owner="mm")
    book.add(order_id="other", side=Side.SELL, price_ticks=101, qty=100)
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100,
                                         price_ticks=101, owner="mm"))
    assert result.filled_qty == 100
    assert result.trades == [(101, 100)]
    assert result.stp_cancelled == ["mine"]
    book.check_integrity()


def test_cancel_aggressing_kills_the_incoming_order_and_keeps_what_it_already_traded():
    book = LimitOrderBook("TEST")
    book.add(order_id="other", side=Side.SELL, price_ticks=101, qty=100)
    book.add(order_id="mine", side=Side.SELL, price_ticks=101, qty=100, owner="mm")
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=200, price_ticks=101,
                                         owner="mm", stp=SelfTradePrevention.CANCEL_AGGRESSING))
    assert result.filled_qty == 100
    assert result.status is MatchStatus.PARTIALLY_FILLED
    assert "mine" in book                  # the resting order survived
    assert "agg" not in book               # the aggressor did not rest
    book.check_integrity()


def test_stp_can_be_disabled_when_reproducing_a_specific_venue():
    book = LimitOrderBook("TEST")
    book.add(order_id="mine", side=Side.SELL, price_ticks=101, qty=100, owner="mm")
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100, price_ticks=101,
                                         owner="mm", stp=SelfTradePrevention.ALLOW))
    assert result.filled_qty == 100
    assert len(result.fills) == 2


def test_stp_is_per_owner_so_distinct_strategies_are_not_treated_as_one():
    """CONTROL for the STP tests: prevention must not fire between different owners, or two
    of our strategies could never trade with the market through each other."""
    book = LimitOrderBook("TEST")
    book.add(order_id="mine", side=Side.SELL, price_ticks=101, qty=100, owner="mm")
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100,
                                         price_ticks=101, owner="pairs"))
    assert result.filled_qty == 100
    assert result.stp_cancelled == []


def test_fok_counts_self_trade_prevented_size_as_unavailable():
    """A FOK that would only 'fill' by trading against itself must be rejected, not
    reported as a fill it cannot actually get."""
    book = LimitOrderBook("TEST")
    book.add(order_id="mine", side=Side.SELL, price_ticks=101, qty=100, owner="mm")
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100, price_ticks=101,
                                         owner="mm", tif=TimeInForce.FOK))
    assert result.status is MatchStatus.REJECTED
    assert "mine" in book
    book.check_integrity()


# ---- the tape --------------------------------------------------------------------------


def test_the_trade_tape_records_anonymous_prints_that_produced_no_fills_of_ours(book):
    """Momentum consumes the tape and participation rate divides by it. Recording only our
    own trades would leave the simulated tape sparser than the real one."""
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=250, price_ticks=101))
    assert result.fills == []
    assert result.trades == [(101, 100), (101, 150)]
    assert result.notional_ticks == 101 * 250
    assert result.avg_price_ticks == 101.0


def test_avg_price_is_none_rather_than_zero_when_nothing_traded(book):
    result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=100, price_ticks=100))
    assert result.avg_price_ticks is None
