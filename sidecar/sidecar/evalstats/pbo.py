"""Probability of backtest overfitting by combinatorially symmetric cross-validation.

Bailey, Borwein, López de Prado, and Zhu: split the time axis into an even number of
slices, score every strategy on each half-split, and ask how often the in-sample winner
fails to beat the median out of sample. Complementary splits are counted once.

This is the original CSCV. It does not purge or embargo overlapping labels. Pass returns
that do not overlap if the labels do.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence

from sidecar.evalstats.core import EvalStatsError


def _sharpe(values: list[float]) -> float:
    """Per-period Sharpe used only to rank configurations. Two points are enough."""
    n = len(values)
    if n < 2:
        raise EvalStatsError("need at least 2 returns to rank a split")
    mean = sum(values) / n
    var = sum((value - mean) ** 2 for value in values) / (n - 1)
    if var <= 0.0:
        raise EvalStatsError("zero variance")
    return mean / math.sqrt(var)


def probability_of_overfitting(
    trials: Sequence[Sequence[float]],
    *,
    n_slices: int = 16,
) -> dict:
    if len(trials) < 2:
        raise EvalStatsError("PBO needs at least two strategy configurations")
    width = len(trials[0])
    if width == 0 or any(len(series) != width for series in trials):
        raise EvalStatsError("trial series must be non-empty and aligned")
    if n_slices < 2 or n_slices % 2 != 0:
        raise EvalStatsError("n_slices must be an even integer >= 2")
    if width < n_slices:
        raise EvalStatsError("not enough observations for the requested slice count")
    usable = width - (width % n_slices)
    start = width - usable
    series = [[float(value) for value in column[start:]] for column in trials]
    slice_len = usable // n_slices
    splits = []
    half = n_slices // 2
    for combo in itertools.combinations(range(n_slices), half):
        complement = tuple(index for index in range(n_slices) if index not in combo)
        if combo < complement:
            splits.append((combo, complement))

    def gather(column: list[float], chosen: tuple[int, ...]) -> list[float]:
        out: list[float] = []
        for slice_id in sorted(chosen):
            left = slice_id * slice_len
            out.extend(column[left : left + slice_len])
        return out

    logits: list[float] = []
    selected_oos: list[float] = []
    overfit = 0
    used = 0
    n_strategies = len(series)
    for ins_ids, oos_ids in splits:
        try:
            ins = [_sharpe(gather(column, ins_ids)) for column in series]
            oos = [_sharpe(gather(column, oos_ids)) for column in series]
        except EvalStatsError:
            continue
        used += 1
        best = max(range(n_strategies), key=lambda index: (ins[index], -index))
        selected = oos[best]
        selected_oos.append(selected)
        ordered = sorted(oos)
        midpoint = 0.5 * (ordered[(n_strategies - 1) // 2] + ordered[n_strategies // 2])
        if selected <= midpoint:
            overfit += 1
        worse = sum(1 for value in oos if value < selected)
        tied = sum(1 for value in oos if value == selected)
        # Midrank among ties, in (0, N], then scale by N+1 so the logit is finite.
        rank = worse + (tied + 1) / 2.0
        omega = rank / (n_strategies + 1)
        omega = min(max(omega, 1e-12), 1.0 - 1e-12)
        logits.append(math.log(omega / (1.0 - omega)))

    if used < 1 or used * 2 < len(splits):
        raise EvalStatsError("too many CSCV splits had zero variance")
    logits_sorted = sorted(logits)
    median_logit = logits_sorted[len(logits_sorted) // 2]
    return {
        "status": "ok",
        "n_strategies": n_strategies,
        "n_slices": n_slices,
        "n_splits": used,
        "observations_used": usable,
        "observations_dropped_prefix": start,
        "pbo": overfit / used,
        "mean_oos_sharpe_of_selected": sum(selected_oos) / len(selected_oos),
        "median_logit": median_logit,
    }
