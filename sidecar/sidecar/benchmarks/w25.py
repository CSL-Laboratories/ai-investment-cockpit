# Port of QuantJourney Backtester strategies/example_weights_25_continuous_futures_trend.py
# Copyright (c) 2026 QuantJourney.
# Licensed under the Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0).
# Source: https://github.com/QuantJourneyOrg/quantjourney-bt
# Commit: 4f403d853833e1daa6de01c5b6c6cb3983dc0044
# ``build_continuous_futures_trend`` is the signal in that file: SMA(63) versus
# SMA(252), sign, then unit-gross inverse-volatility weights. Month-end
# rebalance is the file's RebalancePolicy(frequency="BME"). This module does
# not run the QuantJourney engine and does not trade futures.

"""W25 continuous-futures trend, scored on ETF proxies."""

from __future__ import annotations

import math

SOURCE_COMMIT = "4f403d853833e1daa6de01c5b6c6cb3983dc0044"
SOURCE_PATH = "strategies/example_weights_25_continuous_futures_trend.py"
STRATEGY_ID = "sidecar.benchmark.w25_continuous_futures_trend.v1"
FAST_WINDOW = 63
SLOW_WINDOW = 252
VOL_WINDOW = 63
# Alpaca equity paper has no futures. These ETFs stand in for the source
# continuous contracts. None of them is the future: no roll, no multiplier,
# no exchange margin. DBA is a broad agriculture basket, not corn alone.
FUTURES_TO_ETF = {
    "MES=F": "SPY",
    "MNQ=F": "QQQ",
    "ZN=F": "IEF",
    "CL=F": "USO",
    "GC=F": "GLD",
    "ZC=F": "DBA",
}
PROXY_SYMBOLS = tuple(FUTURES_TO_ETF.values())
PUBLISHED = {"cagr": 0.0588, "sharpe": 0.62, "max_drawdown": -0.097}
SUBSTITUTION_NOTE = (
    "W25 is scored on ETF proxies for MES=F, MNQ=F, ZN=F, CL=F, GC=F and ZC=F "
    "(SPY, QQQ, IEF, USO, GLD, DBA). The source series are provider continuous "
    "futures. This book does not roll contracts or apply a futures multiplier. "
    "Negative weights are a research short. The Alpaca shadow sets those "
    "weights to cash. It does not buy an inverse ETF."
)


def _rolling_mean(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if window < 1:
        return out
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        if index + 1 >= window:
            out[index] = running / window
    return out


def _sample_std(values: list[float]) -> float:
    count = len(values)
    if count < 2:
        return float("nan")
    mean = sum(values) / count
    variance = sum((item - mean) ** 2 for item in values) / (count - 1)
    return math.sqrt(variance)


def _pct_changes(values: list[float]) -> list[float | None]:
    out: list[float | None] = [None]
    for index in range(1, len(values)):
        previous = values[index - 1]
        out.append(None if previous == 0 else values[index] / previous - 1.0)
    return out


def build_continuous_futures_trend(
    closes: dict[str, list[float]],
    *,
    fast_window: int = FAST_WINDOW,
    slow_window: int = SLOW_WINDOW,
    volatility_window: int = VOL_WINDOW,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """Moving-average sign and unit-gross inverse-volatility weights.

    A bar is flat until both averages exist. A zero volatility drops that
    name. Rows whose absolute weights sum to zero stay flat.
    """
    symbols = list(closes)
    length = len(closes[symbols[0]]) if symbols else 0
    fast = {symbol: _rolling_mean(closes[symbol], fast_window) for symbol in symbols}
    slow = {symbol: _rolling_mean(closes[symbol], slow_window) for symbol in symbols}
    changes = {symbol: _pct_changes(closes[symbol]) for symbol in symbols}
    signals: list[dict[str, float]] = []
    weights: list[dict[str, float]] = []
    for index in range(length):
        signal = {}
        raw: dict[str, float] = {}
        for symbol in symbols:
            fast_value = fast[symbol][index]
            slow_value = slow[symbol][index]
            if fast_value is None or slow_value is None:
                signal[symbol] = 0.0
                continue
            diff = fast_value - slow_value
            sign = 0.0 if diff == 0 else (1.0 if diff > 0 else -1.0)
            signal[symbol] = sign
            window = changes[symbol][index - volatility_window + 1 : index + 1]
            finite = [item for item in window if item is not None]
            if len(finite) < volatility_window:
                continue
            volatility = _sample_std(finite) * math.sqrt(252.0)
            if not math.isfinite(volatility) or volatility == 0.0:
                continue
            raw[symbol] = sign / volatility
        gross = sum(abs(value) for value in raw.values())
        row = {symbol: 0.0 for symbol in symbols}
        if gross > 0.0:
            for symbol, value in raw.items():
                row[symbol] = value / gross
        signals.append(signal)
        weights.append(row)
    return signals, weights


def target_weights(
    closes: dict[str, list[float]],
    *,
    fast_window: int = FAST_WINDOW,
    slow_window: int = SLOW_WINDOW,
    volatility_window: int = VOL_WINDOW,
) -> list[dict[str, float]]:
    return build_continuous_futures_trend(
        closes,
        fast_window=fast_window,
        slow_window=slow_window,
        volatility_window=volatility_window,
    )[1]


def long_only(weights: list[dict[str, float]]) -> list[dict[str, float]]:
    """Drop shorts to cash. Do not replace them with an inverse ETF."""
    return [{symbol: value if value > 0 else 0.0 for symbol, value in row.items()} for row in weights]
