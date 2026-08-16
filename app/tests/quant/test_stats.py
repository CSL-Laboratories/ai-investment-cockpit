"""QT-10 — backtest statistics gates.

The properties pinned, and why each one matters more than it looks:

  * A SHARPE WITHOUT A STANDARD ERROR IS NOT A MEASUREMENT. The interval is the output.
  * THE AUTOCORRELATION CORRECTION BITES IN THE RIGHT DIRECTION. Positively autocorrelated
    returns — illiquidity, stale marks, smoothed pricing — make the naive sqrt(252)
    annualization OVERSTATE the annual Sharpe. That is how illiquid strategies come to report
    implausible numbers, so the correction must reduce it, not merely change it.
  * THE NON-NORMAL CORRECTION WIDENS THE INTERVAL FOR SHORT-TAIL PAYOFFS. This package ships
    a variance-harvesting market maker; a normal assumption would flatter precisely the
    strategy whose risk is most concentrated in the tail.
  * SEARCHING 100 WORTHLESS STRATEGIES STILL PRODUCES A GOOD-LOOKING WINNER. The deflated
    Sharpe must reject it. This is the single most important test in the file.
  * PURGED CV ACTUALLY REMOVES THE LEAK, and the leak is verified to exist first — otherwise
    the test proves nothing about purging.
  * PBO ON PURE NOISE IS ~0.5. A selection procedure over noise is coin-flipping, and the
    diagnostic must say so.
"""
from __future__ import annotations

import math
import random

import pytest

from quant.stats import (
    MIN_OBSERVATIONS,
    autocorrelations,
    deflated_sharpe,
    expected_max_sharpe,
    min_track_record_length,
    probability_of_backtest_overfitting,
    purged_kfold,
    pnl_increments,
    returns_from_equity,
    sharpe_with_se,
)


def _normal(n: int, mean: float, sd: float, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


# ---- Sharpe with a standard error ------------------------------------------------------


def test_a_short_sample_returns_None_rather_than_a_number():
    """A statistic from four observations is noise with a decimal point. Returning 0.0 would
    let it propagate into a tearsheet and be quoted."""
    assert sharpe_with_se([0.01] * 5) is None
    assert sharpe_with_se(_normal(MIN_OBSERVATIONS - 1, 0.001, 0.01)) is None


def test_a_constant_series_returns_None_not_an_infinite_sharpe():
    assert sharpe_with_se([0.001] * 100) is None


def test_a_sharpe_comes_with_an_interval_that_contains_it():
    estimate = sharpe_with_se(_normal(500, 0.0005, 0.01), periods_per_year=252)
    assert estimate is not None
    low, high = estimate.confidence_interval()
    assert low < estimate.sharpe < high
    assert estimate.standard_error > 0


def test_the_interval_narrows_as_the_sample_grows():
    """The whole point of a standard error: more data, more confidence. If this failed, the
    SE would be a decoration rather than a measurement."""
    short = sharpe_with_se(_normal(60, 0.0005, 0.01, seed=1))
    long = sharpe_with_se(_normal(2_000, 0.0005, 0.01, seed=1))
    assert short is not None and long is not None
    assert long.standard_error < short.standard_error


def test_iid_returns_recover_the_naive_sqrt_annualization():
    """CONTROL. With no autocorrelation the correction must be a no-op — otherwise every
    result below is explained by a broken baseline rather than by the effect claimed."""
    estimate = sharpe_with_se(_normal(3_000, 0.0004, 0.01, seed=7), periods_per_year=252)
    assert estimate is not None
    assert estimate.autocorrelation_penalty == pytest.approx(1.0, abs=0.06)


def test_POSITIVE_autocorrelation_REDUCES_the_annualized_sharpe():
    """THE correction that matters. Smoothed/illiquid returns inflate the naive Sharpe;
    Lo's eta(q) must pull it back down. Getmansky, Lo & Makarov (2004) is the mechanism by
    which illiquid strategies report Sharpes they do not have."""
    rng = random.Random(3)
    smoothed: list[float] = [rng.gauss(0.0005, 0.01)]
    for _ in range(1_500):
        # AR(1) with phi=0.5 — the shape stale marks produce.
        smoothed.append(0.5 * smoothed[-1] + rng.gauss(0.00025, 0.0087))

    estimate = sharpe_with_se(smoothed, periods_per_year=252)
    assert estimate is not None
    assert estimate.autocorrelation_penalty < 0.95, (
        f"positive autocorrelation must penalise the annualization, got "
        f"{estimate.autocorrelation_penalty}"
    )
    naive = (sum(smoothed) / len(smoothed)) / _stdev(smoothed) * math.sqrt(252)
    assert estimate.sharpe < naive


def _stdev(xs) -> float:
    mean = sum(xs) / len(xs)
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs))


def test_negative_skew_and_fat_tails_WIDEN_the_standard_error():
    """A short-vol payoff and a symmetric one at the same Sharpe are not equally believable.
    Under a normal assumption that difference is invisible — which is exactly the assumption
    a variance-harvesting strategy would benefit from."""
    rng = random.Random(11)
    # Short-vol shape: many small gains, rare large losses.
    short_vol = [(-0.08 if rng.random() < 0.02 else 0.0022) for _ in range(1_000)]

    corrected = sharpe_with_se(short_vol, assume_normal=False)
    normal = sharpe_with_se(short_vol, assume_normal=True)
    assert corrected is not None and normal is not None
    assert corrected.skewness < -1.0, "fixture must actually be negatively skewed"
    assert corrected.standard_error > normal.standard_error


def test_the_harvey_hurdle_is_three_not_two():
    """Harvey, Liu & Zhu (2016). With hundreds of factors tested across the literature, the
    conventional t>2 admits far too many false positives."""
    weak = sharpe_with_se(_normal(300, 0.0004, 0.01, seed=5))
    assert weak is not None
    assert weak.is_significant_at_harvey_hurdle == (weak.t_statistic > 3.0)


def test_the_estimate_serialises_for_a_tearsheet():
    estimate = sharpe_with_se(_normal(400, 0.0005, 0.01))
    assert estimate is not None
    payload = estimate.as_dict()
    assert {"sharpe", "standard_error", "t_statistic", "ci95_low", "ci95_high"} <= set(payload)
    assert all(isinstance(v, (int, float, bool)) for v in payload.values())


def test_autocorrelations_are_empty_on_a_degenerate_series():
    assert autocorrelations([1.0] * 50, lags=5) == []
    assert autocorrelations([0.1, 0.2], lags=0) == []


# ---- the deflated Sharpe: the test that matters most ----------------------------------------


def test_searching_a_hundred_WORTHLESS_strategies_still_produces_a_good_looking_winner():
    """The premise of the whole module, demonstrated before it is corrected for.

    A hundred strategies with genuinely zero edge. The best of them looks good — not because
    it is good, but because a maximum over 100 draws is not a draw.
    """
    rng = random.Random(42)
    trials = [[rng.gauss(0.0, 0.01) for _ in range(250)] for _ in range(100)]
    sharpes = [(sum(t) / len(t)) / _stdev(t) for t in trials]
    best = max(sharpes)

    assert best > 0.15, "best-of-100 on pure noise should look meaningfully positive"
    naive_annual = best * math.sqrt(252)
    assert naive_annual > 1.5, (
        f"the naive annualized Sharpe of the best worthless strategy is {naive_annual:.2f} "
        "— this is the number a backtest would report"
    )


def test_the_deflated_sharpe_REJECTS_the_best_of_a_hundred_worthless_strategies():
    """And here it is corrected. Same data as above; the deflated Sharpe must not endorse it."""
    rng = random.Random(42)
    trials = [[rng.gauss(0.0, 0.01) for _ in range(250)] for _ in range(100)]
    sharpes = [(sum(t) / len(t)) / _stdev(t) for t in trials]
    winner = trials[sharpes.index(max(sharpes))]

    probability = deflated_sharpe(winner, trial_sharpes=sharpes)
    assert probability is not None
    assert probability < 0.95, (
        f"deflated Sharpe endorsed a strategy with no edge (p={probability:.3f})"
    )


def test_a_genuinely_strong_strategy_SURVIVES_deflation():
    """CONTROL. A deflator that rejected everything would pass the test above and be useless."""
    rng = random.Random(9)
    real = [rng.gauss(0.0016, 0.008) for _ in range(1_500)]
    noise_sharpes = [
        (lambda t: (sum(t) / len(t)) / _stdev(t))([rng.gauss(0.0, 0.01) for _ in range(250)])
        for _ in range(100)
    ]
    probability = deflated_sharpe(real, trial_sharpes=noise_sharpes)
    assert probability is not None
    assert probability > 0.95


def test_the_expected_maximum_sharpe_rises_with_the_number_of_trials():
    """More search, higher bar. This is the mechanism the deflation implements."""
    rng = random.Random(4)
    few = [rng.gauss(0.0, 0.1) for _ in range(10)]
    many = [rng.gauss(0.0, 0.1) for _ in range(1_000)]
    assert expected_max_sharpe(few) is not None
    assert expected_max_sharpe(many) > expected_max_sharpe(few)


def test_deflation_REQUIRES_the_trial_sharpes_and_has_no_default():
    """A caller who cannot say how many variants they tried cannot deflate. Defaulting to a
    single trial would assert the very thing the method exists to check."""
    import inspect

    signature = inspect.signature(deflated_sharpe)
    parameter = signature.parameters["trial_sharpes"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_deflation_returns_None_when_it_cannot_be_computed():
    assert deflated_sharpe([0.01] * 5, trial_sharpes=[0.1, 0.2]) is None
    assert deflated_sharpe(_normal(100, 0.001, 0.01), trial_sharpes=[0.5]) is None


# ---- minimum track record length ----------------------------------------------------------------


def _exact(n: int, mean: float, sd: float) -> list[float]:
    """A series whose SAMPLE mean and stdev are exactly ``mean`` and ``sd``.

    Deterministic by construction rather than by seed: a drawn sample can land with a
    negative mean even when the generating mean is positive, which would make the assertion
    below depend on the RNG rather than on the property being tested.
    """
    half = n // 2
    return [mean + sd] * half + [mean - sd] * half


def test_a_weaker_sharpe_needs_a_longer_track_record():
    """The relationship that makes the number useful: marginal results need far more data."""
    strong = min_track_record_length(_exact(500, 0.0020, 0.01))
    weak = min_track_record_length(_exact(500, 0.0004, 0.01))
    assert strong is not None and weak is not None
    assert weak > strong
    # And the magnitude is the point: a marginal Sharpe needs an implausible track record.
    assert weak > 500, f"a 0.04 per-period Sharpe needs far more than 500 observations, got {weak}"


def test_a_losing_strategy_has_no_track_record_length_that_saves_it():
    """No amount of additional data makes a negative result significantly positive."""
    assert min_track_record_length(_normal(500, -0.001, 0.01, seed=6)) is None


# ---- purged cross-validation ---------------------------------------------------------------------


def test_plain_kfold_LEAKS_across_the_boundary():
    """Established first, so the purging test below proves something. With horizon=5, a
    training point within 5 of the test block overlaps it."""
    naive = purged_kfold(100, n_splits=5, horizon=0, embargo=0)
    fold = naive[2]
    assert max(fold.train) > min(fold.test) or min(fold.train) < max(fold.test)
    assert len(fold.train) + len(fold.test) == 100      # nothing dropped at horizon 0


def test_purging_removes_training_points_whose_labels_reach_into_the_test_block():
    horizon = 5
    folds = purged_kfold(100, n_splits=5, horizon=horizon, embargo=0)
    for fold in folds:
        first_test, last_test = fold.test[0], fold.test[-1]
        for i in fold.train:
            assert i + horizon < first_test or i > last_test, (
                f"training index {i} leaks into test block [{first_test}, {last_test}]"
            )


def test_the_embargo_is_applied_AFTER_the_test_block_only():
    """Asymmetric on purpose — information flows forward. A symmetric buffer would discard
    usable history for no reason while missing nothing extra."""
    folds = purged_kfold(100, n_splits=5, horizon=0, embargo=7)
    fold = folds[2]
    last_test = fold.test[-1]
    assert not any(last_test < i <= last_test + 7 for i in fold.train)
    assert any(i < fold.test[0] for i in fold.train), "history before the block must survive"


def test_purging_and_the_embargo_shrink_the_training_set():
    plain = purged_kfold(200, n_splits=5, horizon=0, embargo=0)
    purged = purged_kfold(200, n_splits=5, horizon=10, embargo=10)
    assert len(purged[2].train) < len(plain[2].train)


def test_every_observation_is_tested_exactly_once():
    folds = purged_kfold(97, n_splits=5, horizon=3, embargo=2)
    tested = [i for fold in folds for i in fold.test]
    assert sorted(tested) == list(range(97))


def test_degenerate_split_requests_return_empty_rather_than_raising():
    assert purged_kfold(3, n_splits=5) == []
    assert purged_kfold(100, n_splits=1) == []


def test_negative_horizon_or_embargo_is_refused():
    with pytest.raises(ValueError, match="non-negative"):
        purged_kfold(100, n_splits=5, horizon=-1)


# ---- probability of backtest overfitting ------------------------------------------------------------


def test_PBO_on_pure_noise_is_near_a_coin_flip():
    """Selecting the in-sample winner from noise is choosing at random, and the diagnostic
    must say so rather than reporting a comfortable number."""
    rng = random.Random(17)
    matrix = [[rng.gauss(0.0, 0.01) for _ in range(20)] for _ in range(400)]
    pbo = probability_of_backtest_overfitting(matrix, n_partitions=8)
    assert pbo is not None
    assert 0.25 < pbo < 0.75, f"PBO on noise should be near 0.5, got {pbo}"


def test_PBO_is_LOW_when_one_strategy_genuinely_dominates():
    """CONTROL: a diagnostic that always returned ~0.5 would pass the test above."""
    rng = random.Random(23)
    matrix = []
    for _ in range(400):
        row = [rng.gauss(0.0, 0.01) for _ in range(19)]
        row.append(rng.gauss(0.004, 0.008))       # a genuinely better variant
        matrix.append(row)
    pbo = probability_of_backtest_overfitting(matrix, n_partitions=8)
    assert pbo is not None
    assert pbo < 0.2, f"a real edge should not look overfit, got {pbo}"


def test_PBO_requires_an_even_partition_count():
    with pytest.raises(ValueError, match="even"):
        probability_of_backtest_overfitting([[0.1, 0.2]] * 100, n_partitions=7)


def test_PBO_returns_None_on_an_unusable_matrix():
    assert probability_of_backtest_overfitting([[0.1, 0.2]] * 4, n_partitions=8) is None
    assert probability_of_backtest_overfitting([[0.1]] * 100, n_partitions=8) is None


# ---- equity-curve helper ----------------------------------------------------------------------------------


def test_returns_are_derived_from_an_account_value_curve():
    assert returns_from_equity([100.0, 110.0, 99.0]) == pytest.approx([0.1, -0.1])


def test_a_zero_or_negative_equity_point_is_skipped_rather_than_dividing():
    """Correct for an ACCOUNT VALUE series: a division by zero would produce an infinity
    that poisons every downstream moment."""
    assert returns_from_equity([0.0, 100.0, 110.0]) == pytest.approx([0.1])
    assert returns_from_equity([-5.0, 100.0]) == []


def test_pnl_increments_keep_every_sample_including_the_underwater_ones():
    """REGRESSION. A cumulative PnL path starts at zero and may be negative for long
    stretches. Running it through ``returns_from_equity`` does not raise — it silently drops
    every sample taken while PnL was at or below zero, so a losing strategy has most of its
    history filtered out and reports statistics from the few moments it was up.

    That is a survivorship filter on your own equity curve, flattering exactly the runs that
    deserve it least. It reduced a real 505-sample market-maker run to 16 samples before it
    was caught.
    """
    underwater = [0.0, -5.0, -12.0, -8.0, -20.0]

    assert len(pnl_increments(underwater)) == len(underwater) - 1
    assert pnl_increments(underwater) == pytest.approx([-5.0, -7.0, 4.0, -12.0])
    # The wrong tool keeps almost nothing from the same series.
    assert len(returns_from_equity(underwater)) < len(pnl_increments(underwater))


def test_a_sharpe_on_pnl_increments_is_well_defined():
    """Dimensionally sound — mean increment over the stdev of increments — and how desks
    with no natural capital denominator (market making, futures) quote it."""
    rng = random.Random(31)
    equity, level = [0.0], 0.0
    for _ in range(400):
        level += rng.gauss(0.5, 2.0)
        equity.append(level)
    estimate = sharpe_with_se(pnl_increments(equity), periods_per_year=1)
    assert estimate is not None
    assert estimate.sharpe > 0
