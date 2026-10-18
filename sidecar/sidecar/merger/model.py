"""L2-regularized logistic regression by iteratively reweighted least squares.

Coefficients are fit in float and then stored as shortest round-trip strings. The artifact,
not a later refit, is what ``predict`` uses. The intercept is not penalized. Features are
standardized on the training rows only.
"""

from __future__ import annotations

import math

FEATURE_NAMES = (
    "cash",
    "mixed",
    "hostile",
    "regulatory_review",
    "spread",
    "log_days_to_outside",
    "filing_intensity",
)


class FitError(RuntimeError):
    """The logistic fit did not meet the freeze conditions."""


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        exp = math.exp(-value)
        return 1.0 / (1.0 + exp)
    exp = math.exp(value)
    return exp / (1.0 + exp)


def feature_vector(row: dict) -> list[float]:
    spread = float(row["spread"])
    spread = min(1.0, max(-0.5, spread))
    days = max(0, int(row["days_to_outside"]))
    filings = min(5, int(row["merger_filing_count"])) / 5.0
    return [
        1.0 if row["consideration"] == "cash" else 0.0,
        1.0 if row["consideration"] == "mixed" else 0.0,
        1.0 if row["hostile"] else 0.0,
        1.0 if row["regulatory_review"] else 0.0,
        spread,
        math.log1p(days),
        filings,
    ]


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    size = len(rhs)
    aug = [row[:size] + [rhs[index]] for index, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise FitError("IRLS system is singular")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        for index in range(col, size + 1):
            aug[col][index] /= scale
        for row in range(size):
            if row == col:
                continue
            factor = aug[row][col]
            if factor == 0.0:
                continue
            for index in range(col, size + 1):
                aug[row][index] -= factor * aug[col][index]
    return [aug[row][size] for row in range(size)]


def _standardize(
    raw: list[list[float]],
) -> tuple[list[list[float]], list[float], list[float], list[int]]:
    width = len(raw[0])
    means = []
    stds = []
    kept = []
    for col in range(width):
        column = [row[col] for row in raw]
        mean = sum(column) / len(column)
        var = sum((item - mean) ** 2 for item in column) / len(column)
        std = math.sqrt(var)
        means.append(mean)
        stds.append(std)
        if std > 1e-12:
            kept.append(col)
    scaled = []
    for row in raw:
        scaled.append([(row[col] - means[col]) / stds[col] for col in kept])
    return scaled, means, stds, kept


def fit_logistic(
    rows: list[dict],
    labels: list[int],
    *,
    l2: float = 1.0,
    max_iter: int = 50,
) -> dict:
    if len(rows) != len(labels) or len(rows) < 4:
        raise FitError("need aligned rows and labels")
    if any(label not in (0, 1) for label in labels):
        raise FitError("labels must be 0 or 1")
    if len(set(labels)) < 2:
        raise FitError("training labels are a single class")
    raw = [feature_vector(row) for row in rows]
    scaled, means, stds, kept = _standardize(raw)
    if not kept:
        raise FitError("every feature is constant on the training set")
    width = len(kept) + 1
    beta = [0.0] * width
    for _ in range(max_iter):
        weights = []
        working = []
        for row, label in zip(scaled, labels, strict=True):
            design = [1.0] + row
            linear = sum(coef * value for coef, value in zip(beta, design, strict=True))
            probability = min(1.0 - 1e-12, max(1e-12, _sigmoid(linear)))
            weight = probability * (1.0 - probability)
            weights.append(weight)
            working.append(linear + (label - probability) / weight)
        xtwx = [[0.0] * width for _ in range(width)]
        xtwz = [0.0] * width
        for row, weight, target in zip(scaled, weights, working, strict=True):
            design = [1.0] + row
            for i in range(width):
                xtwz[i] += design[i] * weight * target
                for j in range(width):
                    xtwx[i][j] += design[i] * weight * design[j]
        for index in range(1, width):
            xtwx[index][index] += l2
        updated = _solve(xtwx, xtwz)
        delta = max(abs(a - b) for a, b in zip(updated, beta, strict=True))
        beta = updated
        if delta < 1e-10:
            break
    else:
        raise FitError("IRLS did not converge")
    return {
        "feature_names": [FEATURE_NAMES[index] for index in kept],
        "kept_indices": kept,
        "means": [means[index] for index in kept],
        "stds": [stds[index] for index in kept],
        "intercept": beta[0],
        "coefficients": beta[1:],
        "l2": l2,
    }


def predict_probability(model: dict, row: dict) -> float:
    raw = feature_vector(row)
    linear = float(model["intercept"])
    for index, coef, mean, std in zip(
        model["kept_indices"],
        model["coefficients"],
        model["means"],
        model["stds"],
        strict=True,
    ):
        linear += float(coef) * ((raw[index] - float(mean)) / float(std))
    return min(1.0 - 1e-12, max(1e-12, _sigmoid(linear)))


def brier_score(pairs: list[tuple[float, int]]) -> float:
    if not pairs:
        raise FitError("no scored rows")
    return sum((prob - label) ** 2 for prob, label in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, int]]) -> float:
    if not pairs:
        raise FitError("no scored rows")
    total = 0.0
    for prob, label in pairs:
        clipped = min(1.0 - 1e-12, max(1e-12, prob))
        total += -(label * math.log(clipped) + (1 - label) * math.log(1.0 - clipped))
    return total / len(pairs)
