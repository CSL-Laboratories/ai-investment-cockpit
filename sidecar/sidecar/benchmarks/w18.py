# Port of QuantJourney Backtester strategies/example_weights_18_vol_target_momentum.py
# Copyright (c) 2026 QuantJourney.
# Licensed under the Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0).
# Source: https://github.com/QuantJourneyOrg/quantjourney-bt
# Commit: 4f403d853833e1daa6de01c5b6c6cb3983dc0044
# The momentum rule, the 0.40 clip, the month-end rebalance, and VolTargetModel
# (target 0.15, lookback 42, max leverage 2, scale refreshed on the first sample
# day of each month) are taken from that file and from backtester/risk/vol_target.py
# at the same commit. This module does not run the QuantJourney engine.

"""W18 volatility-targeted momentum basket."""

from __future__ import annotations

import math
from datetime import date

SOURCE_COMMIT = "4f403d853833e1daa6de01c5b6c6cb3983dc0044"
SOURCE_PATH = "strategies/example_weights_18_vol_target_momentum.py"
STRATEGY_ID = "sidecar.benchmark.w18_vol_target_momentum.v1"
PUBLISHED_SYMBOLS = ("SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ")
LOOKBACK = 126
TOP_N = 3
WEIGHT_CAP = 0.40
TARGET_VOL = 0.15
VOL_LOOKBACK = 42
MAX_LEVERAGE = 2.0
PUBLISHED = {"cagr": 0.1096, "sharpe": 0.57, "max_drawdown": -0.412}


def _sample_std(values: list[float]) -> float:
    count = len(values)
    if count < 2:
        return float("nan")
    mean = sum(values) / count
    variance = sum((item - mean) ** 2 for item in values) / (count - 1)
    return math.sqrt(variance)


def momentum_weights(
    closes: dict[str, list[float]],
    *,
    lookback: int = LOOKBACK,
    top_n: int = TOP_N,
    cap: float = WEIGHT_CAP,
) -> list[dict[str, float]]:
    """Equal-weight the strongest positive names, then clip each weight at ``cap``.

    A name is eligible only when its ``lookback`` return is strictly positive.
    At most ``top_n`` names are held. Ties break on the symbol, ascending.
    That tie-break is not specified by ``Series.nlargest``.
    """
    symbols = list(closes)
    rows: list[dict[str, float]] = []
    length = len(closes[symbols[0]]) if symbols else 0
    for index in range(length):
        scored: list[tuple[float, str]] = []
        for symbol in symbols:
            series = closes[symbol]
            if index < lookback or series[index - lookback] == 0:
                continue
            change = series[index] / series[index - lookback] - 1.0
            if change > 0.0:
                scored.append((change, symbol))
        scored.sort(key=lambda item: (-item[0], item[1]))
        picks = [symbol for _change, symbol in scored[:top_n]]
        row = {symbol: 0.0 for symbol in symbols}
        if picks:
            weight = min(1.0 / len(picks), cap)
            for symbol in picks:
                row[symbol] = weight
        rows.append(row)
    return rows


def apply_vol_target(
    weights: list[dict[str, float]],
    returns: list[dict[str, float | None]],
    dates: list[date],
    *,
    target_vol: float = TARGET_VOL,
    lookback: int = VOL_LOOKBACK,
    max_leverage: float = MAX_LEVERAGE,
) -> list[dict[str, float]]:
    """Scale every name by one factor so recent portfolio vol matches the target.

    The factor is recomputed on the first sample date of a month and then held,
    matching ``VolTargetModel`` with the default ``rebalance_freq="BMS"`` period
    test (the month of the row changes). Rows before ``lookback`` stay unscaled.
    Realised vol at or below 1% resets the factor to 1.
    """
    if not weights:
        return []
    symbols = list(weights[0])
    out = [dict(row) for row in weights]
    scale = 1.0
    for index in range(lookback, len(weights)):
        row = weights[index]
        gross = sum(abs(row[symbol]) for symbol in symbols)
        if gross < 1e-8:
            out[index] = {symbol: 0.0 for symbol in symbols}
            continue
        month = (dates[index].year, dates[index].month)
        previous = (dates[index - 1].year, dates[index - 1].month)
        if month != previous:
            norm = {symbol: row[symbol] / gross for symbol in symbols}
            window = returns[index - lookback : index]
            portfolio = []
            for item in window:
                if any(item[symbol] is None for symbol in symbols):
                    continue
                portfolio.append(sum(norm[symbol] * item[symbol] for symbol in symbols))
            realised = _sample_std(portfolio) * math.sqrt(252.0)
            if realised > 0.01:
                scale = min(target_vol / realised, max_leverage)
            else:
                scale = 1.0
        out[index] = {symbol: row[symbol] * scale for symbol in symbols}
    return out


def asset_returns(closes: dict[str, list[float]]) -> list[dict[str, float | None]]:
    """Simple returns. The first row is missing, matching ``pct_change``."""
    symbols = list(closes)
    length = len(closes[symbols[0]]) if symbols else 0
    rows: list[dict[str, float | None]] = []
    for index in range(length):
        row: dict[str, float | None] = {}
        for symbol in symbols:
            series = closes[symbol]
            if index == 0 or series[index - 1] == 0:
                row[symbol] = None
            else:
                row[symbol] = series[index] / series[index - 1] - 1.0
        rows.append(row)
    return rows


def target_weights(
    dates: list[date],
    closes: dict[str, list[float]],
    *,
    lookback: int = LOOKBACK,
    top_n: int = TOP_N,
) -> list[dict[str, float]]:
    raw = momentum_weights(closes, lookback=lookback, top_n=top_n)
    return apply_vol_target(raw, asset_returns(closes), dates)
