"""PSR, deflated Sharpe, minimum track record, drawdown, and block bootstrap.

Per-period Sharpe uses the sample standard deviation (divide by n - 1). Skewness and Pearson
kurtosis use the biased moments (divide by n). PSR and DSR are computed on the per-period
Sharpe. Annualized Sharpe is reported separately and is not fed back into PSR.

Formulas follow Bailey and López de Prado, "The Deflated Sharpe Ratio" (2014):

    PSR(SR*) = Phi( (SR - SR*) * sqrt(n - 1) / sqrt(1 - skew*SR + (kurt - 1)/4 * SR^2) )

    SR0 = SR* + std(SR) * ((1 - euler) * Z^{-1}(1 - 1/N) + euler * Z^{-1}(1 - 1/(N*e)))

    DSR = PSR(SR0) for N >= 2, and DSR = PSR(SR*) for a single trial.

Minimum track-record length is the observation count that makes PSR reach the requested
probability when the plug-in moments stay constant.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from sidecar.normal import norm_cdf, norm_ppf

EULER = 0.5772156649015329
MIN_OBSERVATIONS = 8


class EvalStatsError(ValueError):
    """A statistic was requested on a series that cannot support it."""


@dataclass(frozen=True)
class ReturnMoments:
    n: int
    mean: float
    volatility: float
    sharpe: float
    skewness: float
    kurtosis_pearson: float


def _as_floats(returns: Sequence[float]) -> list[float]:
    values = []
    for item in returns:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise EvalStatsError("returns must be a sequence of finite numbers")
        value = float(item)
        if not math.isfinite(value):
            raise EvalStatsError("returns must be finite")
        values.append(value)
    return values


def moments(returns: Sequence[float]) -> ReturnMoments:
    values = _as_floats(returns)
    n = len(values)
    if n < MIN_OBSERVATIONS:
        raise EvalStatsError(f"need at least {MIN_OBSERVATIONS} returns, got {n}")
    mean = sum(values) / n
    centered = [value - mean for value in values]
    m2 = sum(value * value for value in centered) / n
    if m2 <= 0.0:
        raise EvalStatsError("zero variance")
    m3 = sum(value * value * value for value in centered) / n
    m4 = sum(value ** 4 for value in centered) / n
    sample_var = sum(value * value for value in centered) / (n - 1)
    if sample_var <= 0.0:
        raise EvalStatsError("zero variance")
    return ReturnMoments(
        n=n,
        mean=mean,
        volatility=math.sqrt(sample_var),
        sharpe=mean / math.sqrt(sample_var),
        skewness=m3 / (m2 ** 1.5),
        kurtosis_pearson=m4 / (m2 * m2),
    )


def _sharpe_moment_factor(stats: ReturnMoments) -> float:
    factor = (
        1.0
        - stats.skewness * stats.sharpe
        + ((stats.kurtosis_pearson - 1.0) / 4.0) * stats.sharpe * stats.sharpe
    )
    if factor <= 0.0 or not math.isfinite(factor):
        raise EvalStatsError("moment bound violated; PSR denominator is not positive")
    return factor


def probabilistic_sharpe_ratio(
    stats: ReturnMoments, sr_benchmark: float = 0.0
) -> float:
    if not math.isfinite(sr_benchmark):
        raise EvalStatsError("benchmark Sharpe must be finite")
    factor = _sharpe_moment_factor(stats)
    z = (stats.sharpe - sr_benchmark) * math.sqrt(stats.n - 1) / math.sqrt(factor)
    if not math.isfinite(z):
        raise EvalStatsError("PSR statistic is not finite")
    return norm_cdf(z)


def sharpe_estimator_std(stats: ReturnMoments) -> float:
    return math.sqrt(_sharpe_moment_factor(stats) / (stats.n - 1))


def deflated_threshold(stats: ReturnMoments, n_trials: int, sr_benchmark: float) -> float:
    """Sharpe hurdle used as SR* inside DSR."""
    if n_trials < 1:
        raise EvalStatsError("n_trials must be >= 1")
    if n_trials == 1:
        return sr_benchmark
    std = sharpe_estimator_std(stats)
    # Expected maximum of N null Sharpes, shifted so the null mean is sr_benchmark.
    haircut = (1.0 - EULER) * norm_ppf(1.0 - 1.0 / n_trials) + EULER * norm_ppf(
        1.0 - 1.0 / (n_trials * math.e)
    )
    return sr_benchmark + std * haircut


def deflated_sharpe_ratio(
    stats: ReturnMoments, n_trials: int, sr_benchmark: float = 0.0
) -> tuple[float, float]:
    threshold = deflated_threshold(stats, n_trials, sr_benchmark)
    return probabilistic_sharpe_ratio(stats, threshold), threshold


def minimum_track_record_length(
    stats: ReturnMoments,
    *,
    sr_benchmark: float = 0.0,
    confidence: float = 0.95,
) -> float | None:
    """Return the observation count required for PSR to reach ``confidence``.

    ``None`` means the observed Sharpe does not clear the benchmark, so no finite sample
    of the same Sharpe rejects that benchmark.
    """
    if not 0.5 < confidence < 1.0:
        raise EvalStatsError("confidence must be in (0.5, 1)")
    gap = stats.sharpe - sr_benchmark
    if gap <= 0.0:
        return None
    z = norm_ppf(confidence)
    return 1.0 + _sharpe_moment_factor(stats) * (z / gap) ** 2


def equity_curve(returns: Sequence[float], start: float = 1.0) -> list[float]:
    values = _as_floats(returns)
    if start <= 0.0 or not math.isfinite(start):
        raise EvalStatsError("equity start must be positive")
    curve = [start]
    level = start
    for value in values:
        level *= 1.0 + value
        if level <= 0.0 or not math.isfinite(level):
            raise EvalStatsError("equity curve left the positive reals")
        curve.append(level)
    return curve


def drawdown_stats(returns: Sequence[float]) -> dict:
    curve = equity_curve(returns)
    peak = curve[0]
    max_dd = 0.0
    current_dd = 0.0
    underwater = 0
    max_duration = 0
    peak_at = 0
    trough_at = 0
    episode_peak = 0
    sum_sq = 0.0
    for index, level in enumerate(curve):
        if level >= peak:
            peak = level
            underwater = 0
            episode_peak = index
        else:
            underwater += 1
            dd = (peak - level) / peak
            sum_sq += dd * dd
            if dd > max_dd:
                max_dd = dd
                peak_at = episode_peak
                trough_at = index
            max_duration = max(max_duration, underwater)
        current_dd = 0.0 if curve[-1] >= peak else (peak - curve[-1]) / peak
    n = len(curve)
    ulcer = math.sqrt(sum_sq / n) if n else 0.0
    return {
        "max_drawdown": max_dd,
        "max_drawdown_duration": max_duration,
        "current_drawdown": current_dd,
        "ulcer_index": ulcer,
        "peak_index": peak_at,
        "trough_index": trough_at,
    }


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        raise EvalStatsError("no bootstrap replicates")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * p
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def _sharpe_of(sample: Sequence[float]) -> float:
    return moments(sample).sharpe


def _mean_of(sample: Sequence[float]) -> float:
    values = _as_floats(sample)
    return sum(values) / len(values)


def _max_dd_of(sample: Sequence[float]) -> float:
    return drawdown_stats(sample)["max_drawdown"]


def stationary_bootstrap_ci(
    returns: Sequence[float],
    *,
    reps: int = 400,
    block_length: int | None = None,
    seed: int = 0,
    confidence: float = 0.95,
) -> dict:
    """Politis-Romano stationary bootstrap percentile intervals.

    Block length defaults to ``round(n ** (1/3))``. Replicates that have zero variance are
    dropped and counted in ``reps_used``.
    """
    values = _as_floats(returns)
    n = len(values)
    if n < MIN_OBSERVATIONS:
        raise EvalStatsError(f"need at least {MIN_OBSERVATIONS} returns, got {n}")
    if reps < 50:
        raise EvalStatsError("bootstrap reps must be at least 50")
    if not 0.0 < confidence < 1.0:
        raise EvalStatsError("confidence must be in (0, 1)")
    length = block_length if block_length is not None else max(1, int(round(n ** (1.0 / 3.0))))
    if length < 1:
        raise EvalStatsError("block length must be positive")
    jump = 1.0 / length
    rng = random.Random(seed)
    sharpes: list[float] = []
    means: list[float] = []
    drawdowns: list[float] = []
    for _ in range(reps):
        sample: list[float] = []
        index = rng.randrange(n)
        while len(sample) < n:
            sample.append(values[index])
            if rng.random() < jump:
                index = rng.randrange(n)
            else:
                index = (index + 1) % n
        try:
            sharpes.append(_sharpe_of(sample))
            means.append(_mean_of(sample))
            drawdowns.append(_max_dd_of(sample))
        except EvalStatsError:
            continue
    if len(sharpes) < 50:
        raise EvalStatsError("too many bootstrap replicates had zero variance")
    alpha = (1.0 - confidence) / 2.0
    sharpes.sort()
    means.sort()
    drawdowns.sort()
    return {
        "method": "stationary",
        "reps_requested": reps,
        "reps_used": len(sharpes),
        "seed": seed,
        "block_length": length,
        "confidence": confidence,
        "sharpe_per_period_ci95": (_percentile(sharpes, alpha), _percentile(sharpes, 1.0 - alpha)),
        "mean_return_ci95": (_percentile(means, alpha), _percentile(means, 1.0 - alpha)),
        "max_drawdown_ci95": (
            _percentile(drawdowns, alpha),
            _percentile(drawdowns, 1.0 - alpha),
        ),
    }


def ordinary_least_squares(y: Sequence[float], x: Sequence[float]) -> tuple[float, float]:
    """Return intercept and slope of y on x. Used for beta."""
    ys = _as_floats(y)
    xs = _as_floats(x)
    if len(ys) != len(xs) or len(ys) < 3:
        raise EvalStatsError("beta inputs must be aligned and have at least 3 points")
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    var = sum((item - x_mean) ** 2 for item in xs)
    if var <= 0.0:
        raise EvalStatsError("benchmark has zero variance")
    cov = sum((a - x_mean) * (b - y_mean) for a, b in zip(xs, ys, strict=True))
    return y_mean - (cov / var) * x_mean, cov / var


def benchmark_stats(returns: Sequence[float], benchmark: Sequence[float]) -> dict:
    ys = _as_floats(returns)
    xs = _as_floats(benchmark)
    if len(ys) != len(xs):
        raise EvalStatsError("benchmark length must match returns")
    _intercept, beta = ordinary_least_squares(ys, xs)
    down_y = [y for y, x in zip(ys, xs, strict=True) if x < 0.0]
    down_x = [x for x in xs if x < 0.0]
    down_beta = None
    if len(down_y) >= MIN_OBSERVATIONS:
        try:
            _down_intercept, down_beta = ordinary_least_squares(down_y, down_x)
        except EvalStatsError:
            down_beta = None
    excess = [y - x for y, x in zip(ys, xs, strict=True)]
    excess_sharpe = None
    try:
        excess_sharpe = moments(excess).sharpe
    except EvalStatsError:
        excess_sharpe = None
    return {
        "beta": beta,
        "down_market_beta": down_beta,
        "n_down": len(down_y),
        "excess_sharpe_per_period": excess_sharpe,
    }
