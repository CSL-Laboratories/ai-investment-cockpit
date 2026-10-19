"""P&L intervals, goal-suit log loss, and fill markouts.

Mean P&L uses a normal interval. When there are at least eight games the
stationary bootstrap from ``evalstats`` is added. A bootstrap failure is
reported as unavailable. Negative markout is adverse, the same sign as the
market-making scorer: ``sign(side) * (reference - fill_price)`` with buy = +1.
"""

from __future__ import annotations

import math

from sidecar.evalstats.core import EvalStatsError, stationary_bootstrap_ci
from sidecar.figgie_bench.rules import GOAL_CARD_CENTS, Fill


def normal_ci(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "low": None, "high": None, "method": "normal"}
    mean = sum(values) / n
    if n < 2:
        return {"n": n, "mean": mean, "low": None, "high": None, "method": "normal"}
    variance = sum((item - mean) ** 2 for item in values) / (n - 1)
    half = 1.96 * math.sqrt(variance / n)
    return {"n": n, "mean": mean, "low": mean - half, "high": mean + half, "method": "normal"}


def bootstrap_mean_ci(values: list[float], *, seed: int) -> dict | None:
    try:
        result = stationary_bootstrap_ci(values, reps=50, seed=seed)
    except EvalStatsError as exc:
        return {"status": "unavailable", "reason": str(exc)}
    low, high = result["mean_return_ci95"]
    return {
        "status": "ok",
        "method": "stationary",
        "low": low,
        "high": high,
        "reps_used": result["reps_used"],
        "seed": seed,
    }


def markouts(fills: list[Fill], seat: int, goal: str, *, window: int = 3) -> list[dict]:
    rows = []
    for index, fill in enumerate(fills):
        if seat not in (fill.buyer, fill.seller):
            continue
        later = next((item for item in fills[index + 1 : index + 1 + window] if item.suit == fill.suit), None)
        if later is None:
            reference = GOAL_CARD_CENTS if fill.suit == goal else 0
            source = "terminal_fundamental"
        else:
            reference = later.price_cents
            source = "next_trade"
        sign = 1.0 if seat == fill.buyer else -1.0
        rows.append(
            {
                "suit": fill.suit,
                "source": source,
                "markout": sign * (reference - fill.price_cents) / 100.0,
            }
        )
    return rows


def mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
