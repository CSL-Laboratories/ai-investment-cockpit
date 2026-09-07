"""RW-22: full venue sequences must preserve fill evidence and refuse invalid accounting."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from quant.events import PriceScale, Side, Trade
from quant.execution.autoloop import AutoLoop, EVENT_QUANT_SIM_BREACH, EVENT_QUANT_SIM_SESSION
from quant.execution.sim_venue import LatencyModel, SimVenue
from quant.oms import CancelOrder, NewOrder, ReplaceOrder
from quant.strategies.base import Strategy


class RecordingStrategy(Strategy):
    def __init__(self, name="resize"):
        super().__init__(name=name, symbols=("S",))
        self.callbacks = []

    def on_book_update(self, ctx):
        self.callbacks.append(("book", ctx.ts))
        return []

    def on_fill(self, fill, ctx):
        self.callbacks.append(("fill", ctx.ts))
        return []

    def on_trade(self, trade, ctx):
        self.callbacks.append(("trade", ctx.ts))
        return []

    def on_timer(self, ts, ctx):
        self.callbacks.append(("timer", ts))
        return []

    def describe(self):
        return {"name": self.name}


class Resizer(RecordingStrategy):
    def on_book_update(self, ctx):
        super().on_book_update(ctx)
        if ctx.ts == 1:
            return [NewOrder(client_id="a", symbol="S", side=Side.BUY,
                             qty=10, price_ticks=100)]
        if ctx.ts == 2:
            return [ReplaceOrder(client_id="a", new_qty=10)]
        return []


class Seller(RecordingStrategy):
    def on_book_update(self, ctx):
        super().on_book_update(ctx)
        if ctx.ts == 2:
            return [NewOrder(client_id=cid, symbol="S", side=Side.SELL,
                             qty=qty, price_ticks=price)
                    for cid, qty, price in [("seller1", 10, 100), ("seller2", 1, 99)]]
        return []


class Canceller(RecordingStrategy):
    def on_book_update(self, ctx):
        super().on_book_update(ctx)
        if ctx.ts == 1:
            return [NewOrder(client_id="a", symbol="S", side=Side.BUY,
                             qty=10, price_ticks=100)]
        if ctx.ts == 2:
            return [CancelOrder(client_id="a")]
        return []


def _trade(ts, qty=1, price=101):
    return Trade(symbol="S", seq=ts, price_ticks=price, qty=qty,
                 aggressor=Side.SELL, exchange_ts=ts, recv_ts=ts, ingest_ts=ts)


def _loop(strategies, *, latency=None):
    audit = []

    def append(event_type, payload):
        # AuditLog's canonical encoder does not accept enums or dataclasses either.
        audit.append((event_type, json.loads(json.dumps(payload))))

    loop = AutoLoop(
        venue=SimVenue(symbol="S", latency=latency or LatencyModel.instant()),
        strategies=strategies, scale=PriceScale(tick_size=Decimal("0.01")),
        timer_interval_ns=1, audit_sink=append,
    )
    return loop, audit


def test_real_partial_resize_overfill_halts_without_more_callbacks_or_pnl():
    """The pre-existing total/remaining replace mismatch must yield an invalid run.

    No illegal fill is injected: the venue itself produces the overfill from ordinary
    strategy intents and three market Trade events. Repairing resize semantics is separate.
    """
    strategy = Resizer()
    loop, audit = _loop([strategy])
    consumed = []

    def feed():
        for event in [_trade(1), _trade(2, 3, 100), _trade(3, 10, 100), _trade(4)]:
            consumed.append(event.seq)
            yield event

    result = loop.run(feed())
    assert consumed == [1, 2, 3]
    assert result.halted and not result.accounting_valid
    assert "fill of 10 exceeds 7 remaining" in result.halt_reason
    assert result.attribution is None
    assert result.final_position == 3  # explicitly only the reconciled prefix
    assert [fill.qty for fill in result.fills] == [3]
    assert [fill.qty for fill in result.unreconciled_fills] == [10]
    assert result.desync_fills == 1
    assert all(ts < 3 for _, ts in strategy.callbacks)
    assert any(kind == "timer" for kind, _ in strategy.callbacks)

    breach = next(payload for event, payload in audit if event == EVENT_QUANT_SIM_BREACH)
    assert breach["fill"]["order_id"] == "a" and breach["fill"]["qty"] == 10
    event, end = audit[-1]
    assert event == EVENT_QUANT_SIM_SESSION and end["phase"] == "end"
    assert end["halted"] and not end["accounting_valid"]
    assert end["desync_fills"] == 1 and end["attribution"] is None
    assert end["unreconciled_fills"] == [breach["fill"]]

    # Resetting an external kill switch cannot repair this session's missing accounting.
    loop.guards.reset()
    loop.run([_trade(4)])
    assert result.events == 3 and not result.accounting_valid
    assert all(ts < 3 for _, ts in strategy.callbacks)


def test_advance_overfill_preserves_later_batch_fills_without_routing_callbacks():
    resizer, seller = Resizer(), Seller("seller")
    loop, audit = _loop([resizer, seller])
    loop.venue.book.add(order_id="external", side=Side.BUY, price_ticks=99, qty=1)
    result = loop.run([_trade(1), _trade(2, 3, 100), _trade(3), _trade(4)])

    # advance() has already matched seller1 and seller2 when its first maker fill
    # fails reconciliation. Both the rest of that outcome and the later outcome survive.
    assert result.halted and not result.accounting_valid
    assert result.events == 3 and result.attribution is None
    assert [fill.order_id for fill in result.unreconciled_fills] == ["a", "seller1", "seller2"]
    assert [fill.qty for fill in result.unreconciled_fills] == [10, 10, 1]
    assert [fill.qty for fill in result.fills] == [3]
    assert loop.oms.get("seller2").state.value == "pending_new"
    assert all(ts < 3 for strategy in [resizer, seller] for _, ts in strategy.callbacks)
    assert [fill["order_id"] for fill in audit[-1][1]["unreconciled_fills"]] == [
        "a", "seller1", "seller2",
    ]


@pytest.mark.parametrize("fill_qty, final_state, misses", [(3, "cancelled", 0), (10, "filled", 1)])
def test_traveling_cancel_keeps_legitimate_partial_and_full_fills(fill_qty, final_state, misses):
    strategy = Canceller()
    loop, audit = _loop(
        [strategy], latency=LatencyModel(order_entry_ns=0, market_data_ns=0, cancel_ns=10),
    )
    result = loop.run([_trade(1), _trade(2), _trade(3, fill_qty, 100), _trade(12)])

    assert result.events == 4 and not result.halted
    assert result.accounting_valid and result.desync_fills == 0
    assert result.unreconciled_fills == [] and result.attribution is not None
    assert [fill.qty for fill in result.fills] == [fill_qty]
    assert result.final_position == fill_qty
    assert loop.oms.get("a").state.value == final_state
    assert loop.venue.cancel_misses == misses
    assert ("fill", 3) in strategy.callbacks
    assert audit[-1][1]["accounting_valid"] is True
    assert not any(event == EVENT_QUANT_SIM_BREACH for event, _ in audit)
