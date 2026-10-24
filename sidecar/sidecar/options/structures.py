"""OCC symbols and multi-leg order bodies for credit structures."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sidecar.options.config import SPREAD_FLOOR, SPREAD_FRAC
from sidecar.options.risk import (
    Leg,
    closing_limit_price,
    opening_limit_price,
    validate_mleg_body,
)

_OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def occ_symbol(underlying: str, expiry: date, right: str, strike: Decimal) -> str:
    if not re.fullmatch(r"[A-Z]{1,6}", underlying):
        raise ValueError("underlying")
    flag = {"call": "C", "put": "P"}.get(right)
    if flag is None:
        raise ValueError("right")
    millis = int((strike * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    if millis < 0 or millis > 99_999_999:
        raise ValueError("strike")
    return f"{underlying}{expiry:%y%m%d}{flag}{millis:08d}"


def parse_occ(symbol: str) -> tuple[str, date, str, Decimal] | None:
    match = _OCC.fullmatch(symbol or "")
    if match is None:
        return None
    underlying, yymmdd, flag, strike_txt = match.groups()
    year = 2000 + int(yymmdd[:2])
    expiry = date(year, int(yymmdd[2:4]), int(yymmdd[4:6]))
    right = "call" if flag == "C" else "put"
    strike = Decimal(int(strike_txt)) / Decimal(1000)
    return underlying, expiry, right, strike


def client_order_id(*, day: str, underlying: str, kind: str, action: str, symbols: list[str]) -> str:
    payload = "|".join([day, underlying, kind, action, *symbols])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    text = f"sc{day.replace('-', '')}{underlying}{action[:1].upper()}{digest}"
    text = re.sub(r"[^A-Za-z0-9]", "", text)[:48]
    if not text.startswith("sc") or len(text) < 3:
        raise ValueError("client order id failed its own alphabet check")
    return text


def leg_market(mid: float) -> tuple[Decimal, Decimal, Decimal] | None:
    """Bid, ask, and penny mid. Full spread is max($0.05, 8% of mid)."""
    if mid is None:
        return None
    try:
        raw = Decimal(str(mid))
    except Exception:
        return None
    if not raw.is_finite() or raw <= 0:
        return None
    mid_penny = raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if mid_penny <= 0:
        mid_penny = Decimal("0.01")
    fractional = (mid_penny * SPREAD_FRAC).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    full = max(SPREAD_FLOOR, fractional)
    bid = (mid_penny - full / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ask = (mid_penny + full / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if bid < Decimal("0.01"):
        bid = Decimal("0.01")
    if ask <= bid:
        ask = bid + Decimal("0.01")
    return bid, ask, mid_penny


def order_body(
    *,
    legs: list[Leg],
    symbols: list[str],
    limit: Decimal,
    qty: int,
    client_id: str,
    opening: bool,
) -> dict:
    if len(legs) != len(symbols):
        raise ValueError("leg symbols do not match")
    payload_legs = []
    for leg, symbol in zip(legs, symbols, strict=True):
        if opening:
            intent = "sell_to_open" if leg.side == "sell" else "buy_to_open"
            side = leg.side
        else:
            # Close by reversing the opening side.
            side = "buy" if leg.side == "sell" else "sell"
            intent = "buy_to_close" if side == "buy" else "sell_to_close"
        payload_legs.append(
            {
                "symbol": symbol,
                "side": side,
                "position_intent": intent,
                "ratio_qty": "1",
            }
        )
    body = {
        "order_class": "mleg",
        "qty": str(int(qty)),
        "type": "limit",
        "limit_price": format(limit, "f"),
        "time_in_force": "day",
        "client_order_id": client_id,
        "legs": payload_legs,
    }
    validate_mleg_body(body, opening=opening)
    return body


def opening_order(legs: list[Leg], symbols: list[str], credit: Decimal, qty: int, client_id: str) -> dict:
    return order_body(
        legs=legs,
        symbols=symbols,
        limit=opening_limit_price(credit),
        qty=qty,
        client_id=client_id,
        opening=True,
    )


def closing_order(legs: list[Leg], symbols: list[str], debit: Decimal, qty: int, client_id: str) -> dict:
    return order_body(
        legs=legs,
        symbols=symbols,
        limit=closing_limit_price(debit),
        qty=qty,
        client_id=client_id,
        opening=False,
    )
