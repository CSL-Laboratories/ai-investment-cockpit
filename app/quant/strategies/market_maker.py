"""QT-02 — Avellaneda-Stoikov market making, quoting into a real FIFO queue.

THE MODEL
---------
Avellaneda & Stoikov (2008) solve for a market maker's optimal quotes under inventory risk.
Two quantities do the work, both in PRICE units:

    reservation price   r = s - q * gamma * sigma^2 * (T - t)
    optimal half-spread d = (gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/kappa)) / 2

    quotes:  bid = r - d,  ask = r + d

``r`` is the price at which the maker is INDIFFERENT to holding its current inventory. It is
not the mid: long inventory pushes it below the mid, so both quotes shift down and the ask
becomes more attractive — the model sheds risk by repricing rather than by crossing the
spread. That inventory skew is the entire point, and it is why a market maker with no
inventory term eventually accumulates a position that dwarfs its spread earnings.

``d`` has two terms with genuinely different jobs. The first is compensation for inventory
risk over the remaining horizon and vanishes as ``T -> t``. The second, ``(2/gamma)ln(1 +
gamma/kappa)``, is the monopolistic-competition term: it is what the maker charges for
providing immediacy, and it does NOT vanish. ``kappa`` is the order-arrival intensity decay —
higher kappa means fills fall off faster as you quote wider, so the optimal spread narrows.

UNITS ARE WHERE THIS GOES WRONG, SO THEY ARE STATED
-----------------------------------------------------
``sigma`` in the formulae is volatility in PRICE units over the horizon. ``EwmaVolatility``
produces LOG-RETURN volatility per observation. Those are not the same thing and plugging one
in for the other silently rescales the whole model by the price level — the quotes stay
plausible, the inventory skew is off by a factor of ~mid, and nothing raises. The conversion
is explicit in ``_sigma_ticks``: ``sigma_price = sigma_return * mid``.

``T - t`` is dimensionless here: the fraction of the trading horizon remaining, in [0, 1].
That makes ``gamma`` interpretable as risk aversion per unit of horizon-variance and keeps
the two spread terms commensurable.

WHAT THE REAL QUEUE CHANGES — READ THIS BEFORE TUNING
-------------------------------------------------------
Under the usual ``exp(-k*delta)`` fill model, fill probability depends only on distance from
the mid, so recomputing quotes on every tick and replacing whenever the number moves is FREE.
Against a real book it is not free: ``lob.replace`` is cancel-and-new and goes to the BACK of
the queue. A naive A-S loop that requotes on every book update therefore:

  * never accumulates queue priority, so it fills almost exclusively when the level is swept
    — that is, precisely when the price is about to move through it;
  * converts what looks like spread capture into systematic adverse selection;
  * and shows none of this under an exponential fill model, which is why the behaviour is
    invisible in most toy backtests.

``requote_threshold_ticks`` is the response: hold the existing quote unless the desired price
has moved by at least that many ticks. Queue priority is an ASSET that accrues while you
wait, and this parameter is the price you put on it. It defaults to 1 rather than 0 for that
reason, and setting it to 0 is the single fastest way to make this strategy lose money —
which is a finding the engine can now actually produce.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from quant.oms import CancelOrder, Intent, NewOrder
from quant.signals import EwmaVolatility
from quant.strategies.base import Strategy, StrategyContext


@dataclass(slots=True)
class MarketMakerParams:
    """Every knob, with the units it is in. Swept in ``notebooks/``, not hardcoded below."""

    gamma: float = 0.1
    """Risk aversion. Higher = wider quotes and a stronger inventory skew."""

    kappa: float = 1.5
    """Order-arrival intensity decay. Higher = fills fall off faster with distance, so the
    optimal spread NARROWS. Estimated from the fill/distance relationship, not guessed."""

    horizon_ns: int = 60 * 1_000_000_000
    """The 'T' in (T - t), in event-time nanoseconds. The inventory term decays to zero
    across this window, so it sets how urgently the maker unwinds toward the end."""

    quote_size: int = 100
    max_inventory: int = 1_000
    """Hard inventory bound. At the limit the maker quotes ONE side only — it stops adding
    to the position rather than merely skewing against it. A skew alone is a soft control
    and a trending market walks straight through it."""

    requote_threshold_ticks: int = 1
    """Hold an existing quote unless the target moved this far. See the module docstring:
    this is the price placed on queue priority, and 0 is a materially different strategy."""

    min_spread_ticks: int = 1
    max_spread_ticks: int = 50
    """Sanity clamps. An unwarmed or degenerate volatility estimate can produce a half-spread
    of thousands of ticks; quoting it would be harmless in simulation and catastrophic in the
    verifier, which reads the same parameters."""

    vol_lambda: float = 0.94

    vol_sample_ns: int = 1_000_000
    """Sample the mid for the volatility estimate on a TIME grid, not per book update.

    Per-message sampling is the obvious implementation and it is wrong, in a way that is
    silent and total. Between two consecutive messages the mid usually has not moved at all,
    so most observations are a log return of exactly zero and the EWMA variance decays toward
    it. On a liquid name with a one-tick spread the estimate collapses to ~1e-14 within a few
    thousand messages — at which point ``gamma * sigma^2 * (T-t)`` is zero, the inventory
    skew silently switches OFF, and the maker accumulates position with no risk response
    while still producing perfectly plausible-looking quotes.

    It is also wrong in principle: per-message sampling makes the estimate a function of
    MESSAGE RATE rather than of the market. A chatty, flat name and a quiet, violent one
    would be assigned similar volatilities, which inverts the ranking that matters.
    """


@dataclass(slots=True)
class MarketMaker(Strategy):
    """Two-sided quoting with inventory skew, against a real queue.

    Holds one live quote per side and reprices only when the target moves past the threshold.
    Everything it needs to decide is in ``ctx``; the estimator state is its own.
    """

    params: MarketMakerParams = field(default_factory=MarketMakerParams)
    _vol: EwmaVolatility = field(init=False, repr=False, default=None)
    _start_ts: int | None = field(init=False, repr=False, default=None)
    _bid_id: str | None = field(init=False, repr=False, default=None)
    _ask_id: str | None = field(init=False, repr=False, default=None)
    _bid_ticks: int | None = field(init=False, repr=False, default=None)
    _ask_ticks: int | None = field(init=False, repr=False, default=None)
    _last_reservation: float | None = field(init=False, repr=False, default=None)
    _last_half_spread: float | None = field(init=False, repr=False, default=None)
    _next_vol_sample: int = field(init=False, repr=False, default=0)

    def __post_init__(self) -> None:
        Strategy.__post_init__(self)
        self._vol = EwmaVolatility(lam=self.params.vol_lambda)

    # ---- the model -------------------------------------------------------------------

    def _time_remaining(self, ts: int) -> float:
        """(T - t) as a fraction in [0, 1]. Clamped at both ends.

        Clamped rather than allowed to go negative: past the horizon the inventory term would
        flip sign and the maker would start skewing INTO its position, which is the opposite
        of unwinding and is a genuinely dangerous failure rather than a merely wrong one.
        """
        if self._start_ts is None or self.params.horizon_ns <= 0:
            return 1.0
        elapsed = ts - self._start_ts
        return max(0.0, min(1.0, 1.0 - elapsed / self.params.horizon_ns))

    def _sample_volatility(self, ctx: StrategyContext) -> None:
        """Feed the estimator at most once per ``vol_sample_ns`` of event time.

        The grid is advanced by whole intervals rather than reset to ``ctx.ts``, so a burst
        of messages after a quiet gap does not produce a cluster of closely-spaced samples —
        which would reintroduce the message-rate dependence the grid exists to remove.
        """
        book = ctx.book
        if book is None:
            return
        mid = book.mid_ticks
        if mid is None:
            return
        interval = max(1, self.params.vol_sample_ns)
        if self._next_vol_sample == 0:
            self._next_vol_sample = ctx.ts + interval
            self._vol.update(mid)
            return
        if ctx.ts < self._next_vol_sample:
            return
        while self._next_vol_sample <= ctx.ts:
            self._next_vol_sample += interval
        self._vol.update(mid)

    def _sigma_ticks(self, mid_ticks: float) -> float | None:
        """Log-return volatility -> price volatility in TICKS. The unit conversion that,
        omitted, rescales the entire model by the price level without raising anything."""
        sigma_return = self._vol.value
        if sigma_return is None:
            return None
        return sigma_return * mid_ticks

    def quotes(self, ctx: StrategyContext) -> tuple[int, int] | None:
        """Compute the target (bid, ask) in ticks, or None if the model cannot speak yet.

        Public because the Tier-A verifier and the tests both want the model's opinion
        without the requoting machinery around it.
        """
        book = ctx.book
        if book is None:
            return None
        mid = book.microprice if book.microprice is not None else book.mid_ticks
        if mid is None:
            return None

        sigma = self._sigma_ticks(mid)
        if sigma is None:
            # COLD: refuse to quote rather than substitute a zero. A zero volatility estimate
            # produces an infinitely confident quote at exactly the moment the model knows
            # least, which is the worst possible time to be maximally aggressive.
            return None

        p = self.params
        tau = self._time_remaining(ctx.ts)
        inventory = ctx.position(book.symbol)
        variance_term = p.gamma * sigma * sigma * tau

        reservation = mid - inventory * variance_term
        half_spread = 0.5 * (variance_term + (2.0 / p.gamma) * math.log1p(p.gamma / p.kappa))
        half_spread = max(p.min_spread_ticks / 2.0, min(p.max_spread_ticks / 2.0, half_spread))

        self._last_reservation = reservation
        self._last_half_spread = half_spread

        # Round OUTWARD on both sides. Rounding to nearest would sometimes place a quote
        # inside the model's own spread, which is a systematic giveaway of exactly the edge
        # the spread was computed to capture.
        bid = math.floor(reservation - half_spread)
        ask = math.ceil(reservation + half_spread)
        if ask <= bid:
            ask = bid + max(1, p.min_spread_ticks)
        return bid, ask

    # ---- callbacks ---------------------------------------------------------------------

    def on_book_update(self, ctx: StrategyContext) -> list[Intent]:
        book = ctx.book
        if book is None:
            return []
        if self._start_ts is None:
            self._start_ts = ctx.ts

        self._sample_volatility(ctx)
        target = self.quotes(ctx)
        if target is None:
            return []
        return self._reconcile(ctx, *target)

    def on_fill(self, fill, ctx: StrategyContext) -> list[Intent]:
        """A fill retires the quote it filled, then lets the next book update requote.

        Requoting from inside ``on_fill`` would use the book state as it was BEFORE the trade
        that just happened is reflected — quoting against a book we know to be stale. Letting
        the next update drive it costs one event of latency and is honest about what we knew.
        """
        if fill.order_id == self._bid_id:
            self._bid_id, self._bid_ticks = None, None
        elif fill.order_id == self._ask_id:
            self._ask_id, self._ask_ticks = None, None
        return []

    def on_timer(self, ts: int, ctx: StrategyContext) -> list[Intent]:
        """Requote in a quiet book. Stale quotes in a market that has stopped updating are
        how inventory accumulates against you, and a strategy driven only by book updates
        cannot react to the absence of book updates."""
        target = self.quotes(ctx)
        if target is None:
            return []
        return self._reconcile(ctx, *target)

    # ---- quote management ----------------------------------------------------------------

    def _reconcile(self, ctx: StrategyContext, bid_ticks: int, ask_ticks: int) -> list[Intent]:
        """Bring live quotes toward the target, paying for priority only when it is worth it."""
        book = ctx.book
        symbol = book.symbol
        inventory = ctx.position(symbol)
        p = self.params
        intents: list[Intent] = []

        # HARD inventory bound: stop quoting the side that would deepen the position. A skew
        # alone is soft, and a trending market walks straight through a soft control.
        may_bid = inventory + p.quote_size <= p.max_inventory
        may_ask = inventory - p.quote_size >= -p.max_inventory

        live = {o.client_id for o in ctx.orders_for(symbol)}
        if self._bid_id not in live:
            self._bid_id, self._bid_ticks = None, None
        if self._ask_id not in live:
            self._ask_id, self._ask_ticks = None, None

        intents += self._side_intents(
            symbol=symbol, side_is_bid=True, allowed=may_bid, target_ticks=bid_ticks,
        )
        intents += self._side_intents(
            symbol=symbol, side_is_bid=False, allowed=may_ask, target_ticks=ask_ticks,
        )
        return intents

    def _side_intents(self, *, symbol: str, side_is_bid: bool, allowed: bool, target_ticks: int) -> list[Intent]:
        from quant.events import Side

        current_id = self._bid_id if side_is_bid else self._ask_id
        current_ticks = self._bid_ticks if side_is_bid else self._ask_ticks

        if not allowed:
            if current_id is None:
                return []
            self._set_side(side_is_bid, None, None)
            return [CancelOrder(client_id=current_id)]

        if current_id is not None and current_ticks is not None:
            # THE HYSTERESIS. Holding a quote that is slightly stale preserves queue priority
            # that a replace would forfeit. See the module docstring for why this is the
            # parameter that most changes the strategy's economics.
            if abs(target_ticks - current_ticks) < self.params.requote_threshold_ticks:
                return []
            self._set_side(side_is_bid, None, None)
            new_id = self.next_client_id()
            self._set_side(side_is_bid, new_id, target_ticks)
            return [
                CancelOrder(client_id=current_id),
                NewOrder(client_id=new_id, symbol=symbol,
                         side=Side.BUY if side_is_bid else Side.SELL,
                         qty=self.params.quote_size, price_ticks=target_ticks),
            ]

        new_id = self.next_client_id()
        self._set_side(side_is_bid, new_id, target_ticks)
        return [
            NewOrder(client_id=new_id, symbol=symbol,
                     side=Side.BUY if side_is_bid else Side.SELL,
                     qty=self.params.quote_size, price_ticks=target_ticks)
        ]

    def _set_side(self, side_is_bid: bool, order_id: str | None, ticks: int | None) -> None:
        if side_is_bid:
            self._bid_id, self._bid_ticks = order_id, ticks
        else:
            self._ask_id, self._ask_ticks = order_id, ticks

    def describe(self) -> dict:
        return {
            "strategy": "market_maker",
            "name": self.name,
            "params": {
                "gamma": self.params.gamma,
                "kappa": self.params.kappa,
                "quote_size": self.params.quote_size,
                "max_inventory": self.params.max_inventory,
                "requote_threshold_ticks": self.params.requote_threshold_ticks,
                "horizon_ns": self.params.horizon_ns,
            },
            "state": {
                "volatility": self._vol.value,
                "reservation_ticks": self._last_reservation,
                "half_spread_ticks": self._last_half_spread,
                "bid_ticks": self._bid_ticks,
                "ask_ticks": self._ask_ticks,
            },
        }
