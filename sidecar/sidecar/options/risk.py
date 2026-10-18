"""Pure defined-risk checks. No network and no model calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from sidecar.options.config import (
    ALLOWLIST,
    CONTRACT_MULTIPLIER,
    MAX_LOSS_PER_TRADE,
    MAX_OPEN_SPREADS,
    MAX_TOTAL_LOSS,
    MIN_DTE,
    MAX_DTE,
)

_CLIENT_ID = re.compile(r"^sc[A-Za-z0-9]{1,46}$")
OPEN_INTENTS = frozenset({"buy_to_open", "sell_to_open"})
CLOSE_INTENTS = frozenset({"buy_to_close", "sell_to_close"})


class OptionsRiskError(ValueError):
    """A structure, limit, or order body is not allowed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Leg:
    underlying: str
    expiry: date
    right: str
    strike: Decimal
    side: str
    ratio: int = 1


def _group(legs: list[Leg]) -> dict[tuple, list[Leg]]:
    grouped: dict[tuple, list[Leg]] = {}
    for leg in legs:
        if leg.underlying not in ALLOWLIST:
            raise OptionsRiskError("underlying")
        if leg.right not in {"call", "put"}:
            raise OptionsRiskError("right")
        if leg.side not in {"buy", "sell"}:
            raise OptionsRiskError("side")
        if leg.ratio != 1:
            raise OptionsRiskError("ratio")
        if not isinstance(leg.strike, Decimal) or not leg.strike.is_finite() or leg.strike <= 0:
            raise OptionsRiskError("strike")
        grouped.setdefault((leg.underlying, leg.expiry, leg.right), []).append(leg)
    return grouped


def assert_defined_risk(legs: list[Leg]) -> None:
    """Reject a naked short. Each short needs a further-OTM long, same expiry and right."""
    if not legs:
        raise OptionsRiskError("no_legs")
    grouped = _group(legs)
    covered_any = False
    underlyings = {leg.underlying for leg in legs}
    expiries = {leg.expiry for leg in legs}
    if len(underlyings) != 1 or len(expiries) != 1:
        raise OptionsRiskError("mixed_structure")
    for (_underlying, _expiry, right), group in grouped.items():
        sells = [leg for leg in group if leg.side == "sell"]
        buys = [leg for leg in group if leg.side == "buy"]
        if not sells:
            continue
        if len(buys) < len(sells):
            raise OptionsRiskError("naked_short")
        for sell in sells:
            if right == "put":
                cover = [leg for leg in buys if leg.strike < sell.strike]
            else:
                cover = [leg for leg in buys if leg.strike > sell.strike]
            if not cover:
                raise OptionsRiskError("naked_short")
            buys.remove(cover[0])
            covered_any = True
    if not covered_any:
        raise OptionsRiskError("not_a_credit_structure")
    put_shorts = [leg.strike for leg in legs if leg.right == "put" and leg.side == "sell"]
    call_shorts = [leg.strike for leg in legs if leg.right == "call" and leg.side == "sell"]
    if put_shorts and call_shorts and max(put_shorts) >= min(call_shorts):
        raise OptionsRiskError("inverted")


def wing_width(legs: list[Leg]) -> Decimal:
    """Largest wing. An iron condor loses one side, so the wings are not added."""
    assert_defined_risk(legs)
    grouped = _group(legs)
    widths: list[Decimal] = []
    for (_underlying, _expiry, right), group in grouped.items():
        sells = [leg for leg in group if leg.side == "sell"]
        buys = [leg for leg in group if leg.side == "buy"]
        for sell in sells:
            if right == "put":
                cover = max(leg.strike for leg in buys if leg.strike < sell.strike)
                widths.append(sell.strike - cover)
            else:
                cover = min(leg.strike for leg in buys if leg.strike > sell.strike)
                widths.append(cover - sell.strike)
    if not widths or any(width <= 0 for width in widths):
        raise OptionsRiskError("width")
    return max(widths)


def max_loss_dollars(legs: list[Leg], net_credit_per_share: Decimal, qty: int) -> Decimal:
    if qty < 1:
        raise OptionsRiskError("qty")
    if not isinstance(net_credit_per_share, Decimal) or not net_credit_per_share.is_finite():
        raise OptionsRiskError("credit")
    if net_credit_per_share <= 0:
        raise OptionsRiskError("credit")
    width = wing_width(legs)
    loss = (width - net_credit_per_share) * CONTRACT_MULTIPLIER * Decimal(qty)
    if not loss.is_finite() or loss <= 0:
        raise OptionsRiskError("max_loss")
    if loss > MAX_LOSS_PER_TRADE:
        raise OptionsRiskError("max_loss_per_trade")
    return loss


def admit_spread(
    new_loss: Decimal,
    open_losses: list[Decimal],
    kill_file: Path | None,
) -> None:
    if kill_file is not None and kill_file.exists():
        raise OptionsRiskError("kill_switch")
    if not new_loss.is_finite() or new_loss <= 0 or new_loss > MAX_LOSS_PER_TRADE:
        raise OptionsRiskError("max_loss_per_trade")
    if len(open_losses) >= MAX_OPEN_SPREADS:
        raise OptionsRiskError("max_open_spreads")
    total = sum(open_losses, Decimal("0")) + new_loss
    if total > MAX_TOTAL_LOSS:
        raise OptionsRiskError("total_max_loss")


def assert_entry_dte(dte: int) -> None:
    if dte < MIN_DTE or dte > MAX_DTE:
        raise OptionsRiskError("dte")


def opening_limit_price(net_credit_per_share: Decimal) -> Decimal:
    """Alpaca treats a negative multi-leg limit as a credit."""
    from decimal import ROUND_DOWN

    price = (-net_credit_per_share).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if price >= 0:
        raise OptionsRiskError("credit_limit")
    return price


def closing_limit_price(net_debit_per_share: Decimal) -> Decimal:
    from decimal import ROUND_UP

    if net_debit_per_share <= 0:
        raise OptionsRiskError("debit_limit")
    price = net_debit_per_share.quantize(Decimal("0.01"), rounding=ROUND_UP)
    if price <= 0:
        raise OptionsRiskError("debit_limit")
    return price


def validate_mleg_body(body: dict, *, opening: bool) -> None:
    if not isinstance(body, dict):
        raise OptionsRiskError("order_body")
    if body.get("order_class") != "mleg":
        raise OptionsRiskError("order_class")
    if body.get("type") != "limit":
        raise OptionsRiskError("limit_only")
    if body.get("time_in_force") != "day":
        raise OptionsRiskError("time_in_force")
    if "stop_price" in body or "trail_price" in body:
        raise OptionsRiskError("stop_not_allowed")
    try:
        limit = Decimal(str(body.get("limit_price")))
        qty = Decimal(str(body.get("qty")))
    except Exception as exc:
        raise OptionsRiskError("price") from exc
    if not limit.is_finite() or not qty.is_finite() or qty != Decimal(int(qty)) or qty < 1:
        raise OptionsRiskError("price")
    if opening and limit >= 0:
        raise OptionsRiskError("credit_limit")
    if not opening and limit <= 0:
        raise OptionsRiskError("debit_limit")
    client_id = str(body.get("client_order_id", ""))
    if _CLIENT_ID.fullmatch(client_id) is None:
        raise OptionsRiskError("client_order_id")
    legs = body.get("legs")
    if not isinstance(legs, list) or len(legs) not in {2, 4}:
        raise OptionsRiskError("legs")
    allowed = OPEN_INTENTS if opening else CLOSE_INTENTS
    buy_to_close = False
    for leg in legs:
        if not isinstance(leg, dict):
            raise OptionsRiskError("legs")
        intent = str(leg.get("position_intent", ""))
        if intent not in allowed:
            raise OptionsRiskError("position_intent")
        if intent == "buy_to_close":
            buy_to_close = True
        if str(leg.get("side", "")) not in {"buy", "sell"}:
            raise OptionsRiskError("side")
        if Decimal(str(leg.get("ratio_qty", "0"))) != 1:
            raise OptionsRiskError("ratio")
        if not str(leg.get("symbol", "")).strip():
            raise OptionsRiskError("symbol")
    if not opening and not buy_to_close:
        raise OptionsRiskError("close_must_buy_back")
