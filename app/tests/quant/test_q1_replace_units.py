"""Q1: a replace's size is a NEW TOTAL (guards.py:206, oms.py:249); the book rests REMAINING size.

Red on main 7fb17b6. One test per place a replace crosses OMS -> guard -> venue -> book.
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from quant.book.matching import AggressiveOrder, TimeInForce, match
from quant.events import BookAction, BookUpdate, Event, PriceScale, Side, Trade
from quant.execution.autoloop import AutoLoop
from quant.execution.sim_venue import LatencyModel, SimVenue
from quant.guards import GuardLimits, Guards
from quant.oms import NewOrder, ReplaceOrder
from quant.runner import Runner
from quant.strategies.base import Strategy

SCALE = PriceScale(Decimal("1"))


def _stamps(ts=1, seq=1):
    return dict(symbol="S", seq=seq, exchange_ts=ts, recv_ts=ts, ingest_ts=ts)


class Scripted(Strategy):
    """Emits scripted intents on book updates (keyed by ctx.ts) and on the Nth fill."""

    def __init__(self, on_book=None, on_fill=None):
        super().__init__(name="t", symbols=("S",))
        self.on_book = dict(on_book or {})
        self.on_fill_script = dict(on_fill or {})
        self.fills_seen = 0

    def on_book_update(self, ctx):
        return self.on_book.pop(ctx.ts, [])

    def on_fill(self, fill, ctx):
        self.fills_seen += 1
        return self.on_fill_script.pop(self.fills_seen, [])

    def describe(self):
        return {"name": self.name}


def _seeded_runner(qty=200, guards=None):
    strategy = Scripted()
    runner = Runner([], [strategy], SCALE, guards=guards)
    order = NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=qty, price_ticks=100)
    runner.oms.register(order, strategy="t")
    runner.oms.on_acknowledged("a")
    runner.book_for("S").add(order_id="a", owner="t", side=Side.BUY, price_ticks=100, qty=qty)
    return runner


def _sell_into(runner, qty):
    result = match(runner.book_for("S"), AggressiveOrder(
        order_id="market", side=Side.SELL, qty=qty, price_ticks=100, tif=TimeInForce.IOC))
    runner._absorb_fills(result.fills, runner.context(1, "S"))


# ---- S1 (sweep F01): Runner._replace forwards a total as the resting size ----------------
def test_q1_s1_runner_partial_fill_resize_rests_remaining_shares():
    runner = _seeded_runner()
    _sell_into(runner, 50)
    request = ReplaceOrder(client_id="a", new_qty=300)
    assert Guards(GuardLimits(max_position=300)).check(request, oms=runner.oms).allowed
    runner._replace(request, Event(**_stamps()))
    assert runner.book_for("S").get("a").qty == runner.oms.get("a").remaining_qty == 250


# ---- S2: SimVenue.replace_order (resting branch) via AutoLoop ------------------------------
def _trade(ts, qty, price=100):
    return Trade(price_ticks=price, qty=qty, aggressor=Side.SELL, **_stamps(ts, ts))


def test_q1_s2_autoloop_resize_after_partial_fill_cannot_exceed_the_total():
    strategy = Scripted(on_book={
        1: [NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=10, price_ticks=100)],
        3: [ReplaceOrder(client_id="a", new_qty=10)],
    })
    loop = AutoLoop(venue=SimVenue(symbol="S", latency=LatencyModel.instant()),
                    strategies=[strategy], scale=SCALE,
                    guards=Guards(GuardLimits(max_position=10)))
    result = loop.run([_trade(1, 1, 101), _trade(2, 3), _trade(3, 1, 101),
                       _trade(4, 1, 101), _trade(5, 20), _trade(6, 1, 101)])
    assert result.accounting_valid and not result.halted, result.halt_reason
    assert result.final_position == 10
    assert sum(f.qty for f in result.fills if f.order_id == "a") == 10


# ---- S3: the venue must use ITS fill count, not the OMS's count at send time ---------------
def test_q1_s3_fill_in_flight_between_send_and_arrival_is_counted_at_the_venue():
    # Fill reports travel 10ns. The trade at exchange 2 fills 3 before the strategy decides at
    # ts 5, but the OMS only learns of it at 12. The guard judges a total of 10 against the cap
    # of 10; the venue must rest 10 - 3 = 7, not 10.
    strategy = Scripted(on_book={
        1: [NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=10, price_ticks=100)],
        5: [ReplaceOrder(client_id="a", new_qty=10)],
    })
    loop = AutoLoop(venue=SimVenue(symbol="S", latency=LatencyModel(
                        order_entry_ns=0, market_data_ns=10, cancel_ns=0)),
                    strategies=[strategy], scale=SCALE,
                    guards=Guards(GuardLimits(max_position=10)))
    result = loop.run([_trade(1, 1, 101), _trade(2, 3), _trade(5, 1, 101), _trade(6, 1, 101),
                       _trade(20, 1, 101), _trade(30, 20), _trade(50, 1, 101)])
    assert result.accounting_valid and not result.halted, result.halt_reason
    assert result.final_position == 10
    assert loop.oms.get("a").remaining_qty == 0


# ---- S4: Runner, a replace emitted from on_fill while later fills of the same match wait ----
def test_q1_s4_runner_replace_from_first_fill_counts_the_whole_match():
    strategy = Scripted(
        on_book={3: [NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=50, price_ticks=102)]},
        on_fill={1: [ReplaceOrder(client_id="a", new_qty=40)]},
    )
    feed = [
        BookUpdate(action=BookAction.ADD, side=Side.SELL, price_ticks=101, qty=10, order_ref=1,
                   **_stamps(1, 1)),
        BookUpdate(action=BookAction.ADD, side=Side.SELL, price_ticks=102, qty=10, order_ref=2,
                   **_stamps(2, 2)),
        BookUpdate(action=BookAction.ADD, side=Side.BUY, price_ticks=90, qty=1, order_ref=3,
                   **_stamps(3, 3)),
    ]
    runner = Runner(feed, [strategy], SCALE)
    runner.run()
    order = runner.oms.get("a")
    assert order.filled_qty == 20
    assert runner.book_for("S").get("a").qty == order.remaining_qty == 20


# ---- S5: a replace to at or below the filled size --------------------------------------
def test_q1_s5_guard_refuses_a_total_that_leaves_nothing_to_rest():
    runner = _seeded_runner()
    _sell_into(runner, 50)
    guards = Guards(GuardLimits(max_position=300))
    for total in (30, 50):
        assert not guards.check(ReplaceOrder(client_id="a", new_qty=total), oms=runner.oms).allowed


def test_q1_s5_runner_refuses_before_mutating_the_book():
    for total in (30, 50):
        runner = _seeded_runner()
        _sell_into(runner, 50)
        runner._replace(ReplaceOrder(client_id="a", new_qty=total), Event(**_stamps()))
        assert runner.book_for("S").get("a").qty == 150
        assert runner.oms.get("a").qty == 200 and runner.oms.get("a").remaining_qty == 150


def test_q1_s5_venue_refuses_a_total_at_or_below_its_filled_size():
    venue = SimVenue(symbol="S", latency=LatencyModel.instant())
    venue.accept_order(NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=200,
                                price_ticks=100), owner="t", ts=0)
    venue.advance(0)
    match(venue.book, AggressiveOrder(order_id="m", side=Side.SELL, qty=50, price_ticks=100,
                                      tif=TimeInForce.IOC))
    assert venue.replace_order("a", new_qty=50, ts=1) is False
    assert venue.book.get("a").qty == 150
    assert venue.replace_order("a", new_qty=300, ts=1) is True
    assert venue.book.get("a").qty == 250


# ---- the sweep's F01 witness (S6), with its helper, as written there --------------------
class Recorder(Strategy):
    def __init__(self, scripted=None):
        super().__init__(name='t', symbols=('S',))
        self.scripted = scripted or {}
        self.observed_fills = []
        self.observed_timers = []

    def on_book_update(self, ctx):
        return self.scripted.pop(ctx.ts, [])

    def on_fill(self, execution, ctx):
        self.observed_fills.append((ctx.position('S'), tuple(o.client_id for o in ctx.live_orders)))
        return []

    def on_timer(self, ts, ctx):
        self.observed_timers.append((ts, ctx.ts, ctx.book))
        return []

    def describe(self):
        return {'name': self.name}


def seeded_runner(qty=200):
    strategy = Recorder()
    runner = Runner([], [strategy], SCALE)
    order = NewOrder(client_id='a', symbol='S', side=Side.BUY, qty=qty, price_ticks=100)
    runner.oms.register(order, strategy='t')
    runner.oms.on_acknowledged('a')
    runner.book_for('S').add(order_id='a', owner='t', side=Side.BUY, price_ticks=100, qty=qty)
    return runner, strategy


class QuantWitnesses(unittest.TestCase):
    def test_F01_partial_fill_resize_uses_remaining_shares(self):
        runner, _ = seeded_runner()
        result = match(runner.book_for('S'), AggressiveOrder(
            order_id='market', side=Side.SELL, qty=50, price_ticks=100, tif=TimeInForce.IOC))
        runner._absorb_fills(result.fills, runner.context(1, 'S'))
        request = ReplaceOrder(client_id='a', new_qty=300)
        guards = Guards(GuardLimits(max_position=300))
        self.assertTrue(guards.check(request, oms=runner.oms).allowed)
        runner._replace(request, Event(**_stamps()))
        self.assertEqual(runner.book_for('S').get('a').qty, runner.oms.get('a').remaining_qty)


# ---- S6: the venue's fills-before-entry memory (answer to review 1, F1) -------------------
def _venue_with_partial_fill(qty=200, filled=50):
    venue = SimVenue(symbol="S", latency=LatencyModel.instant())
    venue.accept_order(NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=qty,
                                price_ticks=100), owner="t", ts=0)
    venue.advance(0)
    match(venue.book, AggressiveOrder(order_id="m", side=Side.SELL, qty=filled, price_ticks=100,
                                      tif=TimeInForce.IOC))
    return venue


def test_q1_s6_venue_second_replace_to_the_same_total_rests_the_same_remainder():
    venue = _venue_with_partial_fill()
    assert venue.replace_order("a", new_qty=300, ts=1) is True
    assert venue.book.get("a").qty == 250
    assert venue.replace_order("a", new_qty=300, ts=2) is True
    assert venue.book.get("a").qty == 250       # the 50 filled before the first replace still count


def test_q1_s6_venue_fill_on_arrival_is_remembered_by_a_later_replace():
    venue = SimVenue(symbol="S", latency=LatencyModel.instant())
    venue.book.add(order_id="liq", owner="x", side=Side.SELL, price_ticks=100, qty=4)
    venue.accept_order(NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=10,
                                price_ticks=100), owner="t", ts=0)
    venue.advance(0)
    assert venue.book.get("a").qty == 6         # 4 traded on arrival, 6 rest
    assert venue.replace_order("a", new_qty=10, ts=1) is True
    assert venue.book.get("a").qty == 6         # a total of 10 with 4 filled is 6, not 10


def test_q1_s6_venue_cancel_forgets_the_fills_before_entry():
    venue = _venue_with_partial_fill()
    assert venue.replace_order("a", new_qty=300, ts=1) is True
    assert venue._filled_before_entry == {"a": 50}
    assert venue.cancel_order("a") is True
    assert "a" not in venue._filled_before_entry


def test_q1_s6_venue_fill_record_is_pruned_to_resting_orders_and_stays_bounded():
    venue = _venue_with_partial_fill()
    assert venue.replace_order("a", new_qty=300, ts=1) is True      # "a" rests with a record
    for index in range(1100):                                        # ids that traded out
        venue._note_filled(f"gone-{index}", 1)
    assert "a" in venue._filled_before_entry                         # a resting order is kept
    assert len(venue._filled_before_entry) < 1100                    # the others are dropped
    assert all(key == "a" or key.startswith("gone-") for key in venue._filled_before_entry)
    assert len(venue._filled_before_entry) <= 1025


# ---- S7 (answer to review 1, F2): the runner never rests more than the total less the OMS fills
def test_q1_s7_runner_book_above_the_oms_does_not_inflate_the_replace():
    runner = _seeded_runner(200)
    runner.book_for("S").replace("a", new_qty=400)      # desync: the book holds more than the OMS
    runner._replace(ReplaceOrder(client_id="a", new_qty=300), Event(**_stamps()))
    assert runner.book_for("S").get("a").qty == 300     # what the guard judged, never 500


# ---- S8 (review thread at runner.py:392): a desync counts the OMS's fills as a floor -------
def test_q1_s8_runner_book_above_the_oms_counts_the_oms_fills():
    runner = _seeded_runner(200)
    _sell_into(runner, 50)                              # OMS: 50 filled; the book rests 150
    runner.book_for("S").replace("a", new_qty=400)      # desync: book 400 above the OMS total 200
    runner._replace(ReplaceOrder(client_id="a", new_qty=300), Event(**_stamps()))
    assert runner.book_for("S").get("a").qty == 250     # the guard's remaining, not 300
    _sell_into(runner, 250)
    assert runner.book_for("S").get("a") is None
    runner.feed = [_trade(2, 10)]
    runner.run()                                        # a later trade raises nothing
    assert runner.oms.get("a").filled_qty == 300


# ---- S9 (review thread at :96): a delayed resize that the venue refuses at arrival ---------
def test_q1_s9_refused_delayed_resize_clears_the_reservation_and_keeps_the_oms_terms():
    # Fills travel 10ns. Buy 10; the trade at exchange 2 fills 3 at the venue (the OMS learns at
    # 12); at 5 the strategy shrinks to a total of 3. The guard (0 filled seen) allows it; the
    # venue, with 3 filled, has nothing to rest and refuses at arrival.
    strategy = Scripted(on_book={
        1: [NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=10, price_ticks=100)],
        5: [ReplaceOrder(client_id="a", new_qty=3)],
    })
    loop = AutoLoop(venue=SimVenue(symbol="S", latency=LatencyModel(
                        order_entry_ns=0, market_data_ns=10, cancel_ns=0)),
                    strategies=[strategy], scale=SCALE,
                    guards=Guards(GuardLimits(max_position=10)))
    result = loop.run([_trade(1, 1, 101), _trade(2, 3), _trade(5, 1, 101), _trade(6, 1, 101),
                       _trade(20, 1, 101)])
    assert result.accounting_valid and not result.halted, result.halt_reason
    assert result.blocked_reasons.get("replace_missed") == 1     # the refusal is counted
    assert loop._awaiting_replacements == {}                     # the reservation is cleared
    order = loop.oms.get("a")
    assert (order.qty, order.filled_qty, order.remaining_qty) == (10, 3, 7)   # OMS terms kept
    assert loop.venue.book.get("a").qty == 7
