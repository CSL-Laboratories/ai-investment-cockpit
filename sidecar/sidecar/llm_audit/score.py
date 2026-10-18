"""Brier, reliability, Spearman IC, and a constant-0.5 null.

A row is scored only when ``outcome_at`` is strictly after ``as_of``. If the row names a
model training cutoff, ``as_of`` must also be strictly after that cutoff. Everything else
is counted and excluded.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sidecar.jsonutil import validate

SCHEMA = Path(__file__).with_name("scorecard.schema.json")


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("judgment timestamps must be timezone-aware")
    return parsed


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(values):
        end = index
        while end + 1 < len(values) and values[order[end + 1]] == values[order[index]]:
            end += 1
        # Average rank, 1-based.
        rank = (index + end) / 2.0 + 1.0
        for cursor in range(index, end + 1):
            ranks[order[cursor]] = rank
        index = end + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    var_x = sum((item - x_mean) ** 2 for item in xs)
    var_y = sum((item - y_mean) ** 2 for item in ys)
    if var_x <= 0.0 or var_y <= 0.0:
        return None
    cov = sum((a - x_mean) * (b - y_mean) for a, b in zip(xs, ys, strict=True))
    return cov / math.sqrt(var_x * var_y)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys):
        raise ValueError("IC inputs must be aligned")
    return _pearson(_ranks(xs), _ranks(ys))


def _brier(pairs: list[tuple[float, int]]) -> float:
    return sum((prob - label) ** 2 for prob, label in pairs) / len(pairs)


def _reliability(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for prob, label in pairs:
        index = min(bins - 1, max(0, int(prob * bins)))
        buckets[index].append((prob, label))
    curve = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        curve.append(
            {
                "bin_low": format(index / bins, "f"),
                "bin_high": format((index + 1) / bins, "f"),
                "n": len(bucket),
                "mean_predicted": format(sum(prob for prob, _ in bucket) / len(bucket), ".6f"),
                "fraction_up": format(sum(label for _, label in bucket) / len(bucket), ".6f"),
            }
        )
    return curve


def _fmt(value: float | None) -> str | None:
    if value is None:
        return None
    if not math.isfinite(value):
        raise ValueError("non-finite score")
    return format(value, ".12g")


def score_judgments(rows: list[dict]) -> dict:
    excluded = defaultdict(int)
    by_route: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("outcome_up") is None or row.get("outcome_at") is None:
            excluded["unresolved"] += 1
            continue
        try:
            as_of = _dt(row["as_of"])
            outcome_at = _dt(row["outcome_at"])
        except (TypeError, ValueError):
            excluded["bad_timestamp"] += 1
            continue
        if outcome_at <= as_of:
            excluded["outcome_not_after_as_of"] += 1
            continue
        cutoff = row.get("model_training_cutoff")
        if cutoff:
            try:
                cutoff_at = _dt(cutoff if "T" in str(cutoff) else f"{cutoff}T00:00:00+00:00")
            except ValueError:
                excluded["bad_timestamp"] += 1
                continue
            if as_of <= cutoff_at:
                excluded["as_of_not_after_training_cutoff"] += 1
                continue
        label = row["outcome_up"]
        if label not in (0, 1, True, False):
            excluded["bad_label"] += 1
            continue
        by_route[str(row["route"])].append(row)

    routes = []
    for route, items in sorted(by_route.items()):
        pairs = [(float(item["probability"]), int(item["outcome_up"])) for item in items]
        returns = [
            float(item["outcome_return"])
            for item in items
            if item.get("outcome_return") is not None
        ]
        scores = [
            float(item["probability"])
            for item in items
            if item.get("outcome_return") is not None
        ]
        baselines = [
            (float(item["baseline_probability"]), int(item["outcome_up"]))
            for item in items
            if item.get("baseline_probability") is not None
        ]
        model_brier = _brier(pairs)
        null_pairs = [(0.5, label) for _, label in pairs]
        null_brier = _brier(null_pairs)
        routes.append(
            {
                "route": route,
                "n": len(pairs),
                "brier": _fmt(model_brier),
                "null_brier_constant_half": _fmt(null_brier),
                "brier_minus_null": _fmt(model_brier - null_brier),
                "beats_null": model_brier < null_brier,
                "baseline_brier": None if not baselines else _fmt(_brier(baselines)),
                "log_loss": _fmt(
                    sum(
                        -(
                            label * math.log(min(1 - 1e-12, max(1e-12, prob)))
                            + (1 - label) * math.log(min(1 - 1e-12, max(1e-12, 1 - prob)))
                        )
                        for prob, label in pairs
                    )
                    / len(pairs)
                ),
                "reliability": _reliability(pairs),
                "information_coefficient": _fmt(spearman(scores, returns) if len(scores) >= 3 else None),
            }
        )
    document = {
        "schema_version": "sidecar.route_scorecard.v1",
        "threshold_status": "not_frozen",
        "yield_claim": False,
        "n_rows": len(rows),
        "n_scored": sum(item["n"] for item in routes),
        "excluded": dict(excluded),
        "routes": routes,
        "notes": [
            "Scored rows have outcome_at strictly after as_of.",
            "Rows with a model_training_cutoff are scored only when as_of is after that cutoff.",
            "The null is a constant probability of 0.5. beats_null is descriptive, not a promotion.",
        ],
    }
    return validate(document, SCHEMA)
