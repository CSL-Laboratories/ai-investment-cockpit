from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sidecar.evalstats.core import (
    drawdown_stats,
    minimum_track_record_length,
    moments,
    probabilistic_sharpe_ratio,
)
from sidecar.evalstats.ledger import TrialLedger
from sidecar.evalstats.pbo import probability_of_overfitting
from sidecar.evalstats.report import evaluate
from sidecar.normal import norm_cdf, norm_ppf


def test_normal_quantile_round_trip():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-12)
    assert norm_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-10)
    assert norm_cdf(norm_ppf(0.95)) == pytest.approx(0.95, abs=1e-12)


def test_psr_at_observed_sharpe_is_one_half():
    rng = random.Random(7)
    series = [rng.gauss(0.001, 0.01) for _ in range(80)]
    stats = moments(series)
    assert probabilistic_sharpe_ratio(stats, stats.sharpe) == pytest.approx(0.5, abs=1e-12)


def test_more_trials_deflate_the_sharpe():
    rng = random.Random(3)
    series = [rng.gauss(0.001, 0.01) for _ in range(120)]
    one = evaluate(series, 1, bootstrap_reps=50, seed=1)
    many = evaluate(series, 50, bootstrap_reps=50, seed=1)
    assert float(many["dsr"]) < float(one["dsr"])
    assert one["threshold_status"] == "not_frozen"
    assert one["status"] in {"ok", "moment_bound_violated", "insufficient_sample"}


def test_minimum_track_record_matches_the_closed_form():
    rng = random.Random(11)
    series = [0.002 + rng.gauss(0, 0.01) for _ in range(100)]
    stats = moments(series)
    length = minimum_track_record_length(stats, confidence=0.95)
    assert length is not None
    factor = (
        1.0
        - stats.skewness * stats.sharpe
        + ((stats.kurtosis_pearson - 1.0) / 4.0) * stats.sharpe**2
    )
    expected = 1.0 + factor * (norm_ppf(0.95) / stats.sharpe) ** 2
    assert length == pytest.approx(expected, rel=1e-12)


def test_drawdown_on_a_known_path():
    stats = drawdown_stats([0.10, -0.50, 0.20])
    assert stats["max_drawdown"] == pytest.approx(0.5)
    assert stats["max_drawdown_duration"] == 2
    assert stats["current_drawdown"] == pytest.approx((1.1 - 0.66) / 1.1)


def test_short_series_does_not_invent_a_sharpe():
    report = evaluate([0.01, -0.01, 0.02], 1)
    assert report["status"] == "insufficient_sample"
    assert "psr" not in report


def test_pbo_on_constructed_winners_and_losers():
    dominated = probability_of_overfitting(
        [[1.0, 2.0, -2.0, -1.0], [-2.0, -1.0, 1.0, 2.0]],
        n_slices=2,
    )
    assert dominated["pbo"] == 1.0
    dominant = probability_of_overfitting(
        [[1.0, 2.0, 1.5, 2.5], [-1.0, -0.5, -1.5, -2.0]],
        n_slices=2,
    )
    assert dominant["pbo"] == 0.0


def test_bootstrap_interval_is_reproducible_and_covers_a_constant_shift():
    rng = random.Random(4)
    series = [rng.gauss(0.0, 0.01) for _ in range(40)]
    report = evaluate(series, 1, bootstrap_reps=60, seed=9)
    again = evaluate(series, 1, bootstrap_reps=60, seed=9)
    assert report["bootstrap"]["sharpe_per_period_ci95"] == again["bootstrap"]["sharpe_per_period_ci95"]
    low, high = (float(item) for item in report["bootstrap"]["mean_return_ci95"])
    assert low < high


def test_trial_ledger_is_append_only_and_counts_distinct_configs(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "trials.jsonl")
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    ledger.append(kind="parameter_set", name="a", config={"lookback": 20}, recorded_at=now)
    ledger.append(kind="parameter_set", name="a-again", config={"lookback": 20}, recorded_at=now)
    ledger.append(kind="prompt_version", name="prompt", config={"id": "v1"}, recorded_at=now)
    assert ledger.distinct_config_count() == 2
    assert len(ledger.entries()) == 3
    text = (tmp_path / "trials.jsonl").read_text(encoding="utf-8")
    tampered = text.replace('"lookback":20', '"lookback":21', 1)
    (tmp_path / "trials.jsonl").write_text(tampered, encoding="utf-8")
    with pytest.raises(Exception):
        ledger.entries()


def test_down_market_beta_uses_only_negative_benchmark_days():
    returns = [0.02, -0.01, 0.03, -0.02, 0.01, -0.03, 0.02, 0.01, -0.02, 0.04]
    benchmark = [0.01, -0.02, 0.02, -0.03, 0.01, -0.04, 0.00, 0.02, -0.01, 0.03]
    # Pad so the full-sample moments clear the minimum length.
    returns = returns * 2
    benchmark = benchmark * 2
    report = evaluate(returns, 1, benchmark, bootstrap_reps=50, seed=1)
    assert report["benchmark"]["down_market_beta"] is not None
    assert report["benchmark"]["n_down"] == sum(1 for value in benchmark if value < 0)
    assert math.isfinite(float(report["benchmark"]["beta"]))
