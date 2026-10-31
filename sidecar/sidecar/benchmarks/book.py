"""Shared daily book for the benchmark ports.

Signal dates follow the source. Fills follow the sidecar harness: the decision
is the adjusted close, and the trade is the next session's adjusted open, with
1 bp one-way. QuantJourney's weight engine marks the rebalance at that close,
so the overnight gap here is still on the old shares. That fill lag is stated
on every comparison. Nothing in this module submits an order.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from sidecar.benchmarks.o17 import (
    LOOKBACK as O17_LOOKBACK,
    PUBLISHED_SYMBOLS as O17_SYMBOLS,
    STRATEGY_ID as O17_ID,
    orders_for_event,
)
from sidecar.benchmarks.w18 import (
    PUBLISHED_SYMBOLS as W18_SYMBOLS,
    STRATEGY_ID as W18_ID,
    target_weights as w18_targets,
)
from sidecar.benchmarks.w25 import (
    PROXY_SYMBOLS,
    STRATEGY_ID as W25_ID,
    long_only,
    target_weights as w25_targets,
)
from sidecar.harness.backtest import Bar

BENCHMARK_NAMES = ("w18", "o17", "w25")
COST_BPS = 1.0
STARTING_EQUITY = 100_000.0


def business_month_end_flags(dates: list[date]) -> list[bool]:
    """Business month-end stamps, snapped back onto the sample, plus the first bar.

    The stamp is the last weekday of the month. QuantJourney asks pandas for
    ``BME`` and then takes the latest sample date at or before that stamp.
    """
    flags = [False] * len(dates)
    if not dates:
        return flags
    flags[0] = True
    start = dates[0]
    end = dates[-1]
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        if month == 12:
            cursor = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            cursor = date(year, month + 1, 1) - timedelta(days=1)
        while cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
        if start <= cursor <= end:
            pos = _last_on_or_before(dates, cursor)
            if pos >= 0:
                flags[pos] = True
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return flags


def _last_on_or_before(dates: list[date], stamp: date) -> int:
    lo = 0
    hi = len(dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if dates[mid] <= stamp:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def aligned_panel(
    grouped: dict[str, list[Bar]], symbols: tuple[str, ...]
) -> tuple[list[date], dict[str, list[float]], dict[str, list[float]]]:
    chosen = tuple(symbol for symbol in symbols if symbol in grouped)
    if not chosen:
        raise ValueError("benchmark panel has no symbols in the bar file")
    by_day = {symbol: {bar.day: bar for bar in grouped[symbol]} for symbol in chosen}
    dates = sorted(set.intersection(*(set(rows) for rows in by_day.values())))
    if len(dates) < 3:
        raise ValueError("benchmark panel needs at least three common sessions")
    closes = {symbol: [float(by_day[symbol][day].close_adj) for day in dates] for symbol in chosen}
    opens = {symbol: [float(by_day[symbol][day].open_adj) for day in dates] for symbol in chosen}
    return dates, closes, opens


def _fill(
    *,
    symbol: str,
    trade: float,
    price: float,
    decision: float,
    fee: float,
) -> dict | None:
    if abs(trade) <= 0.01 or price <= 0 or decision <= 0:
        return None
    qty = abs(trade) / price
    if qty <= 0:
        return None
    return {
        "symbol": symbol,
        "side": "buy" if trade > 0 else "sell",
        "qty": format(Decimal(str(qty)), "f"),
        "decision_price": format(Decimal(str(decision)), "f"),
        "arrival_mid": format(Decimal(str(price)), "f"),
        "fill_price": format(Decimal(str(price)), "f"),
        "fee": format(Decimal(str(fee)), "f"),
        "venue_label": "simulated_next_open",
    }


def run_weight_book(
    dates: list[date],
    closes: dict[str, list[float]],
    opens: dict[str, list[float]],
    targets: list[dict[str, float]],
    mask: list[bool],
    *,
    cost_bps: float = COST_BPS,
    starting_equity: float = STARTING_EQUITY,
) -> dict:
    """Hold shares between rebalances. Trade the target at the next open."""
    symbols = list(closes)
    cash = starting_equity
    shares = {symbol: 0.0 for symbol in symbols}
    pending: dict[str, float] | None = None
    pending_nav = starting_equity
    pending_decision: dict[str, float] = {}
    curve: list[tuple[date, float]] = []
    fills: list[dict] = []
    rebalances = 0
    gross_samples: list[float] = []
    cost_rate = cost_bps / 10_000.0
    for index, day in enumerate(dates):
        if pending is not None:
            for symbol in symbols:
                price = opens[symbol][index]
                target_value = pending.get(symbol, 0.0) * pending_nav
                current_value = shares[symbol] * price
                trade = target_value - current_value
                fee = abs(trade) * cost_rate
                row = _fill(
                    symbol=symbol,
                    trade=trade,
                    price=price,
                    decision=pending_decision[symbol],
                    fee=fee,
                )
                if row is not None:
                    fills.append(row)
                cash -= trade + fee
                shares[symbol] = 0.0 if price == 0 else target_value / price
            pending = None
        equity = cash + sum(shares[symbol] * closes[symbol][index] for symbol in symbols)
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError(f"benchmark equity became non-positive on {day.isoformat()}")
        curve.append((day, equity))
        gross_samples.append(
            sum(abs(shares[symbol] * closes[symbol][index]) for symbol in symbols) / equity
        )
        if mask[index] and index + 1 < len(dates):
            pending = dict(targets[index])
            pending_nav = equity
            pending_decision = {symbol: closes[symbol][index] for symbol in symbols}
            rebalances += 1
    last = targets[-1] if targets else {}
    return _pack(
        symbols=symbols,
        curve=curve,
        fills=fills,
        shares=shares,
        cash=cash,
        rebalances=rebalances,
        gross_samples=gross_samples,
        last_targets=last,
        orders_submitted=0,
    )


def run_monthly_rotation(
    dates: list[date],
    closes: dict[str, list[float]],
    opens: dict[str, list[float]],
    *,
    cost_bps: float = COST_BPS,
    starting_equity: float = STARTING_EQUITY,
    lookback: int = O17_LOOKBACK,
) -> dict:
    """O17 orders: decided at the close of the first bar of a month, filled next open."""
    symbols = list(closes)
    cash = starting_equity
    shares = {symbol: 0 for symbol in symbols}
    pending: list[tuple[str, str, int]] = []
    pending_decision: dict[str, float] = {}
    last_month: tuple[int, int] | None = None
    curve: list[tuple[date, float]] = []
    fills: list[dict] = []
    events = 0
    gross_samples: list[float] = []
    cost_rate = cost_bps / 10_000.0
    for index, day in enumerate(dates):
        if pending:
            sells = [item for item in pending if item[0] == "sell"]
            buys = [item for item in pending if item[0] == "buy"]
            for _side, symbol, qty in sells + buys:
                price = opens[symbol][index]
                signed = qty if _side == "buy" else -qty
                trade = signed * price
                fee = abs(trade) * cost_rate
                if _side == "buy" and trade + fee > cash and price > 0:
                    affordable = int(cash / (price * (1.0 + cost_rate)))
                    qty = max(0, affordable)
                    signed = qty
                    trade = signed * price
                    fee = abs(trade) * cost_rate
                if qty <= 0:
                    continue
                row = _fill(
                    symbol=symbol,
                    trade=trade,
                    price=price,
                    decision=pending_decision[symbol],
                    fee=fee,
                )
                if row is not None:
                    fills.append(row)
                cash -= trade + fee
                shares[symbol] = shares[symbol] + signed
            pending = []
        equity = cash + sum(shares[symbol] * closes[symbol][index] for symbol in symbols)
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError(f"benchmark equity became non-positive on {day.isoformat()}")
        curve.append((day, equity))
        gross_samples.append(
            sum(abs(shares[symbol] * closes[symbol][index]) for symbol in symbols) / equity
        )
        month = (day.year, day.month)
        if month == last_month:
            continue
        last_month = month
        if index < lookback or index + 1 >= len(dates):
            continue
        prices = {symbol: closes[symbol][index] for symbol in symbols}
        pending = orders_for_event(
            closes,
            index,
            shares,
            equity,
            prices,
            lookback=lookback,
        )
        pending_decision = prices
        if pending:
            events += 1
    held = _weights_from_shares(shares, closes, len(dates) - 1, curve[-1][1] if curve else starting_equity)
    return _pack(
        symbols=symbols,
        curve=curve,
        fills=fills,
        shares={symbol: float(qty) for symbol, qty in shares.items()},
        cash=cash,
        rebalances=events,
        gross_samples=gross_samples,
        last_targets=held,
        orders_submitted=0,
    )


def _weights_from_shares(
    shares: dict[str, int], closes: dict[str, list[float]], index: int, equity: float
) -> dict[str, float]:
    if equity <= 0:
        return {symbol: 0.0 for symbol in shares}
    return {symbol: shares[symbol] * closes[symbol][index] / equity for symbol in shares}


def _pack(
    *,
    symbols: list[str],
    curve: list[tuple[date, float]],
    fills: list[dict],
    shares: dict[str, float],
    cash: float,
    rebalances: int,
    gross_samples: list[float],
    last_targets: dict[str, float],
    orders_submitted: int,
) -> dict:
    decimal_curve = [(day, Decimal(str(value))) for day, value in curve]
    returns = []
    for prev, nxt in zip(decimal_curve, decimal_curve[1:], strict=False):
        returns.append((nxt[0], (nxt[1] - prev[1]) / prev[1]))
    start = 0
    while start < len(returns) and returns[start][1] == 0:
        start += 1
    mean_gross = (
        sum(gross_samples) / len(gross_samples) if gross_samples else 0.0
    )
    return {
        "symbols": symbols,
        "curve": decimal_curve,
        "returns": returns[start:],
        "fills": fills,
        "bindings": Counter(),
        "rebalances": rebalances,
        "mean_gross": Decimal(str(mean_gross)),
        "shares": {symbol: Decimal(str(qty)) for symbol, qty in shares.items()},
        "cash": Decimal(str(cash)),
        "last_targets": {symbol: format(value, ".10g") for symbol, value in last_targets.items()},
        "orders_submitted": orders_submitted,
    }


def symbols_for(name: str, available: tuple[str, ...]) -> tuple[tuple[str, ...], list[str]]:
    """Symbols this benchmark can score, and the published names absent from the file.

    W18 and O17 need their eight-ETF universe. When fewer than two of those
    names are in the file, the whole file is used so the rank is not a single
    name, and the missing list says the published universe was not available.
    """
    if name in {"w18", "o17"}:
        wanted = W18_SYMBOLS if name == "w18" else O17_SYMBOLS
    elif name == "w25":
        wanted = PROXY_SYMBOLS
    else:
        raise ValueError(f"unknown benchmark {name}")
    present = tuple(symbol for symbol in wanted if symbol in available)
    missing = [symbol for symbol in wanted if symbol not in available]
    if name in {"w18", "o17"} and len(present) < 2:
        return tuple(available), missing
    if not present:
        raise ValueError(f"{name} has none of its symbols in the bar file")
    return present, missing


def simulate_benchmark(name: str, grouped: dict[str, list[Bar]], *, symbols: tuple[str, ...] | None = None) -> dict:
    """Run one benchmark on the bars that exist. ``orders_submitted`` is 0."""
    if name not in BENCHMARK_NAMES:
        raise ValueError(f"unknown benchmark {name}")
    if symbols is None:
        symbols, _missing = symbols_for(name, tuple(sorted(grouped)))
    dates, closes, opens = aligned_panel(grouped, symbols)
    if name == "w18":
        targets = w18_targets(dates, closes)
        result = run_weight_book(dates, closes, opens, targets, business_month_end_flags(dates))
        result["strategy_id"] = W18_ID
    elif name == "o17":
        result = run_monthly_rotation(dates, closes, opens)
        result["strategy_id"] = O17_ID
    else:
        targets = w25_targets(closes)
        result = run_weight_book(dates, closes, opens, targets, business_month_end_flags(dates))
        result["strategy_id"] = W25_ID
        result["alpaca_targets"] = long_only(targets)[-1] if targets else {}
    result["name"] = name
    result["orders_submitted"] = 0
    return result
