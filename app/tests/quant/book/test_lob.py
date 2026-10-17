"""QT-01 — limit order book, driven by hand-built sequences with known-correct answers.

Every sequence here is short enough to verify by reading it. That is the point: this is the
layer everything else trusts, so it is pinned against arithmetic a human can check, not
against a golden file produced by the code under test.

The properties pinned, in the order they matter:

  * QUEUE POSITION IS MEASURED IN SHARES AHEAD, and it responds to activity AHEAD of an
    order while ignoring activity BEHIND it. Both directions are tested, because a
    queue model that also advances on cancels behind you is optimistic in exactly the way
    that makes a passive backtest lie.
  * A SIZE REDUCTION KEEPS PRIORITY; A REPLACE LOSES IT. These are separate methods for
    this reason alone. Modelling a partial fill as remove-then-append sends an order to the
    back after every partial and makes passive strategies look far worse than they are;
    modelling a reprice as an in-place edit hands them free priority and makes them look
    far better.
  * The book's three internal indices (level totals, sorted price ladder, order index)
    never drift from each other — ``check_integrity`` is asserted after every mutation
    sequence, not just at the end.

No matcher is constructed anywhere in this file. The book is state; matching is policy, and
fusing them would make every book test transitively depend on matching rules.
"""
from __future__ import annotations

import pytest

from quant.book.lob import BookIntegrityError, LimitOrderBook, OrderNotFound, PriceLevel
from quant.events import Side


@pytest.fixture
def book() -> LimitOrderBook:
    return LimitOrderBook("TEST")


def _queue_of_three(book: LimitOrderBook) -> None:
    """500 ahead, our 200, 300 behind — all at one price. The shape used throughout."""
    book.add(order_id="ahead", side=Side.BUY, price_ticks=100, qty=500)
    book.add(order_id="ours", side=Side.BUY, price_ticks=100, qty=200, owner="mm")
    book.add(order_id="behind", side=Side.BUY, price_ticks=100, qty=300)


# ---- queue position ------------------------------------------------------------------


def test_queue_position_counts_shares_ahead_not_orders_ahead(book):
    """Two orders ahead means nothing; two thousand shares ahead means you trade only
    after the level has turned over. Shares are what the matcher consumes."""
    _queue_of_three(book)
    assert book.queue_position("ahead") == 0
    assert book.queue_position("ours") == 500
    assert book.queue_position("behind") == 700
    book.check_integrity()


def test_a_new_order_joins_the_back_of_its_level(book):
    _queue_of_three(book)
    book.add(order_id="last", side=Side.BUY, price_ticks=100, qty=50)
    assert book.queue_position("last") == 1_000
    assert book.level_qty(Side.BUY, 100) == 1_050
    book.check_integrity()


def test_a_cancel_AHEAD_advances_us(book):
    _queue_of_three(book)
    book.cancel("ahead")
    assert book.queue_position("ours") == 0
    book.check_integrity()


def test_a_cancel_BEHIND_does_not_move_us(book):
    """The control for the previous test. A queue model that advances on cancels behind is
    optimistic, and the optimism is invisible in aggregate depth statistics."""
    _queue_of_three(book)
    before = book.queue_position("ours")
    book.cancel("behind")
    assert book.queue_position("ours") == before == 500
    book.check_integrity()


def test_a_partial_reduction_AHEAD_advances_us_by_exactly_that_amount(book):
    _queue_of_three(book)
    book.reduce("ahead", 200)
    assert book.queue_position("ours") == 300
    assert book.level_qty(Side.BUY, 100) == 800
    book.check_integrity()


def test_queue_progress_is_the_fraction_of_the_entry_queue_that_has_cleared(book):
    _queue_of_three(book)
    assert book.queue_progress("ours") == 0.0
    book.reduce("ahead", 250)
    assert book.queue_progress("ours") == pytest.approx(0.5)
    book.reduce("ahead", 250)
    assert book.queue_progress("ours") == 1.0


def test_joining_an_empty_level_reports_full_progress_not_a_division_by_zero(book):
    """Front of the queue with nothing ahead is the SAME STATE as 'everything ahead
    cleared'. Reporting 0.0 there would invert the metric's meaning."""
    book.add(order_id="first", side=Side.BUY, price_ticks=100, qty=100, owner="mm")
    assert book.queue_position("first") == 0
    assert book.queue_progress("first") == 1.0


def test_queue_position_is_per_level_so_a_deeper_price_is_not_behind_the_touch(book):
    """Priority is (price, time) — an order at a worse price is not 'behind' one at the
    touch, it is at a different level entirely and starts its own queue."""
    book.add(order_id="touch", side=Side.BUY, price_ticks=100, qty=500)
    book.add(order_id="deeper", side=Side.BUY, price_ticks=99, qty=300, owner="mm")
    assert book.queue_position("deeper") == 0
    book.check_integrity()


# ---- reduce vs replace: the priority distinction --------------------------------------


def test_reduce_keeps_priority_because_a_partial_fill_must_not_cost_the_queue(book):
    _queue_of_three(book)
    book.reduce("ours", 50)
    assert book.queue_position("ours") == 500          # unchanged: still second in line
    assert book.get("ours").qty == 150
    assert book.get("ours").filled_qty == 50
    book.check_integrity()


def test_replace_loses_priority_even_when_the_price_is_unchanged(book):
    """The most tempting shortcut in a simulator is to treat a requote as an in-place edit.
    It hands a requoting market maker free priority, which is precisely the cost the
    strategy is supposed to be weighing."""
    _queue_of_three(book)
    book.replace("ours", new_qty=150)
    assert book.queue_position("ours") == 800          # now behind 'ahead' AND 'behind'
    assert book.get("ours").price_ticks == 100
    book.check_integrity()


def test_replace_to_a_new_price_moves_levels_and_starts_a_fresh_queue(book):
    _queue_of_three(book)
    book.replace("ours", new_price_ticks=101)
    assert book.get("ours").price_ticks == 101
    assert book.queue_position("ours") == 0
    assert book.level_qty(Side.BUY, 100) == 800
    assert book.level_qty(Side.BUY, 101) == 200
    assert book.best_bid() == 101
    book.check_integrity()


def test_replace_preserves_ownership_so_a_requote_does_not_become_market_flow(book):
    _queue_of_three(book)
    book.replace("ours", new_price_ticks=101)
    assert book.get("ours").owner == "mm"
    assert [o.order_id for o in book.our_orders()] == ["ours"]


# ---- level lifecycle and the price ladder ---------------------------------------------


def test_a_level_is_dropped_from_both_indices_when_it_empties(book):
    book.add(order_id="only", side=Side.BUY, price_ticks=100, qty=100)
    book.add(order_id="deeper", side=Side.BUY, price_ticks=99, qty=100)
    book.cancel("only")
    assert book.level(Side.BUY, 100) is None
    assert book.prices(Side.BUY) == [99]
    assert book.best_bid() == 99
    book.check_integrity()


def test_reducing_an_order_to_zero_removes_it_and_its_level(book):
    book.add(order_id="only", side=Side.BUY, price_ticks=100, qty=100)
    book.reduce("only", 100)
    assert book.get("only") is None
    assert book.best_bid() is None
    assert len(book) == 0
    book.check_integrity()


def test_prices_are_returned_best_first_on_both_sides(book):
    """Internally both ladders are ascending; callers always want touch-outward. Returning
    raw ascending bids is how a matcher ends up walking the book backwards."""
    for tick in (98, 100, 99):
        book.add(order_id=f"b{tick}", side=Side.BUY, price_ticks=tick, qty=100)
    for tick in (103, 101, 102):
        book.add(order_id=f"a{tick}", side=Side.SELL, price_ticks=tick, qty=100)
    assert book.prices(Side.BUY) == [100, 99, 98]
    assert book.prices(Side.SELL) == [101, 102, 103]
    assert book.best_bid() == 100
    assert book.best_ask() == 101
    assert book.spread_ticks == 1


def test_depth_reports_aggregate_size_and_order_count_per_level(book):
    book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=300)
    book.add(order_id="b", side=Side.BUY, price_ticks=100, qty=200)
    book.add(order_id="c", side=Side.BUY, price_ticks=99, qty=400)
    depth = book.depth(Side.BUY, levels=5)
    assert [(d.price_ticks, d.qty, d.order_count) for d in depth] == [(100, 500, 2), (99, 400, 1)]


def test_a_crossed_book_is_representable_because_real_feeds_produce_them(book):
    """Transiently crossed books happen between the halves of a price update. A book that
    refuses to hold one cannot replay a real feed."""
    book.add(order_id="b", side=Side.BUY, price_ticks=101, qty=100)
    book.add(order_id="a", side=Side.SELL, price_ticks=100, qty=100)
    assert book.is_crossed
    assert book.spread_ticks == -1
    book.check_integrity()


def test_an_uncrossed_book_is_not_reported_as_crossed(book):
    book.add(order_id="b", side=Side.BUY, price_ticks=100, qty=100)
    book.add(order_id="a", side=Side.SELL, price_ticks=101, qty=100)
    assert not book.is_crossed


def test_a_one_sided_book_has_no_spread_and_is_not_crossed(book):
    book.add(order_id="b", side=Side.BUY, price_ticks=100, qty=100)
    assert book.spread_ticks is None
    assert not book.is_crossed


# ---- failure modes are loud -----------------------------------------------------------


def test_cancelling_an_unknown_order_raises_because_it_means_the_feed_desynced(book):
    """A cancel for an order we never saw means the corresponding add was dropped: the book
    has silently diverged from the venue's and every later queue position is wrong by an
    unknown amount. Swallowing it turns a detectable desync into a plausible backtest."""
    with pytest.raises(OrderNotFound):
        book.cancel("never-existed")


def test_a_duplicate_order_id_is_refused(book):
    book.add(order_id="dup", side=Side.BUY, price_ticks=100, qty=100)
    with pytest.raises(ValueError, match="duplicate"):
        book.add(order_id="dup", side=Side.SELL, price_ticks=101, qty=100)


def test_reducing_by_more_than_remaining_is_refused(book):
    book.add(order_id="o", side=Side.BUY, price_ticks=100, qty=100)
    with pytest.raises(ValueError, match="only 100 remaining"):
        book.reduce("o", 101)


def test_a_zero_or_negative_quantity_order_is_not_representable(book):
    with pytest.raises(ValueError):
        book.add(order_id="z", side=Side.BUY, price_ticks=100, qty=0)


def test_check_integrity_actually_detects_corruption():
    """CONTROL for every ``check_integrity()`` call above.

    An integrity check that cannot fail proves nothing, and every other test in this file
    leans on it. Corrupt the cached level total behind the book's back and confirm it is
    caught — otherwise a silently-drifting aggregate would sail through the whole suite.
    """
    book = LimitOrderBook("TEST")
    book.add(order_id="o", side=Side.BUY, price_ticks=100, qty=100)
    book.check_integrity()
    level: PriceLevel = book.level(Side.BUY, 100)
    level.total_qty = 999
    with pytest.raises(BookIntegrityError, match="cached total"):
        book.check_integrity()


def test_check_integrity_detects_a_desynced_price_ladder():
    """The second control: level totals and the sorted ladder drift independently."""
    book = LimitOrderBook("TEST")
    book.add(order_id="o", side=Side.BUY, price_ticks=100, qty=100)
    book.prices(Side.BUY)  # returns a copy — mutating it must NOT affect the book
    book._bid_prices.append(105)
    with pytest.raises(BookIntegrityError):
        book.check_integrity()


def test_prices_returns_a_copy_so_callers_cannot_corrupt_the_ladder(book):
    book.add(order_id="o", side=Side.BUY, price_ticks=100, qty=100)
    ladder = book.prices(Side.BUY)
    ladder.append(999)
    assert book.prices(Side.BUY) == [100]
    book.check_integrity()


# ---- ownership -------------------------------------------------------------------------


def test_our_orders_are_separable_from_market_flow_which_is_what_makes_a_queue_meaningful(book):
    """A book containing only our orders has no queue to be positioned in. One book holds
    both, and ``owner`` is the only thing distinguishing them."""
    _queue_of_three(book)
    book.add(order_id="ours2", side=Side.SELL, price_ticks=102, qty=100, owner="momentum")
    assert sorted(o.order_id for o in book.our_orders()) == ["ours", "ours2"]
    assert [o.order_id for o in book.our_orders(owner="mm")] == ["ours"]
    assert len(book) == 4


def test_snapshot_is_immutable_and_decoupled_from_later_book_mutation(book):
    """Consumers (the WS broadcast, verify.py, tearsheets) must not observe the book
    changing underneath them, and must not be able to change it."""
    _queue_of_three(book)
    snap = book.snapshot(levels=5)
    book.cancel("ahead")
    assert snap.bids[0].qty == 1_000          # the snapshot did not follow the cancel
    assert book.level_qty(Side.BUY, 100) == 500
    with pytest.raises(Exception):
        snap.bids = ()


def test_retained_orders_stay_live_across_reduce_cancel_and_replace(book):
    """Callers keep the object ``add`` returned. ``reduce`` edits that object.

    ``cancel`` returns it and leaves its qty alone. ``replace`` is cancel-plus-add,
    so the old object stays at its old qty and the book holds a new one.
    """
    held = book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=10, owner="mm", seq=1, ts=1)
    assert book.get("a") is held
    assert book.level(Side.BUY, 100).orders["a"] is held
    assert book._index["a"] is held
    assert book.our_orders()[0] is held

    assert book.reduce("a", 3) is held
    assert held.qty == 7
    assert held.filled_qty == 3
    assert book.level(Side.BUY, 100).orders["a"] is held
    assert book.reduce("a", 7) is held
    assert held.qty == 0
    assert book.get("a") is None
    book.check_integrity()

    held = book.add(order_id="b", side=Side.BUY, price_ticks=100, qty=10, owner="mm")
    assert book.cancel("b") is held
    assert held.qty == 10
    assert book.get("b") is None

    held = book.add(order_id="c", side=Side.BUY, price_ticks=100, qty=10, owner="mm", seq=1, ts=1)
    replaced = book.replace("c", new_qty=4, new_price_ticks=101, seq=8, ts=8)
    assert replaced is not held
    assert held.qty == 10
    assert replaced.qty == 4
    assert book.get("c") is replaced
    assert book.level(Side.BUY, 101).orders["c"] is replaced
    book.check_integrity()


def test_snapshot_derived_statistics_agree_with_the_live_book(book):
    book.add(order_id="b", side=Side.BUY, price_ticks=100, qty=900)
    book.add(order_id="a", side=Side.SELL, price_ticks=102, qty=100)
    snap = book.snapshot()
    assert snap.best_bid_ticks == 100 and snap.best_ask_ticks == 102
    assert snap.spread_ticks == 2
    assert snap.mid_ticks == 101.0
    assert snap.microprice == pytest.approx(101.8)
    assert snap.imbalance == pytest.approx(0.8)


def test_snapshot_is_reused_until_the_book_changes(book):
    from quant.events import Side

    book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=10)
    book.add(order_id="b", side=Side.SELL, price_ticks=101, qty=4)
    first = book.snapshot()
    assert book.snapshot() is first
    assert book.snapshot(levels=5) is first
    stamped = book.snapshot(ts=7)
    assert stamped is not first and stamped.ts == 7
    assert book.snapshot(ts=7) is stamped
    book.reduce("a", 3)
    second = book.snapshot()
    assert second is not first
    assert second.bids[0].qty == 7
    assert book.snapshot() is second


def test_python_snapshot_is_reused_until_the_book_changes():
    from quant.book.lob import PythonLimitOrderBook
    from quant.events import Side

    book = PythonLimitOrderBook("S")
    book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=10)
    first = book.snapshot()
    assert book.snapshot() is first
    book.add(order_id="c", side=Side.BUY, price_ticks=99, qty=1)
    assert book.snapshot() is not first


def test_rejected_edits_keep_the_snapshot():
    from quant.book.lob import PythonLimitOrderBook

    for make in (LimitOrderBook, PythonLimitOrderBook):
        book = make("S")
        book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=10)
        first = book.snapshot()
        with pytest.raises(ValueError):
            book.reduce("a", 11)
        with pytest.raises(ValueError):
            book.reduce("a", 0)
        with pytest.raises(ValueError):
            book.replace("a", new_qty=0)
        with pytest.raises(ValueError):
            book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=1)
        assert book.snapshot() is first
        assert book.snapshot().bids[0].qty == 10


def test_a_direct_level_total_edit_refreshes_the_snapshot():
    from quant.book.lob import PythonLimitOrderBook

    for make in (LimitOrderBook, PythonLimitOrderBook):
        book = make("S")
        book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=10)
        first = book.snapshot()
        level = book.level(Side.BUY, 100)
        level.total_qty = 10
        assert book.snapshot() is first
        level.total_qty = 11
        second = book.snapshot()
        assert second is not first
        assert second.bids[0].qty == 11
        assert book.depth(Side.BUY, 1)[0].qty == 11


def test_resting_at_follows_insertion_order_and_rejects_a_bad_index(book):
    book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=10)
    book.add(order_id="b", side=Side.SELL, price_ticks=101, qty=20)
    book.add(order_id="c", side=Side.BUY, price_ticks=99, qty=30)
    assert book.resting_at(0).order_id == "a"
    assert book.resting_at(1).order_id == "b"
    assert book.resting_at(2).qty == 30
    book.cancel("b")
    assert book.resting_at(0).order_id == "a"
    assert book.resting_at(1).order_id == "c"
    for bad in (2, -1, True, 2**64):
        with pytest.raises(IndexError):
            book.resting_at(bad)
