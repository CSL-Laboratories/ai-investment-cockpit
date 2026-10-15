"""QT-04 — PnL attribution: spread capture, inventory drift, and fees. Three numbers.

WHY NOT ONE NUMBER
-------------------
"The strategy made $412" is almost useless for deciding what to do next. The same total can
mean a maker that captured the spread cleanly and then gave a third of it back holding
inventory, or one that captured nothing and got lucky on a drift. Those imply opposite
changes, and the total cannot tell them apart. So every result decomposes into:

    SPREAD CAPTURE   sum over fills of (mid - price) * signed_qty
                     What we earned at the moment of trading, measured against the mid then.
                     This is the edge the strategy was actually trying to capture. Negative
                     spread capture on a market maker means it is crossing, not making.

    INVENTORY DRIFT  sum over intervals of position * change-in-mid
                     What happened to us for HOLDING. This is the risk the A-S inventory term
                     exists to manage, and a maker with large positive spread capture and
                     larger negative inventory drift is quoting well and hedging badly — a
                     completely different fix from quoting badly.

    FEES             from costs.FeeSchedule, signed, so a maker rebate reads as income.

THE DECOMPOSITION IS EXACT, AND THAT IS TESTED
------------------------------------------------
    spread_capture + inventory_drift + fees == mark-to-market PnL

This is an identity, not an approximation, and ``tests/quant/test_pnl.py`` proves it against
a randomised fill stream. That matters more than it looks: an attribution that only
approximately reconciles is one where the residual absorbs every bookkeeping error, and the
residual is never inspected. If these three numbers must sum to the total exactly, then a
mistake in any of them shows up as a broken test rather than as a plausible-looking report.

The algebra, since it is easy to get subtly wrong:
    MTM     = sum_i s_i * (M_now - P_i)                    (cash + position marked to mid)
    spread  = sum_i s_i * (M_i - P_i)
    drift   = sum_t pos_t * (M_t+1 - M_t)
            = sum_i s_i * (M_now - M_i)                    (regrouping by fill)
    => spread + drift = MTM.
The ordering constraint that makes it hold: at a fill, mark the OLD position against the
current mid BEFORE applying the fill. Doing it the other way attributes the fill's own
inventory to a price move that had not happened yet.

DECIMAL THROUGHOUT
-------------------
Money is ``Decimal``, matching ``risk/engine.py``. Float accumulation over hundreds of
thousands of fills produces a residual that looks exactly like a small attribution bug and
would make the exactness test above flap rather than fail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from quant.costs import FeeSchedule, fee_for_fill
from quant.events import Fill, Liquidity, PriceScale, Side


@dataclass(frozen=True, slots=True)
class Attribution:
    """A completed decomposition. ``total`` is the sum of the parts, by construction."""

    spread_capture: Decimal
    inventory_drift: Decimal
    fees: Decimal
    realized_notional: Decimal
    fills: int
    maker_fills: int
    taker_fills: int
    final_position: int
    final_mid: Decimal | None

    @property
    def gross(self) -> Decimal:
        """Before fees. The number a zero-fee backtest would report."""
        return self.spread_capture + self.inventory_drift

    @property
    def total(self) -> Decimal:
        """Fees are SIGNED (a rebate is negative), so they are subtracted, not added."""
        return self.gross - self.fees

    @property
    def maker_ratio(self) -> float:
        """Fraction of fills that added liquidity. A 'market maker' below ~0.5 is not one."""
        if not self.fills:
            return 0.0
        return self.maker_fills / self.fills

    @property
    def fee_bps(self) -> Decimal:
        if self.realized_notional <= 0:
            return Decimal("0")
        return self.fees / self.realized_notional * Decimal("10000")

    def as_dict(self) -> dict:
        return {
            "spread_capture": str(self.spread_capture),
            "inventory_drift": str(self.inventory_drift),
            "fees": str(self.fees),
            "gross": str(self.gross),
            "total": str(self.total),
            "fills": self.fills,
            "maker_fills": self.maker_fills,
            "taker_fills": self.taker_fills,
            "maker_ratio": round(self.maker_ratio, 4),
            "fee_bps": str(self.fee_bps.quantize(Decimal("0.01"))),
            "final_position": self.final_position,
            "realized_notional": str(self.realized_notional),
        }


@dataclass(slots=True)
class PnLTracker:
    """Online attribution for one strategy on one symbol.

    Online rather than a post-hoc pass over a fill log, because inventory drift depends on
    the mid path BETWEEN fills — information a fill log does not contain. Reconstructing it
    afterwards from sampled mids gives a different (and quietly wrong) answer.
    """

    scale: PriceScale
    schedule: FeeSchedule
    spread_capture: Decimal = field(default=Decimal("0"), init=False)
    inventory_drift: Decimal = field(default=Decimal("0"), init=False)
    fees: Decimal = field(default=Decimal("0"), init=False)
    realized_notional: Decimal = field(default=Decimal("0"), init=False)
    position: int = field(default=0, init=False)
    fills: int = field(default=0, init=False)
    maker_fills: int = field(default=0, init=False)
    taker_fills: int = field(default=0, init=False)
    _last_mid: Decimal | None = field(default=None, init=False, repr=False)
    _last_ts: int | None = field(default=None, init=False, repr=False)
    _cash: Decimal = field(default=Decimal("0"), init=False, repr=False)
    """Running cash from fills, maintained independently of the three attribution terms so
    ``mark_to_market`` is a genuinely separate calculation for the exactness test to check
    against — not the same expression rearranged."""
    # One mark or fill is a handful of Decimal operations. Crossing into the extension
    # on each call measured slower, and so did ``pnl_replay`` of a finished tape once
    # tracker construction was not charged to the Decimal loop. Guards read the running
    # total, so the paper loop calls ``mark`` and ``on_fill``.
    _core: object | None = field(default=None, init=False, repr=False)

    def mark(self, mid_ticks: float | None, ts: int | None = None) -> None:
        """Mark the CURRENT position to a new mid, accruing inventory drift and borrow.

        Call this on every book update. Marking rarely does not bias the total — the drift
        telescopes, so the endpoints are what matter — but it does coarsen the equity curve,
        and drawdown measured on a coarse curve understates the real one.
        """
        if mid_ticks is None:
            return
        # A mid can sit on a half tick, so it is not a price on the grid and ``to_decimal``
        # would rightly refuse it. Scaling the exact decimal form of the float keeps the
        # arithmetic in Decimal without pretending a half-tick mid is tradeable.
        mid = Decimal(str(mid_ticks)) * self.scale.tick_size
        if self._last_mid is not None and self.position:
            self.inventory_drift += Decimal(self.position) * (mid - self._last_mid)
            if self.position < 0 and ts is not None and self._last_ts is not None:
                elapsed = (ts - self._last_ts) / 1_000_000_000
                self.fees += self.schedule.borrow_cost(
                    short_notional=Decimal(-self.position) * mid, seconds=elapsed,
                )
        self._last_mid = mid
        self._last_ts = ts

    def on_fill(self, fill: Fill, mid_ticks: float | None = None) -> None:
        """Fold in one of our executions.

        ORDER IS LOAD-BEARING: mark the OLD position first, then attribute the fill's edge,
        then move the position. Applying the fill before marking would attribute this fill's
        inventory to a price move that had not happened yet, and the exactness identity in
        the module docstring would stop holding.
        """
        if mid_ticks is not None:
            self.mark(mid_ticks, fill.ingest_ts)

        price = self.scale.to_decimal(fill.price_ticks)
        if self._last_mid is not None:
            self.spread_capture += (self._last_mid - price) * Decimal(fill.signed_qty)

        self.fees += fee_for_fill(fill, self.scale, self.schedule)
        self.realized_notional += price * Decimal(fill.qty)
        self._cash -= price * Decimal(fill.signed_qty)
        self.position += fill.signed_qty
        self.fills += 1
        if fill.liquidity is Liquidity.MAKER:
            self.maker_fills += 1
        else:
            self.taker_fills += 1

    def snapshot(self) -> Attribution:
        return Attribution(
            spread_capture=self.spread_capture,
            inventory_drift=self.inventory_drift,
            fees=self.fees,
            realized_notional=self.realized_notional,
            fills=self.fills,
            maker_fills=self.maker_fills,
            taker_fills=self.taker_fills,
            final_position=self.position,
            final_mid=self._last_mid,
        )

    def mark_to_market(self) -> Decimal:
        """Independent gross total: cash plus inventory at the last mid.

        Computed WITHOUT reference to the three attribution terms, so the exactness test
        compares two genuinely separate calculations rather than one expression against
        itself. Gross, i.e. before fees — fees are a separate ledger, not a price effect.
        """
        return self._cash + (Decimal(self.position) * (self._last_mid or Decimal("0")))


_I64_MAX = 2**63 - 1


def _pnl_backend() -> str:
    try:
        import cockpit_quant as native
    except ImportError:
        return "python"
    return "rust" if hasattr(native, "PnlCore") else "python"


PNL_BACKEND = _pnl_backend()


def _make_pnl_core(schedule: FeeSchedule):
    if PNL_BACKEND != "rust":
        return None
    import cockpit_quant as native
    return native.PnlCore(
        format(schedule.maker_rebate_per_share, "f"),
        format(schedule.taker_fee_per_share, "f"),
        format(schedule.sec_fee_rate, "f"),
        format(schedule.taf_per_share, "f"),
        format(schedule.taf_cap, "f"),
        format(schedule.clearing_per_share, "f"),
        format(schedule.borrow_rate_annual, "f"),
    )


def _fits_i64(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and -_I64_MAX - 1 <= value <= _I64_MAX


def _rust_mid_ok(mid_ticks: float | None) -> bool:
    return mid_ticks is not None and not isinstance(mid_ticks, bool) and isinstance(mid_ticks, (int, float))


def _rust_fill_ok(fill: Fill, mid_ticks: float | None) -> bool:
    if mid_ticks is not None and not _rust_mid_ok(mid_ticks):
        return False
    return all(_fits_i64(value) for value in (fill.qty, fill.price_ticks, fill.signed_qty, fill.ingest_ts))


def _elapsed(last_ts: int | None, ts: int | None) -> str | None:
    if ts is None or last_ts is None:
        return None
    return str((ts - last_ts) / 1_000_000_000)


def _load(tracker: PnLTracker, state: tuple) -> None:
    spread, drift, fees, realized, position, fills, maker, taker, last_mid, last_ts, cash = state
    tracker.spread_capture = Decimal(spread)
    tracker.inventory_drift = Decimal(drift)
    tracker.fees = Decimal(fees)
    tracker.realized_notional = Decimal(realized)
    tracker.position = int(position)
    tracker.fills = int(fills)
    tracker.maker_fills = int(maker)
    tracker.taker_fills = int(taker)
    tracker._last_mid = None if last_mid is None else Decimal(last_mid)
    tracker._last_ts = None if last_ts is None else int(last_ts)
    tracker._cash = Decimal(cash)


def _rust_mark(tracker: PnLTracker, mid_ticks: float, ts: int | None) -> None:
    state = tracker._core.mark(
        str(mid_ticks), format(tracker.scale.tick_size, "f"), ts, _elapsed(tracker._last_ts, ts),
    )
    _load(tracker, state)


def _rust_on_fill(tracker: PnLTracker, fill: Fill, mid_ticks: float | None) -> None:
    elapsed = None
    ingest_ts = None
    mid_text = None
    if mid_ticks is not None:
        mid_text = str(mid_ticks)
        ingest_ts = fill.ingest_ts
        elapsed = _elapsed(tracker._last_ts, fill.ingest_ts)
    state = tracker._core.on_fill(
        fill.side is Side.SELL,
        fill.qty,
        fill.price_ticks,
        fill.liquidity is Liquidity.MAKER,
        fill.signed_qty,
        format(tracker.scale.tick_size, "f"),
        ingest_ts,
        mid_text,
        elapsed,
    )
    _load(tracker, state)
