# Port of QuantJourney Backtester strategies/example_orders_17_monthly_rotation_orders.py
# Copyright (c) 2026 QuantJourney.
# Licensed under the Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0).
# Source: https://github.com/QuantJourneyOrg/quantjourney-bt
# Commit: 4f403d853833e1daa6de01c5b6c6cb3983dc0044
# The month-event, the 126-day rank, and the sell-dropped / buy-flat order
# rule are taken from ``_compute_orders`` in that file. This module does not
# run the QuantJourney fill engine.

"""O17 event-driven monthly momentum rotation."""

from __future__ import annotations

SOURCE_COMMIT = "4f403d853833e1daa6de01c5b6c6cb3983dc0044"
SOURCE_PATH = "strategies/example_orders_17_monthly_rotation_orders.py"
STRATEGY_ID = "sidecar.benchmark.o17_monthly_rotation.v1"
PUBLISHED_SYMBOLS = ("SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ")
LOOKBACK = 126
TOP_N = 3
PUBLISHED = {"cagr": 0.0862, "sharpe": 0.53, "max_drawdown": -0.282}
# The source constructor also sets 3 bp slippage and $0.005 per share with a
# $1 minimum. The comparison book uses the sidecar's 1 bp next-open cost so
# the four rows share one cost model. Those source costs are not applied here.


def rank_targets(
    closes: dict[str, list[float]],
    index: int,
    *,
    lookback: int = LOOKBACK,
    top_n: int = TOP_N,
) -> list[str]:
    """Names with the highest lookback return, including negative returns.

    Unlike W18, a negative 6-month return can still be selected. Ties break
    on the symbol, ascending.
    """
    scored: list[tuple[float, str]] = []
    for symbol, series in closes.items():
        if index < lookback or series[index - lookback] == 0:
            continue
        change = series[index] / series[index - lookback] - 1.0
        scored.append((change, symbol))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [symbol for _change, symbol in scored[:top_n]]


def orders_for_event(
    closes: dict[str, list[float]],
    index: int,
    positions: dict[str, int],
    nav: float,
    prices: dict[str, float],
    *,
    lookback: int = LOOKBACK,
    top_n: int = TOP_N,
) -> list[tuple[str, str, int]]:
    """Sell names that left the top set. Buy a flat name its integer share of NAV.

    An existing holding that stays in the set is not resized. ``positions`` are
    share counts. The source sizes off ``bars[inst].close``; this book uses the
    adjusted close because that is the sidecar price.
    """
    target = set(rank_targets(closes, index, lookback=lookback, top_n=top_n))
    orders: list[tuple[str, str, int]] = []
    for symbol in closes:
        held = positions.get(symbol, 0)
        if held > 0 and symbol not in target:
            orders.append(("sell", symbol, held))
    per_name = nav / max(1, len(target))
    for symbol in target:
        if positions.get(symbol, 0) == 0:
            price = prices[symbol]
            if price <= 0:
                continue
            shares = int(per_name / price)
            if shares > 0:
                orders.append(("buy", symbol, shares))
    return orders
