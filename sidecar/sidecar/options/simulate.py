"""Synthetic defined-risk book on daily underlying closes.

There is no historical options tape in this tree. Each premium is a Black-Scholes
price with zero rates, and the volatility input is the trailing 60-session realized
vol of the same underlying. That is a stand-in for implied vol, not an OPRA print.
Fills sell the bid and buy the ask. The signal is computed at the close and the
structure is filled at the next session's close.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from sidecar.jsonutil import sha256_canonical
from sidecar.options.config import (
    ALLOWLIST,
    CONTRACT_MULTIPLIER,
    MIN_CREDIT,
    STARTING_EQUITY,
    StrategyConfig,
)
from sidecar.options.pricing import bs_delta, bs_price, rolling_realized_vol, strike_for_delta
from sidecar.options.risk import Leg, OptionsRiskError, admit_spread, max_loss_dollars
from sidecar.options.structures import leg_market, occ_symbol


@dataclass
class _Position:
    symbol: str
    kind: str
    expiry: date
    legs: list[Leg]
    symbols: list[str]
    credit_dollars: Decimal
    entry_mid: Decimal
    max_loss: Decimal
    qty: int
    opened_index: int
    entry_sigma: float


def _markets(spot: float, legs: list[Leg], t_years: float, sigma: float):
    priced = []
    for leg in legs:
        try:
            mid = bs_price(spot, float(leg.strike), t_years, sigma, leg.right)
        except ValueError:
            return None
        market = leg_market(mid)
        if market is None:
            return None
        priced.append(market)
    return priced


def _t_years(expiry: date, day: date) -> float:
    return max((expiry - day).days, 1) / 365.0


def simulate_book(
    closes: dict[str, list[float]],
    dates: list[date],
    config: StrategyConfig,
    *,
    kill_file: Path | None = None,
) -> dict:
    if config.kind not in {"put_credit", "iron_condor"}:
        raise OptionsRiskError("kind")
    symbols = [symbol for symbol in ALLOWLIST if symbol in closes]
    ivs = {symbol: rolling_realized_vol(closes[symbol], config.iv_window) for symbol in symbols}
    rvs = {symbol: rolling_realized_vol(closes[symbol], config.rv_window) for symbol in symbols}
    start = config.iv_window + config.rank_window
    cash = STARTING_EQUITY
    equity_prev = STARTING_EQUITY
    positions: list[_Position] = []
    pending: list[dict] = []
    returns: list[float] = []
    spy_returns: list[float] = []
    return_dates: list[str] = []
    fills: list[dict] = []
    reasons = {"take_profit": 0, "stop": 0, "dte": 0, "final": 0}
    n_opens = 0
    n_signals = 0
    n_skips = 0

    def _fill(symbol: str, side: str, mid: Decimal, price: Decimal, qty_shares: int) -> None:
        fills.append(
            {
                "symbol": symbol,
                "side": side,
                "qty": str(qty_shares),
                "decision_price": format(mid, "f"),
                "arrival_mid": format(mid, "f"),
                "fill_price": format(price, "f"),
                "fee": "0",
                "venue_label": "synthetic_black_scholes",
            }
        )

    def _mark(pos: _Position, index: int) -> Decimal | None:
        sigma = ivs[pos.symbol][index] or pos.entry_sigma
        if sigma is None or sigma <= 0:
            return None
        priced = _markets(closes[pos.symbol][index], pos.legs, _t_years(pos.expiry, dates[index]), sigma)
        if priced is None:
            return None
        debit = Decimal("0")
        for leg, (_bid, _ask, mid) in zip(pos.legs, priced, strict=True):
            debit += mid if leg.side == "sell" else -mid
        return debit * CONTRACT_MULTIPLIER * Decimal(pos.qty)

    for index, day in enumerate(dates):
        still_pending = []
        for signal in pending:
            if signal["fill_index"] != index:
                still_pending.append(signal)
                continue
            opened = _try_fill(signal, index, day, positions, kill_file, config, closes, ivs)
            if opened is None:
                n_skips += 1
                continue
            positions.append(opened[0])
            cash += opened[0].credit_dollars
            n_opens += 1
            for fill in opened[1]:
                _fill(*fill)
        pending = still_pending

        kept: list[_Position] = []
        for pos in positions:
            sigma = ivs[pos.symbol][index] or pos.entry_sigma
            priced = None if sigma is None else _markets(
                closes[pos.symbol][index], pos.legs, _t_years(pos.expiry, day), sigma
            )
            dte = (pos.expiry - day).days
            reason = None
            mid_debit = None
            if priced is not None:
                mid_debit = Decimal("0")
                for leg, (_bid, _ask, mid) in zip(pos.legs, priced, strict=True):
                    mid_debit += mid if leg.side == "sell" else -mid
            if dte <= config.close_dte:
                reason = "dte"
            elif index == len(dates) - 1:
                reason = "final"
            elif (
                priced is not None
                and pos.opened_index < index
                and mid_debit is not None
                and mid_debit <= pos.entry_mid * config.tp_fraction
            ):
                reason = "take_profit"
            elif (
                priced is not None
                and pos.opened_index < index
                and mid_debit is not None
                and mid_debit >= pos.entry_mid * config.stop_multiple
            ):
                reason = "stop"
            if reason is None or priced is None:
                if reason is not None and priced is None:
                    n_skips += 1
                kept.append(pos)
                continue
            debit = Decimal("0")
            share_qty = 100 * pos.qty
            for leg, (bid, ask, mid) in zip(pos.legs, priced, strict=True):
                if leg.side == "sell":
                    debit += ask
                    _fill(leg.underlying, "buy", mid, ask, share_qty)
                else:
                    debit -= bid
                    _fill(leg.underlying, "sell", mid, bid, share_qty)
            cash -= debit * CONTRACT_MULTIPLIER * Decimal(pos.qty)
            reasons[reason] += 1
        positions = kept

        if index + 1 < len(dates) and index >= start:
            for symbol in symbols:
                if any(pos.symbol == symbol for pos in positions) or any(
                    row["symbol"] == symbol for row in pending
                ):
                    continue
                if len(positions) + len(pending) >= 5:
                    continue
                signal = _signal(
                    symbol=symbol,
                    index=index,
                    day=day,
                    closes=closes,
                    ivs=ivs,
                    rvs=rvs,
                    config=config,
                )
                if signal is None:
                    continue
                n_signals += 1
                pending.append(signal)

        liability = Decimal("0")
        marked = True
        for pos in positions:
            mark = _mark(pos, index)
            if mark is None:
                marked = False
                break
            liability += mark
        equity = cash - liability if marked else equity_prev
        if index >= start:
            if equity_prev > 0:
                returns.append(float((equity - equity_prev) / equity_prev))
            else:
                returns.append(0.0)
            if "SPY" in closes and index > 0 and closes["SPY"][index - 1] > 0:
                spy_returns.append(closes["SPY"][index] / closes["SPY"][index - 1] - 1.0)
            else:
                spy_returns.append(0.0)
            return_dates.append(day.isoformat())
        equity_prev = equity

    config_json = config.as_json()
    return {
        "name": config.name,
        "kind": config.kind,
        "config": config_json,
        "config_hash": sha256_canonical(config_json),
        "starting_equity": format(STARTING_EQUITY, "f"),
        "ending_equity": format(equity_prev, "f"),
        "n_opens": n_opens,
        "n_signals": n_signals,
        "n_skips": n_skips,
        "closes": reasons,
        "n_return_days": len(returns),
        "returns": returns,
        "spy_returns": spy_returns,
        "return_dates": return_dates,
        "fills": fills,
        "orders_submitted": 0,
    }


def _signal(*, symbol, index, day, closes, ivs, rvs, config: StrategyConfig) -> dict | None:
    iv = ivs[symbol][index]
    rv = rvs[symbol][index]
    if iv is None or rv is None:
        return None
    prior = ivs[symbol][index - config.rank_window : index]
    if len(prior) < config.rank_window or any(value is None for value in prior):
        return None
    rank = sum(1 for value in prior if value <= iv) / config.rank_window
    if Decimal(str(rank)) < config.iv_rank_min:
        return None
    if Decimal(str(iv - rv)) <= config.iv_rv_min:
        return None
    from sidecar.options.config import friday_expiry

    expiry = friday_expiry(day, config.target_dte)
    if expiry is None:
        return None
    tenor = (expiry - day).days / 365.0
    spot = closes[symbol][index]
    short_put = strike_for_delta(
        spot, tenor, iv, right="put", target_abs_delta=float(config.delta)
    )
    if short_put is None:
        return None
    try:
        put_delta = bs_delta(spot, float(short_put), tenor, iv, "put")
    except ValueError:
        return None
    if not (0.12 <= abs(put_delta) <= 0.30):
        return None
    long_put = short_put - int(config.width)
    if long_put <= 0:
        return None
    legs = [
        ("put", Decimal(short_put), "sell"),
        ("put", Decimal(long_put), "buy"),
    ]
    if config.kind == "iron_condor":
        short_call = strike_for_delta(
            spot, tenor, iv, right="call", target_abs_delta=float(config.delta)
        )
        if short_call is None:
            return None
        try:
            call_delta = bs_delta(spot, float(short_call), tenor, iv, "call")
        except ValueError:
            return None
        if not (0.12 <= call_delta <= 0.30):
            return None
        long_call = short_call + int(config.width)
        if long_call <= short_put:
            return None
        legs.extend(
            [
                ("call", Decimal(short_call), "sell"),
                ("call", Decimal(long_call), "buy"),
            ]
        )
    return {
        "symbol": symbol,
        "fill_index": index + 1,
        "expiry": expiry,
        "legs": legs,
        "signal_index": index,
    }


def _try_fill(signal, index, day, positions, kill_file, config: StrategyConfig, closes, ivs):
    symbol = signal["symbol"]
    sigma = ivs[symbol][index]
    if sigma is None or sigma <= 0 or not math.isfinite(sigma):
        return None
    spot = closes[symbol][index]
    expiry = signal["expiry"]
    legs = [
        Leg(symbol, expiry, right, strike, side) for right, strike, side in signal["legs"]
    ]
    priced = _markets(spot, legs, _t_years(expiry, day), sigma)
    if priced is None:
        return None
    credit = Decimal("0")
    mid_credit = Decimal("0")
    share_fills = []
    share_qty = 100 * config.qty
    symbols = []
    for leg, (bid, ask, mid) in zip(legs, priced, strict=True):
        symbols.append(occ_symbol(symbol, expiry, leg.right, leg.strike))
        if leg.side == "sell":
            credit += bid
            mid_credit += mid
            share_fills.append((symbol, "sell", mid, bid, share_qty))
        else:
            credit -= ask
            mid_credit -= mid
            share_fills.append((symbol, "buy", mid, ask, share_qty))
    if credit <= MIN_CREDIT or mid_credit <= 0:
        return None
    open_losses = [pos.max_loss for pos in positions]
    try:
        loss = max_loss_dollars(legs, credit, config.qty)
        admit_spread(loss, open_losses, kill_file)
    except OptionsRiskError:
        return None
    position = _Position(
        symbol=symbol,
        kind=config.kind,
        expiry=expiry,
        legs=legs,
        symbols=symbols,
        credit_dollars=credit * CONTRACT_MULTIPLIER * Decimal(config.qty),
        entry_mid=mid_credit,
        max_loss=loss,
        qty=config.qty,
        opened_index=index,
        entry_sigma=sigma,
    )
    return position, share_fills
