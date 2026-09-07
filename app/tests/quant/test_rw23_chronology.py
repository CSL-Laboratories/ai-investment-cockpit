"""BUG-155/156: exchange mutations and observer notifications have separate clocks."""
from __future__ import annotations

from decimal import Decimal
from dataclasses import replace

import pytest

from quant.events import BookAction, BookUpdate, Event, PriceScale, Side, Trade
from quant.execution.autoloop import AutoLoop
from quant.execution.sim_venue import LatencyModel, SimVenue
from quant.guards import GuardLimits, Guards
from quant.oms import CancelOrder, NewOrder, ReplaceOrder
from quant.strategies.base import Strategy


class ScriptedStrategy(Strategy):
    def __init__(self, orders):
        super().__init__(name="script", symbols=("S",))
        self.orders = orders
        self.observations = []

    def on_book_update(self, ctx):
        self.observations.append(("book", ctx.ts, ctx.position("S")))
        return self.orders.pop(ctx.ts, [])

    def on_fill(self, fill, ctx):
        self.observations.append(("fill", ctx.ts, ctx.position("S")))
        return []

    def describe(self):
        return {"name": self.name}


def _order(qty=1):
    return NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=qty, price_ticks=100)


def _event(exchange, ingest=None, *, seq=None):
    ingest = exchange if ingest is None else ingest
    return Event(symbol="S", seq=exchange if seq is None else seq,
                 exchange_ts=exchange, recv_ts=ingest, ingest_ts=ingest)


def _ask(action, exchange, ingest, *, seq):
    return BookUpdate(symbol="S", seq=seq, exchange_ts=exchange, recv_ts=ingest,
                      ingest_ts=ingest, action=action, side=Side.SELL,
                      price_ticks=100, qty=1, order_ref=1)


def _trade(exchange, ingest=None, qty=1):
    ingest = exchange if ingest is None else ingest
    return Trade(symbol="S", seq=exchange, exchange_ts=exchange, recv_ts=ingest,
                 ingest_ts=ingest, aggressor=Side.SELL, price_ticks=100, qty=qty)


def _loop(strategy, *, entry=0, data=0, cancel=0):
    audit = []
    loop = AutoLoop(
        venue=SimVenue("S", LatencyModel(order_entry_ns=entry,
                                        market_data_ns=data, cancel_ns=cancel)),
        strategies=[strategy], scale=PriceScale(Decimal("0.01")),
        audit_sink=lambda event, payload: audit.append((event, payload)),
    )
    return loop, audit


@pytest.mark.parametrize("cancel_exchange, expected_fills", [(120, 0), (180, 1)])
def test_exchange_cancel_wins_only_when_it_precedes_order_arrival(cancel_exchange, expected_fills):
    strategy = ScriptedStrategy({100: [_order()]})
    loop, _audit = _loop(strategy, entry=50)
    result = loop.run([
        _ask(BookAction.ADD, 1, 100, seq=1),
        _ask(BookAction.CANCEL, cancel_exchange, 200, seq=2),
        _event(220, 250),
    ])

    assert result.accounting_valid and not result.halted
    assert result.final_position == expected_fills
    assert len(result.fills) == expected_fills
    assert result.desync_cancels == expected_fills  # genuine counterfactual consumption only
    if expected_fills:
        assert result.fills[0].exchange_ts == 150


def test_execution_report_is_delivered_only_after_receive_time():
    strategy = ScriptedStrategy({1: [_order()]})
    loop, _audit = _loop(strategy, data=100)
    result = loop.run([
        _ask(BookAction.ADD, 1, 1, seq=1), _event(2), _event(50), _event(101),
    ])

    assert strategy.observations == [
        ("book", 1, 0), ("book", 2, 0), ("book", 50, 0),
        ("fill", 101, 1), ("book", 101, 1),
    ]
    assert result.fills[0].exchange_ts == 1
    assert result.fills[0].recv_ts == result.fills[0].ingest_ts == 101
    assert result.accounting_valid and result.pending_reports == 0


def test_a_late_report_uses_actual_processing_time_not_a_past_callback_time():
    strategy = ScriptedStrategy({1: [_order()]})
    loop, _audit = _loop(strategy, data=100)
    result = loop.run([_ask(BookAction.ADD, 1, 1, seq=1), _event(2), _event(80), _event(200)])
    assert strategy.observations[-2:] == [("fill", 200, 1), ("book", 200, 1)]
    assert result.fills[0].recv_ts == 101
    assert result.fills[0].ingest_ts == 200


def test_end_of_feed_retains_undelivered_fill_and_does_not_invent_a_clock_tick():
    strategy = ScriptedStrategy({1: [_order()]})
    loop, audit = _loop(strategy, data=100)
    result = loop.run([_ask(BookAction.ADD, 1, 1, seq=1), _event(2)])
    assert result.accounting_valid and result.final_position == 0
    assert result.fills == [] and result.attribution is None
    assert result.pending_reports == 1 and result.stranded_orders == 0
    assert len(result.undelivered_fills) == 1
    assert result.undelivered_fills[0].recv_ts == 101
    assert audit[-1][1]["pending_reports"] == 1
    assert audit[-1][1]["undelivered_fills"][0]["qty"] == 1

    # A later real input tick delivers the packet exactly once.
    result = loop.run([_event(101)])
    assert result.final_position == 1 and len(result.fills) == 1
    assert result.pending_reports == 0 and result.undelivered_fills == []
    assert result.attribution is not None


def test_market_fill_report_precedes_the_later_cancel_confirmation():
    strategy = ScriptedStrategy({1: [_order(qty=10)], 3: [CancelOrder(client_id="a")]})
    loop, _audit = _loop(strategy, data=100)
    result = loop.run([_event(1), _trade(2, qty=3), _event(3), _event(4),
                       _event(102), _event(103)])
    assert ("book", 3, 0) in strategy.observations
    assert ("fill", 102, 3) in strategy.observations
    assert loop.oms.get("a").state.value == "cancelled"
    assert result.accounting_valid and result.final_position == 3
    assert result.desync_fills == 0 and result.pending_reports == 0


def test_market_fill_retains_distinct_capture_and_processing_timestamps():
    strategy = ScriptedStrategy({1: [_order()]})
    loop, _audit = _loop(strategy)
    trade = replace(_trade(2, 30), recv_ts=20)
    result = loop.run([_event(1), trade])
    assert result.fills[0].exchange_ts == 2
    assert result.fills[0].recv_ts == 20
    assert result.fills[0].ingest_ts == 30


@pytest.mark.parametrize("later_intent", [
    CancelOrder(client_id="a"),
    ReplaceOrder(client_id="a", new_price_ticks=90),
])
def test_observer_cancel_or_replace_cannot_rewrite_an_earlier_exchange_trade(later_intent):
    strategy = ScriptedStrategy({100: [_order(qty=5)], 200: [later_intent]})
    loop, _audit = _loop(strategy)
    result = loop.run([_event(1, 100), _event(110, 200), _trade(150, 250, qty=5),
                       _event(300, 350)])
    assert result.accounting_valid and result.final_position == 5
    assert len(result.fills) == 1 and result.fills[0].exchange_ts == 150


def test_unacknowledged_growth_cannot_reuse_the_same_position_budget():
    second = replace(_order(qty=9), client_id="b")
    strategy = ScriptedStrategy({
        1: [_order()],
        2: [ReplaceOrder(client_id="a", new_qty=10), second],
        4: [replace(second, client_id="c")],
    })
    loop, _audit = _loop(strategy, data=100)
    loop.guards = Guards(limits=GuardLimits(max_position=10))
    result = loop.run([_event(1), _event(2), _event(3), _event(4), _event(102)])
    assert result.accounting_valid
    assert loop.oms.get("a").qty == 10
    assert loop.oms.get("b") is None and loop.oms.get("c") is None
    assert loop.venue.book.get("a").qty == 10
    assert sum(result.blocked_reasons.values()) == 2


def test_smaller_replacement_releases_exposure_only_when_acknowledged():
    strategy = ScriptedStrategy({
        1: [_order()], 2: [ReplaceOrder(client_id="a", new_price_ticks=90)],
        3: [replace(_order(), client_id="early", price_ticks=10)],
        12: [replace(_order(), client_id="after-ack", price_ticks=10)],
    })
    loop, _audit = _loop(strategy, data=10)
    loop.guards = Guards(limits=GuardLimits(max_gross_exposure_ticks=100))
    result = loop.run([_event(1), _event(2), _event(3), _event(12), _event(13)])
    assert result.accounting_valid
    assert loop.oms.get("early") is None
    assert loop.oms.get("after-ack") is not None
    assert loop.venue.book.get("after-ack") is not None
    assert sum(result.blocked_reasons.values()) == 1


def test_successive_replacements_keep_reservations_until_their_own_reports():
    strategy = ScriptedStrategy({
        1: [_order()],
        2: [ReplaceOrder(client_id="a", new_qty=10),
            ReplaceOrder(client_id="a", new_qty=2),
            replace(_order(qty=9), client_id="early")],
        12: [replace(_order(qty=8), client_id="after-both-acks")],
    })
    loop, _audit = _loop(strategy, data=10)
    loop.guards = Guards(limits=GuardLimits(max_position=10))
    result = loop.run([_event(1), _event(2), _event(3), _event(12), _event(13)])
    assert result.accounting_valid and loop.oms.get("early") is None
    assert loop.oms.get("a").qty == loop.venue.book.get("a").qty == 2
    assert loop.oms.get("after-both-acks").qty == 8
    assert loop.venue.book.get("after-both-acks").qty == 8


@pytest.mark.parametrize("bad", [
    _event(9, 20), _event(20, 9), _event(30, 20), replace(_event(20), recv_ts=30),
])
def test_ambiguous_event_clock_order_halts_without_claiming_valid_pnl(bad):
    strategy = ScriptedStrategy({})
    loop, audit = _loop(strategy)
    result = loop.run([_event(10), bad, _event(40)])
    assert result.halted and not result.accounting_valid
    assert result.attribution is None
    assert "chronology" in result.halt_reason
    assert strategy.observations == [("book", 10, 0)]
    assert audit[-1][1]["accounting_valid"] is False
