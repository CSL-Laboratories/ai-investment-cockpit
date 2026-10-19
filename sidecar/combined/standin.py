"""Deterministic stand-in for the cockpit's LLM trade proposal.

The live proposal is a schema-validated model output. This module does not call a model
and does not invent thesis text. It only turns prior closes into a long-only target
notional the risk gate can accept or refuse.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from collections.abc import Sequence

from sidecar.sizer.kelly import SizerError, suggest_notional

STAND_IN_ID = "combined.deterministic_stand_in.v1"
TREND_LOOKBACK = 60
VOL_LOOKBACK = 20
TARGET_VOL = Decimal("0.01")
KELLY_FRAC = Decimal("0.25")
MAX_WEIGHT = Decimal("1")
SHARE_QUANTUM = Decimal("0.0001")
MIN_TRADE = Decimal("1")


def trailing_return(closes: Sequence[Decimal], lookback: int) -> Decimal | None:
    """Close-to-close change ending at the last close. None until the window exists."""
    if lookback < 1 or len(closes) <= lookback:
        return None
    base = closes[-1 - lookback]
    if base <= 0:
        return None
    return closes[-1] / base - 1


def daily_returns(closes: Sequence[Decimal], lookback: int) -> list[Decimal] | None:
    if lookback < 2 or len(closes) <= lookback:
        return None
    window = closes[-(lookback + 1) :]
    out = []
    for prev, nxt in zip(window, window[1:], strict=False):
        if prev <= 0:
            return None
        out.append(nxt / prev - 1)
    return out


def mean_std(values: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    count = Decimal(len(values))
    mean = sum(values, Decimal(0)) / count
    variance = sum((item - mean) ** 2 for item in values) / (count - 1)
    return mean, variance.sqrt()


def target_notional(
    closes: Sequence[Decimal],
    *,
    variant: str,
    cap: Decimal,
    equity: Decimal,
) -> Decimal | None:
    """Desired long notional at this close. None means the window is still warming up.

    ``cockpit`` and ``tca`` use the sign of the trailing 60-session return and the
    shipped dollar cap. ``sizer`` replaces that size with vol-targeted fractional Kelly,
    still capped. ``combined`` averages the two notionals. Kronos and the options
    overlay are not inside this number: they have no per-symbol proposal the equity
    gate can admit.
    """
    if variant not in {"cockpit", "sizer", "combined", "tca"}:
        raise ValueError(f"unknown variant {variant}")
    trend = trailing_return(closes, TREND_LOOKBACK)
    series = daily_returns(closes, VOL_LOOKBACK)
    if trend is None or series is None:
        return None
    cockpit = cap if trend > 0 else Decimal(0)
    edge, vol = mean_std(series)
    try:
        sized = suggest_notional(
            edge,
            vol,
            TARGET_VOL,
            KELLY_FRAC,
            cap,
            equity,
            max_weight=MAX_WEIGHT,
        ).suggested
    except SizerError:
        sized = Decimal(0)
    if variant in {"cockpit", "tca"}:
        return cockpit
    if variant == "sizer":
        return sized
    return (cockpit + sized) / 2


def order_quantity(notional: Decimal, price: Decimal) -> Decimal:
    if price <= 0:
        return Decimal(0)
    return (notional / price).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)


def proposal_for(
    *,
    symbol: str,
    closes: Sequence[Decimal],
    held_shares: Decimal,
    price: Decimal,
    variant: str,
    cap: Decimal,
    equity: Decimal,
) -> dict | None:
    """A limit proposal, or None when there is nothing to change.

    The dict is the risk engine's input. It is not a model TradeProposal: there is no
    thesis, no source citation, and ``generated_by`` names this stand-in.
    """
    target = target_notional(closes, variant=variant, cap=cap, equity=equity)
    if target is None or price <= 0:
        return None
    current = held_shares * price
    delta = target - current
    if abs(delta) < MIN_TRADE:
        return None
    if delta > 0:
        qty = order_quantity(delta, price)
        if qty <= 0:
            return None
        side = "buy"
    else:
        qty = order_quantity(-delta, price)
        qty = min(qty, held_shares)
        if qty <= 0:
            return None
        side = "sell"
    return {
        "symbol": symbol,
        "side": side,
        "asset_type": "equity",
        "order_type": "limit",
        "quantity": qty,
        "notional": qty * price,
        "time_horizon": "short",
        "sizing_basis": "fixed-notional",
        "generated_by": {"runtime": "combined", "model": STAND_IN_ID, "session_id": None},
    }
