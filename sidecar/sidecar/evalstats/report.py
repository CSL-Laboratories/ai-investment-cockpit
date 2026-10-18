"""Assemble a schema-validated evaluation report. Never a qualify decision."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

from sidecar.evalstats.core import (
    MIN_OBSERVATIONS,
    EvalStatsError,
    benchmark_stats,
    deflated_sharpe_ratio,
    drawdown_stats,
    minimum_track_record_length,
    moments,
    probabilistic_sharpe_ratio,
    stationary_bootstrap_ci,
)
from sidecar.evalstats.pbo import probability_of_overfitting
from sidecar.jsonutil import validate

SCHEMA = Path(__file__).with_name("eval_report.schema.json")


def _s(value: float) -> str:
    if not math.isfinite(value):
        raise EvalStatsError("refusing to emit a non-finite statistic")
    return format(value, ".12g")


def _optional(value: float | None) -> str | None:
    if value is None:
        return None
    return _s(value)


def evaluate(
    returns: Sequence[float],
    n_trials: int = 1,
    benchmark: Sequence[float] | None = None,
    *,
    periods_per_year: int = 252,
    sr_benchmark: float = 0.0,
    trial_matrix: Sequence[Sequence[float]] | None = None,
    n_slices: int = 16,
    bootstrap_reps: int = 400,
    seed: int = 0,
    track_record_confidence: float = 0.95,
) -> dict:
    """Evaluate a return series.

    ``n_trials`` is the number of configurations or prompts already tried, including this
    one. Pass the ledger's distinct count. The report records statistics only. It does not
    compare them with a frozen pass/fail bar.
    """
    if periods_per_year < 1:
        raise EvalStatsError("periods_per_year must be positive")
    if n_trials < 1:
        raise EvalStatsError("n_trials must be >= 1")
    notes = [
        "PSR and DSR use the per-period Sharpe. Annualized Sharpe is a display scaling.",
        "threshold_status is not_frozen. This report is not a qualify or reject decision.",
    ]
    try:
        stats = moments(returns)
    except EvalStatsError as exc:
        report = {
            "schema_version": "sidecar.eval_report.v1",
            "status": "insufficient_sample",
            "reason": str(exc),
            "n_observations": len(list(returns)),
            "n_trials": n_trials,
            "periods_per_year": periods_per_year,
            "threshold_status": "not_frozen",
            "notes": notes,
        }
        return validate(report, SCHEMA)

    status = "ok"
    reason = None
    psr = dsr = threshold = min_trl = None
    try:
        psr = probabilistic_sharpe_ratio(stats, sr_benchmark)
        dsr, threshold = deflated_sharpe_ratio(stats, n_trials, sr_benchmark)
        min_trl = minimum_track_record_length(
            stats, sr_benchmark=sr_benchmark, confidence=track_record_confidence
        )
    except EvalStatsError as exc:
        status = "moment_bound_violated"
        reason = str(exc)
        notes.append(reason)

    drawdown = drawdown_stats(returns)
    try:
        bootstrap = stationary_bootstrap_ci(returns, reps=bootstrap_reps, seed=seed)
    except EvalStatsError as exc:
        bootstrap = None
        notes.append(f"bootstrap omitted: {exc}")

    bench = None
    if benchmark is not None:
        raw = benchmark_stats(returns, benchmark)
        excess = raw["excess_sharpe_per_period"]
        bench = {
            "beta": _s(raw["beta"]),
            "down_market_beta": _optional(raw["down_market_beta"]),
            "n_down": raw["n_down"],
            "excess_sharpe_per_period": _optional(excess),
            "excess_sharpe_annualized": (
                None if excess is None else _s(excess * math.sqrt(periods_per_year))
            ),
        }

    pbo = None
    if trial_matrix is not None:
        try:
            raw_pbo = probability_of_overfitting(trial_matrix, n_slices=n_slices)
        except EvalStatsError as exc:
            pbo = {"status": "unavailable", "reason": str(exc)}
            notes.append(f"PBO omitted: {exc}")
        else:
            pbo = {
                "status": "ok",
                "n_strategies": raw_pbo["n_strategies"],
                "n_slices": raw_pbo["n_slices"],
                "n_splits": raw_pbo["n_splits"],
                "observations_used": raw_pbo["observations_used"],
                "observations_dropped_prefix": raw_pbo["observations_dropped_prefix"],
                "pbo": _s(raw_pbo["pbo"]),
                "mean_oos_sharpe_of_selected": _s(raw_pbo["mean_oos_sharpe_of_selected"]),
                "median_logit": _s(raw_pbo["median_logit"]),
            }
            notes.append(
                "PBO is the fraction of CSCV splits where the in-sample winner's "
                "out-of-sample Sharpe is at or below the median. Splits are not purged."
            )

    if min_trl is None and status == "ok":
        notes.append(
            "No finite minimum track record: per-period Sharpe does not exceed the benchmark."
        )

    report = {
        "schema_version": "sidecar.eval_report.v1",
        "status": status,
        "reason": reason,
        "n_observations": stats.n,
        "minimum_observations": MIN_OBSERVATIONS,
        "n_trials": n_trials,
        "periods_per_year": periods_per_year,
        "sr_benchmark": _s(sr_benchmark),
        "mean_return": _s(stats.mean),
        "volatility": _s(stats.volatility),
        "sharpe_per_period": _s(stats.sharpe),
        "sharpe_annualized": _s(stats.sharpe * math.sqrt(periods_per_year)),
        "skewness": _s(stats.skewness),
        "kurtosis_pearson": _s(stats.kurtosis_pearson),
        "psr": _optional(psr),
        "dsr": _optional(dsr),
        "sr_deflation_threshold": _optional(threshold),
        "min_track_record_length": _optional(min_trl),
        "min_track_record_years": (
            None if min_trl is None else _s(min_trl / periods_per_year)
        ),
        "track_record_confidence": _s(track_record_confidence),
        "drawdown": {
            "max_drawdown": _s(drawdown["max_drawdown"]),
            "max_drawdown_duration": drawdown["max_drawdown_duration"],
            "current_drawdown": _s(drawdown["current_drawdown"]),
            "ulcer_index": _s(drawdown["ulcer_index"]),
            "peak_index": drawdown["peak_index"],
            "trough_index": drawdown["trough_index"],
        },
        "bootstrap": (
            None
            if bootstrap is None
            else {
                "method": bootstrap["method"],
                "reps_requested": bootstrap["reps_requested"],
                "reps_used": bootstrap["reps_used"],
                "seed": bootstrap["seed"],
                "block_length": bootstrap["block_length"],
                "sharpe_per_period_ci95": [
                    _s(bootstrap["sharpe_per_period_ci95"][0]),
                    _s(bootstrap["sharpe_per_period_ci95"][1]),
                ],
                "mean_return_ci95": [
                    _s(bootstrap["mean_return_ci95"][0]),
                    _s(bootstrap["mean_return_ci95"][1]),
                ],
                "max_drawdown_ci95": [
                    _s(bootstrap["max_drawdown_ci95"][0]),
                    _s(bootstrap["max_drawdown_ci95"][1]),
                ],
            }
        ),
        "benchmark": bench,
        "pbo": pbo,
        "threshold_status": "not_frozen",
        "notes": notes,
    }
    return validate(report, SCHEMA)
