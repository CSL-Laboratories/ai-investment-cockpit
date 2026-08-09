"""QT-02 — order-flow-imbalance momentum. The taker, to the market maker's maker.

THE SIGNAL
----------
Two inputs, both microstructural, neither a price-history indicator:

  * ORDER FLOW IMBALANCE — net signed traded volume over a rolling window of prints, signed
    by the AGGRESSOR. A print at the ask means a buyer paid the spread to get filled now.
    That is information about intent, in a way the price alone is not: the same price can be
    reached by a buyer lifting or a seller being hit, and those imply opposite continuations.
  * MICROPRICE DEVIATION — how far the size-weighted fair value sits from the arithmetic mid,
    in ticks. A heavy bid against a thin ask says the next print is likelier at the ask.

They are combined as a weighted sum rather than a filter chain because they disagree often
and usefully: OFI is what has ALREADY traded, the microprice is what is RESTING. Requiring
both to agree produces a signal that is right and late.

WHY THIS STRATEGY EXISTS ALONGSIDE THE MARKET MAKER
-----------------------------------------------------
It pays the spread on both legs and earns nothing passively, so under the realistic cost
model in ``costs.py`` it is the strategy most likely to be marginal — which makes it the
useful one to keep. A book of strategies where everything is comfortably profitable in
backtest is a book that has not modelled costs. The maker and the taker also sit on opposite
sides of the same fee schedule, so a cost assumption that flatters one penalises the other
and cannot quietly flatter both.

CROSSING IS DELIBERATE
-----------------------
This strategy sends marketable limit orders, accepts taker fees, and never rests. A momentum
signal that waits passively for a fill gets filled exactly when it is wrong — the queue only
comes to you when the market is going the other way. That adverse selection is not an
implementation detail to be optimised away; it is the reason momentum takes and making
quotes is a different business.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from quant.events import Side, Trade
from quant.oms import Intent, NewOrder
from quant.signals import OrderFlowImbalance
from quant.strategies.base import Strategy, StrategyContext


@dataclass(slots=True)
class MomentumParams:
    ofi_window: int = 50
    ofi_weight: float = 1.0
    microprice_weight: float = 0.5
    """Weight on microprice deviation, expressed per tick of deviation. Lower than the OFI
    weight by default because resting size is cheap to display and frequently spoofed,
    whereas a print is a commitment someone actually paid for."""

    entry_threshold: float = 0.35
    exit_threshold: float = 0.10
    """Deliberately asymmetric. One threshold makes the strategy flip in and out around the
    boundary, paying the spread on every oscillation — a cost that dwarfs the signal and is
    invisible without a real fee model."""

    trade_size: int = 100
    max_position: int = 500
    min_warm_prints: int = 20


@dataclass(slots=True)
class Momentum(Strategy):
    """Crosses the spread when signed flow and resting imbalance agree strongly enough."""

    params: MomentumParams = field(default_factory=MomentumParams)
    _ofi: OrderFlowImbalance = field(init=False, repr=False, default=None)
    _prints: int = field(init=False, repr=False, default=0)
    _last_signal: float | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        Strategy.__post_init__(self)
        self._ofi = OrderFlowImbalance(window=self.params.ofi_window)

    def signal(self, ctx: StrategyContext) -> float | None:
        """Combined score. Positive means buy pressure. None while cold.

        None rather than 0.0 while warming up, so "no opinion" and "a confident opinion of
        neutral" are distinguishable. A strategy that cannot tell those apart will trade its
        own initialisation transient.
        """
        book = ctx.book
        if book is None or self._prints < self.params.min_warm_prints:
            return None
        micro, mid = book.microprice, book.mid_ticks
        if micro is None or mid is None:
            return None
        p = self.params
        score = p.ofi_weight * self._ofi.value + p.microprice_weight * (micro - mid)
        self._last_signal = score
        return score

    def on_trade(self, trade: Trade, ctx: StrategyContext) -> list[Intent]:
        """Signed by the aggressor — the side that removed liquidity. ``Trade.aggressor`` is
        named that way precisely so this cannot be wired backwards; a sign error here yields
        a signal that is exactly wrong rather than merely weak."""
        self._ofi.update(signed_qty=trade.aggressor.sign * trade.qty)
        self._prints += 1
        return []

    def on_book_update(self, ctx: StrategyContext) -> list[Intent]:
        book = ctx.book
        score = self.signal(ctx)
        if book is None or score is None:
            return []

        # One order in flight at a time. Stacking marketable orders on a signal that has not
        # changed is how a taker turns one intended position into three.
        if ctx.orders_for(book.symbol):
            return []

        position = ctx.position(book.symbol)
        p = self.params

        if abs(score) < p.exit_threshold and position != 0:
            return self._flatten(book, position)
        if abs(score) < p.entry_threshold:
            return []

        side = Side.BUY if score > 0 else Side.SELL
        headroom = p.max_position - position if side is Side.BUY else p.max_position + position
        qty = min(p.trade_size, max(0, headroom))
        if qty <= 0:
            return []

        # Marketable LIMIT, not market: capped at the far touch so a thin book cannot fill us
        # arbitrarily deep. A market order in a book with one share at the touch is how a
        # backtest discovers slippage it never modelled.
        price = book.best_ask_ticks if side is Side.BUY else book.best_bid_ticks
        if price is None:
            return []
        return [NewOrder(client_id=self.next_client_id(), symbol=book.symbol,
                         side=side, qty=qty, price_ticks=price, tif="ioc")]

    def _flatten(self, book, position: int) -> list[Intent]:
        side = Side.SELL if position > 0 else Side.BUY
        price = book.best_bid_ticks if position > 0 else book.best_ask_ticks
        if price is None:
            return []
        return [NewOrder(client_id=self.next_client_id(), symbol=book.symbol,
                         side=side, qty=abs(position), price_ticks=price, tif="ioc")]

    def describe(self) -> dict:
        return {
            "strategy": "momentum",
            "name": self.name,
            "params": {
                "ofi_window": self.params.ofi_window,
                "entry_threshold": self.params.entry_threshold,
                "exit_threshold": self.params.exit_threshold,
                "trade_size": self.params.trade_size,
                "max_position": self.params.max_position,
            },
            "state": {
                "ofi": self._ofi.value,
                "prints_seen": self._prints,
                "signal": self._last_signal,
                "warm": self._prints >= self.params.min_warm_prints,
            },
        }
