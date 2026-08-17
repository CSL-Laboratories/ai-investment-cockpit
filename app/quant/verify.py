"""W-21 — pure pre-trade transaction-cost and net-edge verification.

The live/paper verifier uses L1 prices plus RW-14 daily statistics.  Displayed IEX sizes are
not a liquidity model: participation is dollar notional divided by median daily dollar
volume, and impact follows the square-root law ``Y * sigma_daily * sqrt(Q / ADV)``.

Every authority-bearing input is injected.  This module performs no I/O, clock read, model
call, or subprocess spawn, preserving Invariant 5.  A model-authored proposal cannot provide
its own expected return or borrow rate; those values arrive only as typed, independently
verified inputs.  Production intentionally supplies no edge estimate until a deterministic
strategy source is separately selected, so the risk layer can report the cost while refusing
to mint an order.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from quant.book.lob import BookSnapshot
from quant.costs import FeeSchedule, holding_seconds
from quant.events import PriceScale, Side


TRADING_DAYS_PER_YEAR = Decimal("252")
CALENDAR_SECONDS_PER_YEAR = Decimal(365 * 24 * 60 * 60)
_BPS = Decimal("10000")
_BPS_QUANTUM = Decimal("0.01")
_PARTICIPATION_QUANTUM = Decimal("0.000001")
_MONEY_QUANTUM = Decimal("0.01")


class Verdict(enum.Enum):
    OK = "ok"
    MARGINAL = "marginal"
    UNECONOMIC = "uneconomic"
    INSUFFICIENT_DATA = "insufficient_data"

    @property
    def is_actionable(self) -> bool:
        return self is Verdict.OK


class PositionEffect(enum.Enum):
    """Deterministic position intent; never sourced from the model proposal.

    The current production boundary maps BUY to ``OPEN_LONG`` and SELL to ``CLOSE_LONG``.
    ``OPEN_SHORT`` exists only to make the borrow contract testable and fail-closed; W-21 does
    not weaken ``risk.engine``'s existing no-naked-short rule or make that state reachable.
    """

    OPEN_LONG = "open_long"
    CLOSE_LONG = "close_long"
    OPEN_SHORT = "open_short"
    CLOSE_SHORT = "close_short"

    @property
    def is_opening(self) -> bool:
        return self in {PositionEffect.OPEN_LONG, PositionEffect.OPEN_SHORT}

    @property
    def is_short_entry(self) -> bool:
        return self is PositionEffect.OPEN_SHORT


@dataclass(frozen=True, slots=True)
class MarketImpactInputs:
    """Pure boundary form of RW-14 ``MarketMetrics``.

    ``annualized_realized_volatility`` is a decimal fraction (``0.20`` = 20% annualized),
    while ``median_daily_dollar_volume`` is dollars/day.  ``age_days`` is computed by the I/O
    caller from the injected clock and the metric's completed-bar ``as_of`` date.
    """

    symbol: str
    as_of: date
    median_daily_dollar_volume: Decimal
    annualized_realized_volatility: Decimal
    observations: int
    age_days: int


@dataclass(frozen=True, slots=True)
class EdgeEstimate:
    """Schema-like trusted edge input reserved for a deterministic strategy source.

    The unit is gross holding-period return in basis points of position notional. W-23's
    cash-merger provider may construct it from signed immutable evidence; every unavailable,
    stale, or ineligible state still supplies ``None`` and fails closed.
    """

    expected_gross_return_bps: Decimal
    time_horizon: str
    source_id: str
    evidence_ref: str
    computed_at_ns: int
    verified: bool
    schema_version: str = "edge_estimate.v1"


@dataclass(frozen=True, slots=True)
class BorrowQuote:
    """Verified borrow input for an explicitly identified short entry.

    ``annual_rate`` is a decimal annual fraction (``0.30`` = 30%/year); ``locate_fee`` is
    dollars for this order.  Missing, stale, unverified, or malformed data never falls back to
    the simulator's general-collateral default.
    """

    annual_rate: Decimal
    locate_fee: Decimal
    source_id: str
    computed_at_ns: int
    verified: bool
    schema_version: str = "borrow_quote.v1"


@dataclass(frozen=True, slots=True)
class VerifyParams:
    """Pinned conservative assumptions.  They are explicit and replayable."""

    scale: PriceScale
    schedule: FeeSchedule = field(default_factory=FeeSchedule)
    marginal_cost_bps: Decimal = Decimal("15")
    uneconomic_cost_bps: Decimal = Decimal("50")
    max_adv_participation: Decimal = Decimal("0.10")
    impact_coefficient_y: Decimal = Decimal("1.0")
    sensitivity_low_y: Decimal = Decimal("0.5")
    min_net_cost_multiple: Decimal = Decimal("2.0")
    max_metrics_age_days: int = 7
    max_staleness_ns: int = 5_000_000_000
    max_edge_age_ns: int = 24 * 60 * 60 * 1_000_000_000
    max_borrow_age_ns: int = 24 * 60 * 60 * 1_000_000_000
    max_borrow_rate: Decimal = Decimal("5.0")
    max_expected_gross_return_bps: Decimal = Decimal("10000")
    curve_participation_points: tuple[Decimal, ...] = (
        Decimal("0.0001"), Decimal("0.00025"), Decimal("0.0005"),
        Decimal("0.001"), Decimal("0.0025"), Decimal("0.005"),
        Decimal("0.01"), Decimal("0.025"), Decimal("0.05"), Decimal("0.10"),
    )


@dataclass(frozen=True, slots=True)
class CostSensitivityPoint:
    qty: int
    notional: Decimal
    adv_participation_rate: Decimal
    low_impact_cost_bps: Decimal
    gate_cost_bps: Decimal
    net_return_bps: Decimal | None

    def as_dict(self) -> dict:
        return {
            "qty": self.qty,
            "notional": str(self.notional),
            "adv_participation_rate": str(self.adv_participation_rate),
            "low_impact_cost_bps": str(self.low_impact_cost_bps),
            "gate_cost_bps": str(self.gate_cost_bps),
            "net_return_bps": (
                None if self.net_return_bps is None else str(self.net_return_bps)
            ),
        }


@dataclass(frozen=True, slots=True)
class _CostComponents:
    spread_bps: Decimal
    impact_bps: Decimal
    fee_bps: Decimal
    borrow_bps: Decimal

    @property
    def total_bps(self) -> Decimal:
        return self.spread_bps + self.impact_bps + self.fee_bps + self.borrow_bps


@dataclass(frozen=True, slots=True)
class _BorrowTerms:
    annual_rate: Decimal
    locate_fee: Decimal
    source_id: str


@dataclass(frozen=True, slots=True)
class QuantVerdict:
    """Serializable deterministic evidence attached to the ``RiskDecision`` row."""

    symbol: str
    side: str
    qty: int
    position_effect: str
    verdict: Verdict
    expected_spread_bps: Decimal
    expected_impact_bps: Decimal
    expected_fee_bps: Decimal
    expected_borrow_bps: Decimal
    expected_cost_bps: Decimal
    adv_participation_rate: Decimal
    borrow_state: str
    borrow_source_id: str | None
    net_edge_state: str
    edge_source_id: str | None
    edge_evidence_ref: str | None
    expected_gross_return_bps: Decimal | None
    expected_net_return_bps: Decimal | None
    net_cost_multiple: Decimal | None
    min_net_cost_multiple: Decimal
    cost_curve: tuple[CostSensitivityPoint, ...]
    first_negative_qty: int | None
    first_negative_notional: Decimal | None
    max_model_qty: int
    reasons: tuple[str, ...]
    book_seq: int
    computed_at_ns: int
    metrics_as_of: str | None

    @property
    def is_actionable(self) -> bool:
        return self.verdict.is_actionable and self.net_edge_state in {
            "sufficient", "not_applicable"
        }

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "position_effect": self.position_effect,
            "verdict": self.verdict.value,
            "expected_spread_bps": str(self.expected_spread_bps),
            "expected_impact_bps": str(self.expected_impact_bps),
            "expected_fee_bps": str(self.expected_fee_bps),
            "expected_borrow_bps": str(self.expected_borrow_bps),
            "expected_cost_bps": str(self.expected_cost_bps),
            "adv_participation_rate": str(self.adv_participation_rate),
            "borrow_state": self.borrow_state,
            "borrow_source_id": self.borrow_source_id,
            "net_edge_state": self.net_edge_state,
            "edge_source_id": self.edge_source_id,
            "edge_evidence_ref": self.edge_evidence_ref,
            "expected_gross_return_bps": (
                None if self.expected_gross_return_bps is None
                else str(self.expected_gross_return_bps)
            ),
            "expected_net_return_bps": (
                None if self.expected_net_return_bps is None
                else str(self.expected_net_return_bps)
            ),
            "net_cost_multiple": (
                None if self.net_cost_multiple is None else str(self.net_cost_multiple)
            ),
            "min_net_cost_multiple": str(self.min_net_cost_multiple),
            "cost_curve": [point.as_dict() for point in self.cost_curve],
            "first_negative_qty": self.first_negative_qty,
            "first_negative_notional": (
                None if self.first_negative_notional is None
                else str(self.first_negative_notional)
            ),
            "max_model_qty": self.max_model_qty,
            "reasons": list(self.reasons),
            "book_seq": self.book_seq,
            "computed_at_ns": self.computed_at_ns,
            "metrics_as_of": self.metrics_as_of,
        }


def _d(value) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("NaN")


def _qbps(value: Decimal) -> Decimal:
    return value.quantize(_BPS_QUANTUM)


def _effect_for(proposal: dict, explicit: PositionEffect | None) -> PositionEffect | None:
    if explicit is not None:
        try:
            return explicit if isinstance(explicit, PositionEffect) else PositionEffect(explicit)
        except ValueError:
            return None
    if proposal.get("side") == "buy":
        return PositionEffect.OPEN_LONG
    if proposal.get("side") == "sell":
        return PositionEffect.CLOSE_LONG
    return None


def proposal_quantity(proposal: dict, reference_price: Decimal) -> int | None:
    """Resolve whole shares once; never silently resize a fractional instruction."""
    quantity = proposal.get("quantity")
    if quantity is not None:
        explicit = _d(quantity)
        if (not explicit.is_finite() or explicit <= 0
                or explicit.as_integer_ratio()[1] != 1):
            return None
        return int(explicit)

    notional = proposal.get("notional")
    if notional is None or not reference_price.is_finite() or reference_price <= 0:
        return None
    amount = _d(notional)
    if not amount.is_finite() or amount <= 0:
        return None
    try:
        shares = int(amount / reference_price)
    except (InvalidOperation, ValueError, ZeroDivisionError, OverflowError):
        return None
    return shares if shares > 0 else None


def _empty_verdict(*, symbol: str, side: str, effect: PositionEffect | None, qty: int,
                   book_seq: int, ts: int, reason: str) -> QuantVerdict:
    zero = Decimal("0")
    return QuantVerdict(
        symbol=symbol, side=side, qty=qty,
        position_effect=effect.value if effect is not None else "unknown",
        verdict=Verdict.INSUFFICIENT_DATA,
        expected_spread_bps=zero, expected_impact_bps=zero,
        expected_fee_bps=zero, expected_borrow_bps=zero, expected_cost_bps=zero,
        adv_participation_rate=zero,
        borrow_state="unavailable", borrow_source_id=None,
        net_edge_state="unavailable", edge_source_id=None, edge_evidence_ref=None,
        expected_gross_return_bps=None, expected_net_return_bps=None,
        net_cost_multiple=None, min_net_cost_multiple=Decimal("2.0"),
        cost_curve=(), first_negative_qty=None, first_negative_notional=None,
        max_model_qty=0, reasons=(reason,), book_seq=book_seq,
        computed_at_ns=ts, metrics_as_of=None,
    )


def _validate_params(params: VerifyParams) -> str | None:
    checks = (
        (params.max_adv_participation > 0
         and params.max_adv_participation <= Decimal("0.10"),
         "max ADV participation must be in (0, 0.10]"),
        (params.impact_coefficient_y == Decimal("1.0"),
         "gate impact coefficient Y must remain pinned at 1.0"),
        (params.sensitivity_low_y >= Decimal("0.5")
         and params.sensitivity_low_y <= params.impact_coefficient_y,
         "impact sensitivity lower bound must be in [0.5, 1.0]"),
        (params.min_net_cost_multiple >= Decimal("2.0")
         and params.min_net_cost_multiple <= Decimal("3.0"),
         "net-to-cost multiple must be in [2.0, 3.0]"),
        (params.max_metrics_age_days >= 0, "metrics age limit must be non-negative"),
    )
    return next((reason for passed, reason in checks if not passed), None)


def _validate_metrics(metrics: MarketImpactInputs | None, *, symbol: str,
                      params: VerifyParams) -> tuple[Decimal, Decimal, str | None]:
    if metrics is None:
        return Decimal("NaN"), Decimal("NaN"), "RW-14 market metrics are unavailable"
    adv = _d(metrics.median_daily_dollar_volume)
    annual_vol = _d(metrics.annualized_realized_volatility)
    if str(metrics.symbol).upper() != str(symbol).upper():
        return adv, annual_vol, "market metrics symbol does not match the proposal"
    if not adv.is_finite() or adv <= 0:
        return adv, annual_vol, "median daily dollar volume is missing or invalid"
    if not annual_vol.is_finite() or annual_vol <= 0:
        return adv, annual_vol, "realized volatility is missing or invalid"
    if not isinstance(metrics.observations, int) or metrics.observations < 3:
        return adv, annual_vol, "market metrics have fewer than three observations"
    if not isinstance(metrics.age_days, int) or metrics.age_days < 0:
        return adv, annual_vol, "market metrics date is future or invalid"
    if metrics.age_days > params.max_metrics_age_days:
        return adv, annual_vol, (
            f"market metrics are stale ({metrics.age_days} days > "
            f"{params.max_metrics_age_days})"
        )
    return adv, annual_vol, None


def _validate_borrow(quote: BorrowQuote | None, *, params: VerifyParams,
                     now_ns: int) -> tuple[_BorrowTerms | None, str | None]:
    if quote is None:
        return None, "verified borrow data is unavailable for the short leg"
    rate = _d(quote.annual_rate)
    locate = _d(quote.locate_fee)
    if quote.schema_version != "borrow_quote.v1":
        return None, "borrow data does not match borrow_quote.v1"
    if quote.verified is not True or not str(quote.source_id).strip():
        return None, "borrow data is unverified or lacks provenance"
    if (not isinstance(quote.computed_at_ns, int) or quote.computed_at_ns > now_ns
            or now_ns - quote.computed_at_ns > params.max_borrow_age_ns):
        return None, "borrow data is stale or future-dated"
    if not rate.is_finite() or rate < 0 or rate > params.max_borrow_rate:
        return None, "annual borrow rate is outside the verified contract"
    if not locate.is_finite() or locate < 0:
        return None, "locate fee is outside the verified contract"
    return _BorrowTerms(rate, locate, str(quote.source_id)), None


def _validate_edge(estimate: EdgeEstimate | None, *, proposal: dict, params: VerifyParams,
                   now_ns: int) -> tuple[Decimal | None, str | None]:
    if estimate is None:
        return None, "trusted expected edge is unavailable"
    gross = _d(estimate.expected_gross_return_bps)
    if estimate.schema_version != "edge_estimate.v1":
        return None, "expected edge does not match edge_estimate.v1"
    if (estimate.verified is not True or not str(estimate.source_id).strip()
            or not str(estimate.evidence_ref).strip()):
        return None, "expected edge is unverified or lacks provenance"
    if str(estimate.time_horizon) != str(proposal.get("time_horizon", "")):
        return None, "expected edge horizon does not match the proposal"
    if (not isinstance(estimate.computed_at_ns, int) or estimate.computed_at_ns > now_ns
            or now_ns - estimate.computed_at_ns > params.max_edge_age_ns):
        return None, "expected edge is stale or future-dated"
    if (not gross.is_finite() or gross > params.max_expected_gross_return_bps
            or gross < -params.max_expected_gross_return_bps):
        return None, "expected edge is non-finite or outside the bounded contract"
    return gross, None


def _sides_for(effect: PositionEffect) -> tuple[Side, ...]:
    if effect is PositionEffect.OPEN_LONG:
        return Side.BUY, Side.SELL
    if effect is PositionEffect.OPEN_SHORT:
        return Side.SELL, Side.BUY
    if effect is PositionEffect.CLOSE_LONG:
        return (Side.SELL,)
    return (Side.BUY,)


def _fee_dollars(*, params: VerifyParams, effect: PositionEffect, qty: int,
                 bid_price: Decimal, ask_price: Decimal) -> Decimal:
    """Conservative uncapped fee estimate used by the authority-bearing cost curve.

    Exchange/clearing/SEC/TAF terms are linear in shares here.  Ignoring the TAF cap can only
    overstate cost, avoids a non-monotone bps curve, and makes the whole-share first-negative
    boundary exact and replayable.  The simulator retains exact venue caps in ``fee_for``.
    """
    schedule = params.schedule
    shares = Decimal(qty)
    total = Decimal("0")
    for side in _sides_for(effect):
        price = ask_price if side is Side.BUY else bid_price
        total += (schedule.taker_fee_per_share + schedule.clearing_per_share) * shares
        if side is Side.SELL:
            total += price * shares * schedule.sec_fee_rate
            total += schedule.taf_per_share * shares
    return total


def _components(*, qty: int, mid_price: Decimal, bid_price: Decimal, ask_price: Decimal,
                adv: Decimal, annual_vol: Decimal, effect: PositionEffect,
                params: VerifyParams, impact_y: Decimal,
                borrow_terms: _BorrowTerms | None, time_horizon: str) -> _CostComponents:
    notional = mid_price * Decimal(qty)
    participation = notional / adv
    half_spread = (ask_price - bid_price) / mid_price * _BPS / Decimal("2")
    legs = Decimal("2") if effect.is_opening else Decimal("1")
    spread_bps = _qbps(half_spread * legs)
    daily_vol = annual_vol / TRADING_DAYS_PER_YEAR.sqrt()
    one_way_impact = impact_y * daily_vol * participation.sqrt() * _BPS
    impact_bps = _qbps(one_way_impact * legs)
    fee_bps = _qbps(
        _fee_dollars(
            params=params, effect=effect, qty=qty,
            bid_price=bid_price, ask_price=ask_price,
        ) / notional * _BPS
    )
    borrow_bps = Decimal("0")
    if effect.is_short_entry and borrow_terms is not None:
        seconds = Decimal(holding_seconds(time_horizon))
        borrow_bps = _qbps(
            borrow_terms.annual_rate * seconds / CALENDAR_SECONDS_PER_YEAR * _BPS
            + borrow_terms.locate_fee / notional * _BPS
        )
    return _CostComponents(spread_bps, impact_bps, fee_bps, borrow_bps)


def _max_model_qty(*, adv: Decimal, mid_price: Decimal, params: VerifyParams) -> int:
    raw = adv * params.max_adv_participation / mid_price
    return max(0, int(raw.to_integral_value(rounding=ROUND_FLOOR)))


def _first_negative_qty(*, gross_bps: Decimal, max_qty: int, mid_price: Decimal,
                        bid_price: Decimal, ask_price: Decimal, adv: Decimal,
                        annual_vol: Decimal, effect: PositionEffect, params: VerifyParams,
                        borrow_terms: _BorrowTerms | None, time_horizon: str) -> int | None:
    if max_qty < 1:
        return None

    def negative(qty: int) -> bool:
        cost = _components(
            qty=qty, mid_price=mid_price, bid_price=bid_price, ask_price=ask_price,
            adv=adv, annual_vol=annual_vol, effect=effect, params=params,
            impact_y=params.impact_coefficient_y, borrow_terms=borrow_terms,
            time_horizon=time_horizon,
        ).total_bps
        return gross_bps - cost < 0

    if negative(1):
        return 1
    if not negative(max_qty):
        return None
    low, high = 1, max_qty
    while low + 1 < high:
        middle = (low + high) // 2
        if negative(middle):
            high = middle
        else:
            low = middle
    return high


def _curve(*, current_qty: int, gross_bps: Decimal | None, max_qty: int,
           mid_price: Decimal, bid_price: Decimal, ask_price: Decimal, adv: Decimal,
           annual_vol: Decimal, effect: PositionEffect, params: VerifyParams,
           borrow_terms: _BorrowTerms | None, time_horizon: str) -> tuple[CostSensitivityPoint, ...]:
    if max_qty < 1:
        return ()
    quantities = {min(current_qty, max_qty)}
    for fraction in params.curve_participation_points:
        qty = int((adv * fraction / mid_price).to_integral_value(rounding=ROUND_FLOOR))
        if 1 <= qty <= max_qty:
            quantities.add(qty)
    points: list[CostSensitivityPoint] = []
    for qty in sorted(quantities):
        gate = _components(
            qty=qty, mid_price=mid_price, bid_price=bid_price, ask_price=ask_price,
            adv=adv, annual_vol=annual_vol, effect=effect, params=params,
            impact_y=params.impact_coefficient_y, borrow_terms=borrow_terms,
            time_horizon=time_horizon,
        )
        low = _components(
            qty=qty, mid_price=mid_price, bid_price=bid_price, ask_price=ask_price,
            adv=adv, annual_vol=annual_vol, effect=effect, params=params,
            impact_y=params.sensitivity_low_y, borrow_terms=borrow_terms,
            time_horizon=time_horizon,
        )
        notional = (mid_price * Decimal(qty)).quantize(_MONEY_QUANTUM)
        participation = (mid_price * Decimal(qty) / adv).quantize(_PARTICIPATION_QUANTUM)
        net = None if gross_bps is None else _qbps(gross_bps - gate.total_bps)
        points.append(CostSensitivityPoint(
            qty=qty, notional=notional, adv_participation_rate=participation,
            low_impact_cost_bps=low.total_bps, gate_cost_bps=gate.total_bps,
            net_return_bps=net,
        ))
    return tuple(points)


def verify_proposal(proposal: dict, book: BookSnapshot, params: VerifyParams, *,
                    metrics: MarketImpactInputs | None = None,
                    position_effect: PositionEffect | None = None,
                    expected_edge: EdgeEstimate | None = None,
                    borrow_quote: BorrowQuote | None = None,
                    now_ns: int | None = None) -> QuantVerdict:
    """Price one proposal from injected L1/ADV/volatility/edge/borrow inputs."""
    symbol = str(proposal.get("symbol", book.symbol))
    side_raw = str(proposal.get("side", ""))
    ts = now_ns if now_ns is not None else book.ts
    effect = _effect_for(proposal, position_effect)
    if side_raw not in {"buy", "sell"} or effect is None:
        return _empty_verdict(
            symbol=symbol, side=side_raw, effect=effect, qty=0,
            book_seq=book.seq, ts=ts, reason=f"unknown side/effect {side_raw!r}",
        )
    expected_side = {
        PositionEffect.OPEN_LONG: "buy", PositionEffect.CLOSE_SHORT: "buy",
        PositionEffect.OPEN_SHORT: "sell", PositionEffect.CLOSE_LONG: "sell",
    }[effect]
    if side_raw != expected_side:
        return _empty_verdict(
            symbol=symbol, side=side_raw, effect=effect, qty=0,
            book_seq=book.seq, ts=ts,
            reason=f"side {side_raw!r} conflicts with position effect {effect.value!r}",
        )
    config_error = _validate_params(params)
    if config_error:
        return _empty_verdict(
            symbol=symbol, side=side_raw, effect=effect, qty=0,
            book_seq=book.seq, ts=ts, reason=config_error,
        )
    if str(book.symbol).upper() != symbol.upper():
        return _empty_verdict(
            symbol=symbol, side=side_raw, effect=effect, qty=0,
            book_seq=book.seq, ts=ts, reason="book symbol does not match the proposal",
        )
    if not book.bids or not book.asks:
        return _empty_verdict(
            symbol=symbol, side=side_raw, effect=effect, qty=0,
            book_seq=book.seq, ts=ts, reason="one-sided or empty L1 book",
        )
    if now_ns is not None:
        if book.ts > now_ns:
            return _empty_verdict(
                symbol=symbol, side=side_raw, effect=effect, qty=0,
                book_seq=book.seq, ts=ts, reason="book snapshot is future-dated",
            )
        if now_ns - book.ts > params.max_staleness_ns:
            return _empty_verdict(
                symbol=symbol, side=side_raw, effect=effect, qty=0,
                book_seq=book.seq, ts=ts,
                reason=f"book snapshot is {(now_ns - book.ts) / 1e9:.1f}s stale",
            )

    bid_ticks = book.bids[0].price_ticks
    ask_ticks = book.asks[0].price_ticks
    if bid_ticks <= 0 or ask_ticks <= bid_ticks:
        return _empty_verdict(
            symbol=symbol, side=side_raw, effect=effect, qty=0,
            book_seq=book.seq, ts=ts, reason="L1 bid/ask is crossed or invalid",
        )
    bid_price = params.scale.to_decimal(bid_ticks)
    ask_price = params.scale.to_decimal(ask_ticks)
    mid_price = (bid_price + ask_price) / Decimal("2")
    qty = proposal_quantity(proposal, mid_price)
    if qty is None:
        return _empty_verdict(
            symbol=symbol, side=side_raw, effect=effect, qty=0,
            book_seq=book.seq, ts=ts,
            reason="proposal carries neither a usable whole quantity nor notional",
        )

    adv, annual_vol, metric_error = _validate_metrics(metrics, symbol=symbol, params=params)
    if metric_error:
        return _empty_verdict(
            symbol=symbol, side=side_raw, effect=effect, qty=qty,
            book_seq=book.seq, ts=ts, reason=metric_error,
        )

    borrow_terms = None
    borrow_state = "not_applicable"
    borrow_source_id = None
    if effect.is_short_entry:
        borrow_terms, borrow_error = _validate_borrow(
            borrow_quote, params=params, now_ns=ts
        )
        if borrow_error:
            missing = _empty_verdict(
                symbol=symbol, side=side_raw, effect=effect, qty=qty,
                book_seq=book.seq, ts=ts, reason=borrow_error,
            )
            return replace(missing, borrow_state="unavailable")
        borrow_state = "verified"
        borrow_source_id = borrow_terms.source_id

    try:
        holding_seconds(str(proposal.get("time_horizon", "")))
    except ValueError as exc:
        return _empty_verdict(
            symbol=symbol, side=side_raw, effect=effect, qty=qty,
            book_seq=book.seq, ts=ts, reason=str(exc),
        )

    components = _components(
        qty=qty, mid_price=mid_price, bid_price=bid_price, ask_price=ask_price,
        adv=adv, annual_vol=annual_vol, effect=effect, params=params,
        impact_y=params.impact_coefficient_y, borrow_terms=borrow_terms,
        time_horizon=str(proposal.get("time_horizon", "")),
    )
    raw_participation = mid_price * Decimal(qty) / adv
    participation = raw_participation.quantize(_PARTICIPATION_QUANTUM)
    reasons: list[str] = []

    if raw_participation > params.max_adv_participation:
        verdict = Verdict.UNECONOMIC
        reasons.append(
            f"ADV participation {participation} exceeds the hard model-validity limit "
            f"{params.max_adv_participation}"
        )
    elif components.total_bps >= params.uneconomic_cost_bps:
        verdict = Verdict.UNECONOMIC
        reasons.append(
            f"expected all-in cost {components.total_bps}bps is at or above the "
            f"uneconomic threshold {params.uneconomic_cost_bps}bps"
        )
    elif components.total_bps >= params.marginal_cost_bps:
        verdict = Verdict.MARGINAL
        reasons.append(
            f"expected all-in cost {components.total_bps}bps is at or above the "
            f"marginal threshold {params.marginal_cost_bps}bps"
        )
    else:
        verdict = Verdict.OK

    gross_bps = None
    net_bps = None
    ratio = None
    edge_state = "not_applicable" if not effect.is_opening else "unavailable"
    edge_source_id = None
    edge_evidence_ref = None
    edge_error = None
    if effect.is_opening:
        gross_bps, edge_error = _validate_edge(
            expected_edge, proposal=proposal, params=params, now_ns=ts
        )
        if gross_bps is not None:
            edge_source_id = str(expected_edge.source_id)
            edge_evidence_ref = str(expected_edge.evidence_ref)
            net_bps = _qbps(gross_bps - components.total_bps)
            if components.total_bps > 0:
                ratio = (net_bps / components.total_bps).quantize(Decimal("0.01"))
            sufficient = net_bps >= params.min_net_cost_multiple * components.total_bps
            edge_state = "sufficient" if sufficient else "insufficient"
            if not sufficient:
                reasons.append(
                    f"expected net return {net_bps}bps is below "
                    f"{params.min_net_cost_multiple}x all-in cost "
                    f"{components.total_bps}bps"
                )
        else:
            reasons.append(f"{edge_error}; relative net-edge gate is unavailable")

    max_qty = _max_model_qty(adv=adv, mid_price=mid_price, params=params)
    curve = _curve(
        current_qty=qty, gross_bps=gross_bps, max_qty=max_qty,
        mid_price=mid_price, bid_price=bid_price, ask_price=ask_price,
        adv=adv, annual_vol=annual_vol, effect=effect, params=params,
        borrow_terms=borrow_terms, time_horizon=str(proposal.get("time_horizon", "")),
    )
    first_negative = None
    if gross_bps is not None and effect.is_opening:
        first_negative = _first_negative_qty(
            gross_bps=gross_bps, max_qty=max_qty, mid_price=mid_price,
            bid_price=bid_price, ask_price=ask_price, adv=adv,
            annual_vol=annual_vol, effect=effect, params=params,
            borrow_terms=borrow_terms,
            time_horizon=str(proposal.get("time_horizon", "")),
        )
    first_notional = (
        None if first_negative is None
        else (mid_price * Decimal(first_negative)).quantize(_MONEY_QUANTUM)
    )
    return QuantVerdict(
        symbol=symbol, side=side_raw, qty=qty, position_effect=effect.value,
        verdict=verdict,
        expected_spread_bps=components.spread_bps,
        expected_impact_bps=components.impact_bps,
        expected_fee_bps=components.fee_bps,
        expected_borrow_bps=components.borrow_bps,
        expected_cost_bps=components.total_bps,
        adv_participation_rate=participation,
        borrow_state=borrow_state, borrow_source_id=borrow_source_id,
        net_edge_state=edge_state, edge_source_id=edge_source_id,
        edge_evidence_ref=edge_evidence_ref,
        expected_gross_return_bps=gross_bps,
        expected_net_return_bps=net_bps,
        net_cost_multiple=ratio,
        min_net_cost_multiple=params.min_net_cost_multiple,
        cost_curve=curve,
        first_negative_qty=first_negative,
        first_negative_notional=first_notional,
        max_model_qty=max_qty,
        reasons=tuple(reasons), book_seq=book.seq, computed_at_ns=ts,
        metrics_as_of=metrics.as_of.isoformat() if metrics is not None else None,
    )
