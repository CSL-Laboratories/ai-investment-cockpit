"""QT-02 — pairs / statistical arbitrage on a rolling-beta spread.

THE STRATEGY
------------
Fit ``y = alpha + beta*x`` over a rolling window of the two legs' fair values, and trade the
residual ``e = y - (alpha + beta*x)`` on the assumption it mean-reverts. Enter when the
z-score of ``e`` exceeds ``entry_z`` (short the rich leg, long the cheap one, hedged by
``beta``), exit as it decays through ``exit_z``, and stop out beyond ``stop_z``.

WHY THE HEDGE RATIO IS ROLLING AND NOT FITTED ONCE
----------------------------------------------------
A cointegrating relationship drifts. A beta fitted over all available history is fitted
mostly to a regime that has ended, and the resulting spread inherits a slow trend that reads
as a persistent, confident, and entirely spurious signal. ``RollingBeta`` therefore refits
over a bounded window — which costs responsiveness at genuine regime changes and is the
right trade, because the alternative failure is silent and this one is visible in the
position.

THE STOP EXISTS BECAUSE THE PREMISE CAN BE FALSE
--------------------------------------------------
``stop_z`` is not risk-management garnish. The strategy's entire premise is that a wide
spread will narrow; when a pair genuinely decouples — an acquisition, a guidance cut, an
index reconstitution — the spread widens without bound and the position scales INTO the
loss, because every further divergence looks like a better entry. Mean-reversion strategies
do not die of many small losses. Without a stop, the backtest shows a beautiful equity curve
with one terminal event, and the average is meaningless.

WHAT MAKES THIS ONE HARD IN THIS ENGINE
-----------------------------------------
Both legs must fill or the position is not hedged — it is naked directional risk in whichever
leg did fill. Under the exponential fill model that concern does not exist, because both legs
"fill" with a probability draw. Against a real book, one leg is liquid and the other is not,
and the unhedged interval is real. This implementation crosses on both legs to shorten that
interval, and accepts the taker cost as the price of actually being hedged; ``pnl.py``
attributes the resulting fees so the choice is visible rather than assumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from quant.events import Side
from quant.oms import Intent, NewOrder
from quant.signals import RollingBeta, RollingZScore
from quant.strategies.base import Strategy, StrategyContext


@dataclass(slots=True)
class PairsParams:
    beta_window: int = 200
    z_window: int = 200
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    """Beyond this the premise is treated as falsified and the position is flattened. See the
    module docstring — this is the parameter that decides whether the strategy survives a
    decoupling or is defined by one."""

    trade_size: int = 100
    max_position: int = 500


@dataclass(slots=True)
class Pairs(Strategy):
    """Trades the residual of leg_y on leg_x. ``symbols`` must name exactly the two legs."""

    params: PairsParams = field(default_factory=PairsParams)
    _beta: RollingBeta = field(init=False, repr=False, default=None)
    _z: RollingZScore = field(init=False, repr=False, default=None)
    _last_z: float | None = field(init=False, repr=False, default=None)
    _last_residual: float | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        Strategy.__post_init__(self)
        if len(self.symbols) != 2:
            raise ValueError(
                f"Pairs needs exactly two symbols (x, y), got {self.symbols!r}. The hedge "
                "ratio is meaningless with any other number, so this is refused rather than "
                "defaulted."
            )
        self._beta = RollingBeta(window=self.params.beta_window)
        self._z = RollingZScore(window=self.params.z_window)

    @property
    def leg_x(self) -> str:
        return self.symbols[0]

    @property
    def leg_y(self) -> str:
        return self.symbols[1]

    def _fair(self, ctx: StrategyContext, symbol: str) -> float | None:
        """Microprice where available, else mid. Never last-trade.

        Last-trade price is stale by construction and lags the book by however long it has
        been since someone crossed — on the quiet leg of a pair, that can be a long time, and
        a spread computed from one stale leg and one fresh one is mostly noise about which
        leg last printed.
        """
        book = ctx.books.get(symbol)
        if book is None:
            return None
        return book.microprice if book.microprice is not None else book.mid_ticks

    def zscore(self, ctx: StrategyContext) -> float | None:
        """Current spread z-score, or None while cold or degenerate."""
        return self._last_z

    def on_book_update(self, ctx: StrategyContext) -> list[Intent]:
        x = self._fair(ctx, self.leg_x)
        y = self._fair(ctx, self.leg_y)
        if x is None or y is None:
            return []

        # MEASURE FIRST, THEN LEARN. The residual is computed against the relationship as it
        # stood BEFORE this observation, and only then is the observation folded in.
        #
        # The other order — update, then measure — is the obvious way to write this and it
        # quietly destroys the signal. The current point is included in its own fit, so a
        # genuine dislocation partially explains itself away: the wider the divergence, the
        # more it drags the hedge ratio toward itself and the smaller the residual it
        # produces. The effect is strongest exactly when x has little variance, which is the
        # normal state of two closely-related names, so the strategy would be most blind
        # precisely on the pairs it is best suited to.
        # Capture the hedge ratio alongside the residual, BEFORE folding this observation in.
        # The signal and the hedge must come from one model state: sizing the hedge with a
        # beta that already contains the dislocation, while measuring the dislocation against
        # the beta that did not, is a quiet inconsistency that grows with the size of the move.
        beta_at_signal = self._beta.beta
        residual = self._beta.residual(x, y)
        self._beta.update(x, y)
        if residual is None:
            return []
        self._last_residual = residual
        self._last_z = self._z.update(residual)
        z = self._last_z
        if z is None:
            return []

        position_y = ctx.position(self.leg_y)
        p = self.params

        # One set of legs in flight at a time — a second entry on an unchanged signal turns
        # one intended spread position into two, unhedged against each other in the interval.
        if ctx.orders_for(self.leg_x) or ctx.orders_for(self.leg_y):
            return []

        if abs(z) >= p.stop_z and position_y != 0:
            return self._flatten(ctx, position_y)
        if abs(z) <= p.exit_z and position_y != 0:
            return self._flatten(ctx, position_y)
        if abs(z) < p.entry_z:
            return []

        # Rich spread (z > 0) means y is expensive relative to x: short y, long x.
        side_y = Side.SELL if z > 0 else Side.BUY
        headroom = (
            p.max_position - position_y if side_y is Side.BUY else p.max_position + position_y
        )
        qty_y = min(p.trade_size, max(0, headroom))
        if qty_y <= 0:
            return []

        beta = beta_at_signal
        if beta is None:
            return []
        qty_x = int(round(abs(beta) * qty_y))
        if qty_x <= 0:
            # A hedge ratio that rounds to zero shares is not a hedge. Taking the y leg alone
            # would be naked directional risk wearing a pairs label.
            return []
        side_x = side_y.opposite if beta > 0 else side_y

        return [
            *self._cross(ctx, self.leg_x, side_x, qty_x),
            *self._cross(ctx, self.leg_y, side_y, qty_y),
        ]

    def _flatten(self, ctx: StrategyContext, position_y: int) -> list[Intent]:
        intents: list[Intent] = []
        for symbol in (self.leg_x, self.leg_y):
            position = ctx.position(symbol)
            if position == 0:
                continue
            side = Side.SELL if position > 0 else Side.BUY
            intents += self._cross(ctx, symbol, side, abs(position))
        return intents

    def _cross(self, ctx: StrategyContext, symbol: str, side: Side, qty: int) -> list[Intent]:
        """Marketable limit at the far touch. Capped so a thin leg cannot fill arbitrarily
        deep — the failure mode a pairs strategy is most exposed to, since the illiquid leg
        is exactly the one whose book cannot absorb the hedge."""
        book = ctx.books.get(symbol)
        if book is None:
            return []
        price = book.best_ask_ticks if side is Side.BUY else book.best_bid_ticks
        if price is None:
            return []
        return [NewOrder(client_id=self.next_client_id(), symbol=symbol, side=side,
                         qty=qty, price_ticks=price, tif="ioc")]

    def describe(self) -> dict:
        return {
            "strategy": "pairs",
            "name": self.name,
            "params": {
                "legs": [self.leg_x, self.leg_y],
                "entry_z": self.params.entry_z,
                "exit_z": self.params.exit_z,
                "stop_z": self.params.stop_z,
                "trade_size": self.params.trade_size,
            },
            "state": {
                "beta": self._beta.beta,
                "residual": self._last_residual,
                "zscore": self._last_z,
                "warm": self._z.is_warm and self._beta.is_warm,
            },
        }
