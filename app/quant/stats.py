"""QT-10 — backtest statistics: is this result real, or did I find it by looking?

WHY THIS MODULE OUTRANKS EVERY STRATEGY IN THE PACKAGE
--------------------------------------------------------
The rest of ``quant/`` can run a strategy and attribute its PnL exactly. None of it can say
whether the resulting number means anything. That is the more important question, and it has
a specific, well-developed answer that this module implements.

The problem is not that backtests are noisy — it is that SEARCH IS FREE and evaluation is
not. Try two hundred variants of a market maker, report the best, and you have a number that
is a maximum over two hundred draws being presented as a single draw. The Sharpe ratio you
print is an order statistic, not an estimate. Harvey, Liu & Zhu (2016) put the appropriate
t-hurdle near 3.0 for exactly this reason; Hou, Xue & Zhang (2020) found ~65% of published
anomalies fail to replicate under consistent methodology.

FOUR TOOLS, IN THE ORDER YOU NEED THEM
----------------------------------------
1. ``sharpe_with_se`` — a Sharpe ratio with a CONFIDENCE INTERVAL. A Sharpe with no standard
   error is not a measurement. Lo (2002) also shows the usual ``sqrt(252)`` annualization is
   WRONG under autocorrelation, and by how much.
2. ``deflated_sharpe`` — the probability the strategy's true Sharpe exceeds zero, AFTER
   accounting for how many variants were tried. Bailey & Lopez de Prado (2014).
3. ``min_track_record_length`` — how long a track record must be before the observed Sharpe
   could be believed. Usually far longer than the backtest you have.
4. ``purged_kfold`` / ``probability_of_backtest_overfitting`` — validation that does not leak
   across time, and a direct estimate of how overfit a selection procedure is.

NON-NORMALITY IS ON BY DEFAULT, AND THAT IS THE POINT
-------------------------------------------------------
Every estimator here carries the skewness/kurtosis correction (Mertens 2002; Opdyke 2007)
rather than assuming Gaussian returns. A short-volatility strategy and a trend strategy at
the same Sharpe are not the same asset — the first has large negative skew and fat tails, so
its Sharpe estimate is far less reliable, and under a normal assumption that difference is
invisible. Since this package ships a variance-harvesting market maker whose payoff is
structurally short-tail, defaulting to the normal case would flatter precisely the strategy
that most needs the correction.

NONE, NOT A NUMBER, WHEN THE INPUT CANNOT SUPPORT ONE
-------------------------------------------------------
Consistent with ``signals.py``: a degenerate or too-short sample returns ``None``. A
statistic computed from four observations is not a small-sample statistic, it is noise with a
decimal point, and returning ``0.0`` would let it propagate into a tearsheet.

STDLIB ONLY
------------
``statistics.NormalDist`` supplies the normal CDF and its inverse, so this module needs no
scipy — preserving the package's zero-dependency property.

References
    Lo (2002), "The Statistics of Sharpe Ratios", Financial Analysts Journal.
    Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio", Journal of Portfolio Mgmt.
    Bailey, Borwein, Lopez de Prado & Zhu (2017), "The Probability of Backtest Overfitting",
        Journal of Computational Finance.
    Lopez de Prado (2018), "Advances in Financial Machine Learning", ch. 7 (purged CV).
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Iterable, Sequence

EULER_MASCHERONI = 0.5772156649015329
_NORMAL = NormalDist()

MAX_CSCV_PARTITIONS = 20
"""Ceiling on CSCV partitions. C(n, n/2) explodes: 16 -> 12,870, 30 -> 155 million,
40 -> 137 billion. Past this the function does not fail on a bad argument, it never returns."""

MIN_OBSERVATIONS = 20
"""Below this, every statistic here returns None.

Not a round number chosen for comfort: the Sharpe standard error goes as 1/sqrt(T), so at
T=20 a strategy with a true Sharpe of 1.0 has a standard error near 0.25 — the 95% interval
spans 0.5 to 1.5 and is compatible with 'mediocre' and 'excellent' simultaneously. Below
that the number carries no information at all, and reporting one invites it to be quoted.
"""


@dataclass(frozen=True, slots=True)
class SharpeEstimate:
    """A Sharpe ratio WITH its uncertainty. The interval is the point of the object."""

    sharpe: float
    standard_error: float
    observations: int
    periods_per_year: int
    skewness: float
    kurtosis: float
    autocorrelation_penalty: float
    """Lo's eta(q)/sqrt(q). Below 1.0 the naive sqrt(q) annualization OVERSTATES the annual
    Sharpe, which is the common case: positively autocorrelated returns (illiquidity, stale
    marks, smoothed pricing) inflate it. Getmansky, Lo & Makarov (2004) show this is how
    illiquid strategies come to report implausible Sharpes."""

    @property
    def t_statistic(self) -> float:
        return self.sharpe / self.standard_error if self.standard_error > 0 else 0.0

    def confidence_interval(self, confidence: float = 0.95) -> tuple[float, float]:
        z = _NORMAL.inv_cdf(0.5 + confidence / 2.0)
        return (self.sharpe - z * self.standard_error, self.sharpe + z * self.standard_error)

    @property
    def is_significant_at_harvey_hurdle(self) -> bool:
        """t > 3.0, not 2.0. Harvey, Liu & Zhu (2016): with hundreds of factors tested across
        the literature, the conventional 2.0 hurdle admits far too many false positives."""
        return self.t_statistic > 3.0

    def as_dict(self) -> dict:
        low, high = self.confidence_interval()
        return {
            "sharpe": round(self.sharpe, 4),
            "standard_error": round(self.standard_error, 4),
            "t_statistic": round(self.t_statistic, 3),
            "ci95_low": round(low, 4),
            "ci95_high": round(high, 4),
            "observations": self.observations,
            "skewness": round(self.skewness, 4),
            "kurtosis": round(self.kurtosis, 4),
            "autocorrelation_penalty": round(self.autocorrelation_penalty, 4),
            "clears_harvey_hurdle": self.is_significant_at_harvey_hurdle,
        }


def _moments(returns: Sequence[float]) -> tuple[float, float, float, float] | None:
    """(mean, stdev, skewness, kurtosis). Kurtosis is RAW (normal == 3), not excess.

    Raw rather than excess because every published formula below — Mertens, Bailey &
    Lopez de Prado — is written in terms of raw kurtosis, and silently swapping the
    convention shifts the variance term by exactly 0.5*SR^2.
    """
    n = len(returns)
    if n < 2:
        return None
    mean = sum(returns) / n
    deviations = [r - mean for r in returns]
    m2 = sum(d * d for d in deviations) / n
    if m2 <= 0:
        return None
    stdev = math.sqrt(m2)
    m3 = sum(d ** 3 for d in deviations) / n
    m4 = sum(d ** 4 for d in deviations) / n
    return mean, stdev, m3 / (stdev ** 3), m4 / (stdev ** 4)


def autocorrelations(returns: Sequence[float], lags: int) -> list[float]:
    """Sample autocorrelations rho_1..rho_lags. Empty when the series cannot support them."""
    n = len(returns)
    if n < 3 or lags < 1:
        return []
    mean = sum(returns) / n
    deviations = [r - mean for r in returns]
    denominator = sum(d * d for d in deviations)
    if denominator <= 0:
        return []
    out: list[float] = []
    for k in range(1, min(lags, n - 1) + 1):
        cov = sum(deviations[t] * deviations[t - k] for t in range(k, n))
        out.append(cov / denominator)
    return out


def newey_west_lags(n_observations: int) -> int:
    """Andrews/Newey-West truncation: how many sample autocorrelations to actually trust.

    THIS TRUNCATION IS NOT AN APPROXIMATION SHORTCUT — WITHOUT IT THE CORRECTION IS NOISE.

    Lo's eta(q) is exact in the POPULATION autocorrelations. Substituting sample estimates
    and summing all q-1 of them is a different and much worse estimator: each rho_k carries
    sampling error of order 1/sqrt(T), the weights (q-k) are large, and the accumulated
    variance swamps the quantity being estimated. On genuinely IID daily returns with
    T=3000, summing all 251 lags produced an annualization penalty of 1.076 — a 7.6%
    "correction" for autocorrelation that does not exist.

    Truncating at T^(1/3) trades a small downward bias (lags beyond the cut are treated as
    zero, so a true long-memory effect is under-corrected) for an estimator whose variance
    is small enough that the correction means something. The bias is in the conservative
    direction: it under-corrects rather than inventing a correction.
    """
    if n_observations < 2:
        return 0
    return max(1, round(n_observations ** (1.0 / 3.0)))


def sharpe_with_se(returns: Sequence[float], *, periods_per_year: int = 252,
                   risk_free_per_period: float = 0.0,
                   assume_normal: bool = False,
                   max_lags: int | None = None) -> SharpeEstimate | None:
    """Annualized Sharpe ratio with a standard error. ``None`` if the sample cannot support one.

    TWO CORRECTIONS THE NAIVE VERSION OMITS, both of which usually push the number DOWN:

    * AUTOCORRELATION. ``SR_annual = sqrt(q) * SR_period`` holds only for IID returns. Lo
      (2002) gives ``eta(q) = q / sqrt(q + 2*sum_{k<q}(q-k)*rho_k)``. Positive
      autocorrelation — from illiquidity, stale marks, or smoothed pricing — makes the naive
      form overstate the annual Sharpe, sometimes by a large factor.
    * NON-NORMALITY. The textbook ``Var(SR) = (1 + SR^2/2)/T`` assumes Gaussian returns. The
      Mertens/Opdyke form adds skewness and kurtosis terms. For a negatively-skewed,
      fat-tailed payoff — which is what harvesting a variance risk premium produces — the
      standard error is materially larger, so the confidence interval widens exactly where
      the strategy's risk is concentrated.

    ``assume_normal=True`` drops the second correction. It exists to demonstrate the size of
    the difference in tests, not as a modelling option worth choosing.
    """
    if len(returns) < MIN_OBSERVATIONS:
        return None
    excess = [r - risk_free_per_period for r in returns]
    moments = _moments(excess)
    if moments is None:
        return None
    mean, stdev, skewness, kurtosis = moments

    n = len(excess)
    sharpe_period = mean / stdev

    # Lo's eta(q): the autocorrelation-aware annualization scale factor. Lags are TRUNCATED
    # (see newey_west_lags) — summing all q-1 sample autocorrelations makes this estimator
    # noise-dominated rather than more accurate.
    q = periods_per_year
    lags = max_lags if max_lags is not None else newey_west_lags(n)
    rho = autocorrelations(excess, lags=min(lags, q - 1))

    # ONLY CORRECT FOR AUTOCORRELATION THAT IS ACTUALLY THERE.
    #
    # Truncation alone is not enough. The weights (q-k) are of order q, so annualizing daily
    # returns amplifies each rho_k by ~250x — and a sample autocorrelation on white noise has
    # a standard error of 1/sqrt(T), which at T=3000 is 0.018. Amplified, that alone moved the
    # measured penalty to 0.89 on genuinely IID data: an 11% "correction" for an effect that
    # does not exist.
    #
    # So each lag is tested against the Bartlett band (+/- 2/sqrt(T), the ~95% interval for
    # white noise) and lags inside it are treated as exactly zero. On IID returns the
    # correction correctly vanishes; a real AR(1) with phi=0.5 sits an order of magnitude
    # outside the band and survives untouched. The asymmetry is deliberate — this estimator
    # should decline to correct when unsure, never invent a correction from noise.
    band = 2.0 / math.sqrt(n)
    weighted = sum((q - k) * rho[k - 1]
                   for k in range(1, len(rho) + 1)
                   if abs(rho[k - 1]) > band)
    denominator = q + 2.0 * weighted
    if denominator <= 0:
        # Autocorrelation strong and negative enough to make the scale undefined. Refuse
        # rather than fall back to sqrt(q), which would silently report the number the
        # correction exists to replace.
        return None
    eta = q / math.sqrt(denominator)
    sharpe_annual = eta * sharpe_period

    if assume_normal:
        variance = (1.0 + 0.5 * sharpe_period ** 2) / (n - 1)
    else:
        variance = (
            1.0
            - skewness * sharpe_period
            + 0.25 * (kurtosis - 1.0) * sharpe_period ** 2
        ) / (n - 1)
    if variance <= 0:
        return None

    return SharpeEstimate(
        sharpe=sharpe_annual,
        standard_error=eta * math.sqrt(variance),
        observations=n,
        periods_per_year=q,
        skewness=skewness,
        kurtosis=kurtosis,
        autocorrelation_penalty=eta / math.sqrt(q),
    )


def expected_max_sharpe(trial_sharpes: Sequence[float]) -> float | None:
    """The Sharpe you EXPECT to see from the best of N independent worthless strategies.

    This is the null the deflated Sharpe tests against, and it is startlingly high: searching
    100 variants of a strategy with no edge whatsoever still produces a best-of-100 Sharpe
    around 2.5 standard deviations above zero. Reporting that number without deflating it is
    the single most common way a backtest lies.

    Bailey & Lopez de Prado (2014), using the expected value of the maximum of N draws from
    a standard normal.
    """
    n = len(trial_sharpes)
    if n < 2:
        return None
    moments = _moments(trial_sharpes)
    if moments is None:
        return None
    _mean, stdev, _skew, _kurt = moments
    return stdev * (
        (1.0 - EULER_MASCHERONI) * _NORMAL.inv_cdf(1.0 - 1.0 / n)
        + EULER_MASCHERONI * _NORMAL.inv_cdf(1.0 - 1.0 / (n * math.e))
    )


def deflated_sharpe(returns: Sequence[float], *, trial_sharpes: Sequence[float],
                    periods_per_year: int = 252) -> float | None:
    """P(true Sharpe > 0) after accounting for the number of variants tried.

    ``trial_sharpes`` is EVERY Sharpe you computed while searching — not just the winner.
    It is deliberately REQUIRED and has no default, because the honest default does not
    exist: a caller who cannot say how many variants they tried cannot deflate, and silently
    assuming one trial would assert the very thing the method exists to check.

    Returns a probability. Below ~0.95 the result should not be treated as evidence of skill.
    """
    if len(returns) < MIN_OBSERVATIONS or len(trial_sharpes) < 2:
        return None
    moments = _moments(returns)
    if moments is None:
        return None
    mean, stdev, skewness, kurtosis = moments
    sharpe_period = mean / stdev
    threshold = expected_max_sharpe(trial_sharpes)
    if threshold is None:
        return None

    n = len(returns)
    variance = 1.0 - skewness * sharpe_period + 0.25 * (kurtosis - 1.0) * sharpe_period ** 2
    if variance <= 0:
        return None
    statistic = ((sharpe_period - threshold) * math.sqrt(n - 1)) / math.sqrt(variance)
    return _NORMAL.cdf(statistic)


def min_track_record_length(returns: Sequence[float], *, target_sharpe: float = 0.0,
                            confidence: float = 0.95) -> float | None:
    """How many observations you would need for the observed Sharpe to clear ``target_sharpe``.

    Answers "is my backtest long enough?" with a number rather than a shrug. Compare it to
    the length you actually have; the usual result is uncomfortable, which is the value.

    ``None`` when the observed Sharpe does not exceed the target at all — no amount of
    additional data makes a negative result significant in the positive direction.
    """
    if len(returns) < MIN_OBSERVATIONS:
        return None
    moments = _moments(returns)
    if moments is None:
        return None
    mean, stdev, skewness, kurtosis = moments
    sharpe = mean / stdev
    if sharpe <= target_sharpe:
        return None
    z = _NORMAL.inv_cdf(confidence)
    variance = 1.0 - skewness * sharpe + 0.25 * (kurtosis - 1.0) * sharpe ** 2
    if variance <= 0:
        return None
    return 1.0 + variance * (z / (sharpe - target_sharpe)) ** 2


# ---- validation that respects time ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fold:
    train: tuple[int, ...]
    test: tuple[int, ...]


def purged_kfold(n_observations: int, *, n_splits: int = 5, horizon: int = 1,
                 embargo: int = 0) -> list[Fold]:
    """K-fold splits with PURGING and an EMBARGO, for serially dependent observations.

    Plain k-fold is invalid on financial data and invalid in a way that flatters you. A label
    observed at ``t`` typically depends on prices through ``t + horizon``; if ``t`` sits in
    the training set and ``t + horizon`` in the test set, the model has seen its own answer.
    The leak inflates measured performance, so the failure mode is a validation score that is
    too good — never one that warns you.

    Two defences, and both are needed:

    * PURGE — drop training observations whose label window ``[i, i+horizon]`` overlaps the
      test block at either end.
    * EMBARGO — additionally drop a buffer AFTER the test block. Purging handles overlap;
      the embargo handles serial correlation that outlives the label window, which overlap
      arithmetic alone cannot see. Asymmetric on purpose — information flows forward.

    Lopez de Prado (2018), ch. 7.
    """
    if n_observations < n_splits or n_splits < 2:
        return []
    if horizon < 0 or embargo < 0:
        raise ValueError(f"horizon and embargo must be non-negative, got {horizon}, {embargo}")

    indices = list(range(n_observations))
    size, remainder = divmod(n_observations, n_splits)
    folds: list[Fold] = []
    start = 0
    for split in range(n_splits):
        stop = start + size + (1 if split < remainder else 0)
        test = indices[start:stop]
        train = [
            i for i in indices
            # purge: the label window of a training point must not reach into the test block
            if (i + horizon < test[0] or i > test[-1] + embargo)
        ]
        folds.append(Fold(train=tuple(train), test=tuple(test)))
        start = stop
    return folds


def probability_of_backtest_overfitting(performance: Sequence[Sequence[float]], *,
                                        n_partitions: int = 8) -> float | None:
    """PBO via combinatorially symmetric cross-validation (CSCV).

    Takes a matrix of per-period returns — one row per PERIOD, one column per STRATEGY
    VARIANT — and asks a single question: when you pick the in-sample winner, how often does
    it land below the median out-of-sample?

    A PBO near 0.5 means your selection procedure is indistinguishable from choosing at
    random, which is the honest description of most backtest-driven parameter searches. The
    output is a property of the SEARCH, not of any one strategy — which is why it needs the
    whole matrix and cannot be computed from the winner alone.

    ``n_partitions`` must be even. The paper uses 16 (12,870 combinations); the default of 8
    (70 combinations) is fast enough to run every time, which matters more than precision
    here — a diagnostic nobody runs has no value.

    Bailey, Borwein, Lopez de Prado & Zhu (2017).
    """
    if n_partitions % 2 != 0 or n_partitions < 2:
        raise ValueError(f"n_partitions must be a positive even number, got {n_partitions}")
    if n_partitions > MAX_CSCV_PARTITIONS:
        # C(n, n/2) grows explosively: 16 -> 12,870 combinations, 30 -> 155 million,
        # 40 -> 137 BILLION. Without a ceiling this function does not fail on a bad argument,
        # it simply never returns — and a diagnostic that hangs is worse than one that
        # refuses, because the caller cannot tell it from a slow dataset.
        raise ValueError(
            f"n_partitions {n_partitions} exceeds {MAX_CSCV_PARTITIONS}: that is "
            f"{math.comb(n_partitions, n_partitions // 2):,} combinations and would not "
            "terminate in useful time. The paper uses 16."
        )
    rows = len(performance)
    if rows < n_partitions:
        return None
    n_strategies = len(performance[0]) if rows else 0
    if n_strategies < 2 or any(len(r) != n_strategies for r in performance):
        return None

    size = rows // n_partitions
    blocks = [performance[i * size:(i + 1) * size] for i in range(n_partitions)]

    def _sharpes(block_indices: Iterable[int]) -> list[float] | None:
        columns: list[list[float]] = [[] for _ in range(n_strategies)]
        for b in block_indices:
            for row in blocks[b]:
                for s in range(n_strategies):
                    columns[s].append(row[s])
        out: list[float] = []
        for column in columns:
            moments = _moments(column)
            # A degenerate variant (flat returns) gets a Sharpe of 0 rather than removing the
            # whole combination — dropping combinations would bias which splits are counted.
            out.append(0.0 if moments is None else moments[0] / moments[1])
        return out

    logits: list[float] = []
    half = n_partitions // 2
    for combination in itertools.combinations(range(n_partitions), half):
        in_sample = _sharpes(combination)
        out_sample = _sharpes([b for b in range(n_partitions) if b not in combination])
        if in_sample is None or out_sample is None:
            continue
        best = max(range(n_strategies), key=lambda s: in_sample[s])
        # Relative rank of the IS winner within the OOS results, in (0, 1).
        worse = sum(1 for s in range(n_strategies) if out_sample[s] <= out_sample[best])
        omega = worse / (n_strategies + 1)
        omega = min(max(omega, 1e-9), 1 - 1e-9)
        logits.append(math.log(omega / (1.0 - omega)))

    if not logits:
        return None
    return sum(1 for value in logits if value <= 0) / len(logits)


def returns_from_equity(equity: Sequence[float]) -> list[float]:
    """Simple period returns from an ACCOUNT-VALUE curve, skipping non-positive denominators.

    Only correct when ``equity`` is a capital base — a positive account value. For a
    cumulative PnL path use ``pnl_increments`` instead; see its docstring for why this
    function is the wrong tool there and how quietly it fails.
    """
    out: list[float] = []
    for previous, current in zip(equity, equity[1:]):
        if previous > 0:
            out.append(current / previous - 1.0)
    return out


def pnl_increments(equity: Sequence[float]) -> list[float]:
    """Period-over-period P&L CHANGES from a cumulative PnL curve.

    THE RIGHT TOOL FOR A PNL PATH, AND ``returns_from_equity`` IS NOT.

    A cumulative PnL series starts at zero and may be negative for long stretches — there is
    no capital base to divide by. Feeding it to ``returns_from_equity`` does not raise; it
    silently DISCARDS every sample taken while cumulative PnL was at or below zero, because
    those have no usable denominator. A losing strategy therefore has almost its entire
    history filtered out and reports statistics computed from the handful of samples where it
    happened to be up — a survivorship filter on your own equity curve, and one that flatters
    exactly the runs that deserve it least. (This is not hypothetical: it silently reduced a
    500-sample market-maker run to 16 samples.)

    A Sharpe on P&L increments is dimensionally sound — mean increment over the standard
    deviation of increments — and is how desks with no natural capital denominator (market
    making, futures) quote it.
    """
    return [current - previous for previous, current in zip(equity, equity[1:])]
