"""QT-04 — realistic execution costs. Where most toy backtests actually die.

WHY THIS IS A MILESTONE AND NOT A FOOTNOTE
--------------------------------------------
A strategy that is marginally profitable gross is usually decisively unprofitable net, and
the strategies most worth building are exactly the marginal ones. Costs are therefore not a
correction applied at the end — they are the thing that decides the answer. A backtest that
models fills perfectly and fees approximately has modelled the easy half.

The specific trap for this engine: the maker and the taker sit on OPPOSITE SIDES of the same
fee schedule. A single "assume 1bp of costs" fudge flatters the market maker (which actually
EARNS a rebate) and understates the momentum strategy (which pays the taker fee on both
legs). One number cannot be wrong in only one direction, so the schedule is explicit.

US EQUITY FEES ARE PER SHARE, NOT PER DOLLAR
----------------------------------------------
This is the modelling error most worth avoiding here. Exchange maker/taker pricing is quoted
in dollars PER SHARE (~$0.0020 rebate, ~$0.0030 fee), not in basis points. The distinction is
not cosmetic — it inverts with price:

    $400 stock, 100 shares: $0.30 taker fee on $40,000 = 0.075 bps. Negligible.
    $4   stock, 100 shares: $0.30 taker fee on $400    = 7.5 bps.   Decisive.

Model it in bps and every low-priced name looks tradeable when it is not, which is precisely
where a naive high-frequency strategy would concentrate. Regulatory fees (SEC Section 31,
FINRA TAF) are genuinely per-notional and per-share respectively, and both are SELL-SIDE
ONLY — a symmetry error there quietly doubles the cost of every round trip.

RATES CHANGE. THESE ARE DEFAULTS, NOT CONSTANTS.
-------------------------------------------------
The SEC Section 31 rate is reset by the Commission, sometimes more than once a year, and
venue rebate schedules change with tier and with volume. The numbers below are plausible
2024-25 US equity defaults for a mid-tier participant, they are PARAMETERS, and any result
that depends sensitively on them should be reported as a range rather than a point.

DECIMAL, AND NO IMPORTS BEYOND THE STDLIB
-------------------------------------------
Money is ``Decimal`` here for the same reason it is in ``risk/engine.py`` — the repo has one
money type and this is it. The dependency-free constraint is load-bearing rather than
stylistic: ``quant/verify.py`` estimates costs and is imported by ``risk/``, which Invariant 5
requires to stay pure and deterministic. A numpy import in this module would propagate
straight into the risk engine.
"""
from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from decimal import Decimal

from quant.events import Fill, Liquidity, PriceScale, Side

log = logging.getLogger(__name__)


SECONDS_PER_DAY = 24 * 60 * 60
HOLDING_SECONDS = {
    "intraday": 1 * SECONDS_PER_DAY,
    "short": 7 * SECONDS_PER_DAY,
    "long": 30 * SECONDS_PER_DAY,
}
"""W-21 borrow horizon contract, in calendar seconds.

Borrow accrues over nights and weekends, not trading-session seconds.  ``intraday`` still
reserves a full borrow day because a broker may charge a daily minimum.  This mapping is the
single home for the proposal schema's deliberately coarse horizon enum.
"""


def holding_seconds(time_horizon: str) -> int:
    """Return the pinned calendar holding duration; reject unknown horizons explicitly."""
    try:
        return HOLDING_SECONDS[str(time_horizon)]
    except KeyError as exc:
        raise ValueError(f"unknown time_horizon {time_horizon!r}") from exc

# US equity regulatory fees. Sell-side only, both of them.
DEFAULT_SEC_FEE_RATE = Decimal("0.0000278")
"""SEC Section 31, per dollar of SELL proceeds. Reset periodically by the Commission."""

DEFAULT_TAF_PER_SHARE = Decimal("0.000166")
DEFAULT_TAF_CAP = Decimal("8.30")
"""FINRA Trading Activity Fee: per share SOLD, capped per trade."""


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """One venue's economics. Per-share for exchange fees, per-notional for Section 31."""

    maker_rebate_per_share: Decimal = Decimal("0.0020")
    """CREDITED, not charged. A market maker's rebate is a real and often decisive part of
    its revenue: at 100 shares a side that is $0.20 per round trip, which on a penny-spread
    name is comparable to the entire captured spread. A cost model that omits it does not
    merely understate the maker — it can invert the sign of the strategy's profitability."""

    taker_fee_per_share: Decimal = Decimal("0.0030")
    sec_fee_rate: Decimal = DEFAULT_SEC_FEE_RATE
    taf_per_share: Decimal = DEFAULT_TAF_PER_SHARE
    taf_cap: Decimal = DEFAULT_TAF_CAP
    clearing_per_share: Decimal = Decimal("0.0002")

    borrow_rate_annual: Decimal = Decimal("0.005")
    """Annualised borrow on SHORT notional. 50bps is a general-collateral default; a hard-to-
    borrow name can be 20x that or more, which is why it is a parameter. A stat-arb strategy
    that shorts the rich leg pays this continuously, and it is the cost most often left out
    of a pairs backtest — the short leg is treated as symmetric with the long, and it is not."""

    locate_fee: Decimal = Decimal("0.00")

    def fee_for(self, *, side: Side, qty: int, price: Decimal, liquidity: Liquidity) -> Decimal:
        """Total cost of one fill. NEGATIVE means we were PAID (a maker rebate net of fees).

        Signed rather than absolute so the caller cannot accidentally add a rebate as a cost.
        A rebate that arrives as a positive "fee" and is subtracted is a sign error worth
        roughly twice the rebate, and it silently makes passive strategies look unprofitable.

        One fill stays on Decimal. Formatting the schedule into strings and parsing a
        Decimal back measured slower than this function, and ``fee_for_many`` did too.
        The extension remains for a caller that already holds those strings.
        """
        return python_fee_for(self, side=side, qty=qty, price=price, liquidity=liquidity)

    def borrow_cost(self, *, short_notional: Decimal, seconds: float) -> Decimal:
        """Carry on a short position held for ``seconds``. Zero for a flat or long book.

        Charged on ELAPSED TIME rather than per day, because a high-frequency strategy holds
        for seconds and a daily accrual would round its entire borrow cost to zero — which is
        the assumption that makes an intraday short look free.

        One interval stays on Decimal. The string crossing measured slower.
        """
        return python_borrow_cost(self, short_notional=short_notional, seconds=seconds)


ZERO_FEES = FeeSchedule(
    maker_rebate_per_share=Decimal("0"), taker_fee_per_share=Decimal("0"),
    sec_fee_rate=Decimal("0"), taf_per_share=Decimal("0"),
    clearing_per_share=Decimal("0"), borrow_rate_annual=Decimal("0"),
)
"""A frictionless venue. FOR TESTS AND FOR MEASURING THE COST OF COSTS ONLY.

Running a strategy against this and against a real schedule is the cleanest way to see how
much of a result is edge and how much is a fee assumption. Never a default: a zero-fee
backtest is the specific artefact this module exists to prevent.
"""


def fee_for_fill(fill: Fill, scale: PriceScale, schedule: FeeSchedule) -> Decimal:
    """Convenience wrapper: cost of a ``Fill``, converting ticks to a Decimal price."""
    return schedule.fee_for(
        side=fill.side, qty=fill.qty,
        price=scale.to_decimal(fill.price_ticks), liquidity=fill.liquidity,
    )


def cost_bps(cost: Decimal, notional: Decimal) -> Decimal:
    """Cost as basis points of notional. Sign preserved, so a rebate reads negative.

    One ratio stays on Decimal. The string crossing measured slower.
    """
    return python_cost_bps(cost, notional)


def python_fee_for(
    schedule: FeeSchedule, *, side: Side, qty: int, price: Decimal, liquidity: Liquidity,
) -> Decimal:
    """Unaccelerated ``FeeSchedule.fee_for``. Parity tests call this directly."""
    if qty <= 0:
        raise ValueError(f"fee_for needs a positive qty, got {qty}")
    shares = Decimal(qty)
    notional = price * shares

    exchange = (
        -schedule.maker_rebate_per_share * shares
        if liquidity is Liquidity.MAKER
        else schedule.taker_fee_per_share * shares
    )
    regulatory = Decimal("0")
    if side is Side.SELL:
        regulatory += (notional * schedule.sec_fee_rate).quantize(Decimal("0.01"))
        regulatory += min(schedule.taf_per_share * shares, schedule.taf_cap)
    return exchange + regulatory + schedule.clearing_per_share * shares


def python_borrow_cost(schedule: FeeSchedule, *, short_notional: Decimal, seconds: float) -> Decimal:
    """Unaccelerated ``FeeSchedule.borrow_cost``."""
    if short_notional <= 0 or seconds <= 0:
        return Decimal("0")
    year = Decimal(365 * 24 * 3600)
    return short_notional * schedule.borrow_rate_annual * Decimal(str(seconds)) / year


def python_cost_bps(cost: Decimal, notional: Decimal) -> Decimal:
    """Unaccelerated ``cost_bps``."""
    if notional <= 0:
        return Decimal("0")
    return cost / notional * Decimal("10000")


COSTS_BACKEND = "python"


def _load_native() -> None:
    """Record that the costs extension is installed. Per-call methods stay on Decimal.

    A missing module keeps that path. A present extension that fails to import is
    broken and the error propagates. A present module missing ``fee_for``,
    ``borrow_cost``, ``cost_bps``, or ``fee_for_many`` is a partial wheel.
    ``cockpit_quant`` is arithmetic only: it does not enter the impure set that
    ``quant/verify.py`` is forbidden to reach.
    """
    global COSTS_BACKEND
    if importlib.util.find_spec("cockpit_quant") is None:
        log.info("COSTS_BACKEND=%s", COSTS_BACKEND)
        return
    import cockpit_quant as native

    required = ("fee_for", "borrow_cost", "cost_bps", "fee_for_many")
    missing = [name for name in required if not hasattr(native, name)]
    if missing:
        raise ImportError(
            "cockpit_quant is installed but does not provide " + ", ".join(missing)
        )
    COSTS_BACKEND = "rust"
    log.info("COSTS_BACKEND=%s", COSTS_BACKEND)


_load_native()
