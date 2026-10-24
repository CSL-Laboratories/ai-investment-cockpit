"""Freeze a versioned calibration artifact, or refuse to.

The cockpit's pinned document is ``wilson95_heldout_worst_case_prices_v2``. This artifact is
a different method and must not be loaded as ``MergerCalibrationEvidence``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sidecar.jsonutil import sha256_canonical, validate
from sidecar.merger.model import brier_score, fit_logistic, log_loss, predict_probability

SCHEMA = Path(__file__).with_name("calibration.schema.json")
METHOD = "irls_l2_logistic_v1"


def wilson_lower_bound(successes: int, n: int, z: Decimal = Decimal("1.96")) -> Decimal:
    """95% Wilson lower bound, matching the arithmetic in ``merger_edge``.

    Exposed so a later retrofit can compare this model with the pinned cohort method.
    It is not a substitute for that method's full cohort contract.
    """
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("Wilson inputs are out of range")
    count = Decimal(n)
    p_hat = Decimal(successes) / count
    z2 = z * z
    return (
        p_hat
        + z2 / (Decimal(2) * count)
        - z * ((p_hat * (1 - p_hat) + z2 / (Decimal(4) * count)) / count).sqrt()
    ) / (1 + z2 / count)


def _cutoff(value: str) -> date:
    return date.fromisoformat(value)


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def expected_return(probability: Decimal, spread: Decimal, break_loss: Decimal) -> Decimal:
    """``p * spread - (1 - p) * break_loss``. ``break_loss`` is a positive magnitude."""
    if not (Decimal(0) < probability < Decimal(1)):
        raise ValueError("probability must be in (0, 1)")
    if break_loss < 0:
        raise ValueError("break_loss magnitude must be non-negative")
    return probability * spread - (1 - probability) * break_loss


def _float_str(value: float) -> str:
    return format(value, ".17g")


def fit_calibration(
    observations: list[dict],
    *,
    training_cutoff: str,
    l2: float = 1.0,
    min_train: int = 20,
    min_class: int = 5,
    min_breaks: int = 5,
    allow_synthetic: bool = False,
) -> dict:
    cutoff = _cutoff(training_cutoff)
    if any(item.get("synthetic") for item in observations) and not allow_synthetic:
        raise ValueError("synthetic cards require allow_synthetic=True and must be labeled")
    train = []
    test = []
    censored = 0
    for row in observations:
        observed = _when(row["observed_at"]).date()
        if row["outcome"] == "open" or row["outcome_at"] is None:
            censored += 1
            continue
        resolved = _when(row["outcome_at"]).date()
        if resolved <= cutoff and observed <= cutoff:
            train.append(row)
        elif observed > cutoff and resolved > observed:
            test.append(row)
        else:
            censored += 1
    labels = [1 if row["outcome"] == "completed" else 0 for row in train]
    positives = sum(labels)
    negatives = len(labels) - positives
    if len(train) < min_train or positives < min_class or negatives < min_class:
        raise ValueError(
            "refusing to freeze: training sample or class count is below the minimum"
        )
    breaks = []
    for row in train:
        if row["outcome"] != "broken":
            continue
        entry = Decimal(row["market_price"])
        final = Decimal(row["outcome_price"])
        breaks.append((final - entry) / entry)
    if len(breaks) < min_breaks:
        raise ValueError("refusing to freeze: not enough broken deals to estimate break loss")
    ordered = sorted(breaks)
    # Nearest-rank 10th percentile of broken-deal returns (the worse tail).
    index = max(0, math_ceil(0.10 * len(ordered)) - 1)
    p10 = ordered[index]
    loss = -p10 if p10 < 0 else Decimal(0)
    model = fit_logistic(train, labels, l2=l2)
    train_pairs = [(predict_probability(model, row), label) for row, label in zip(train, labels, strict=True)]
    test_labels = [1 if row["outcome"] == "completed" else 0 for row in test]
    test_pairs = [
        (predict_probability(model, row), label) for row, label in zip(test, test_labels, strict=True)
    ]
    base_rate = positives / len(labels)
    train_base_pairs = [(base_rate, label) for label in labels]
    artifact = {
        "schema_version": "sidecar.merger_calibration.v1",
        "method": METHOD,
        "frozen": True,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "training_cutoff": cutoff.isoformat(),
        "dataset_hash": sha256_canonical(observations),
        "synthetic": any(row.get("synthetic") for row in observations),
        "yield_claim": False,
        "n_train": len(train),
        "n_test": len(test),
        "n_censored": censored,
        "train_completed": positives,
        "train_broken": negatives,
        "l2": _float_str(l2),
        "feature_names": model["feature_names"],
        "kept_indices": model["kept_indices"],
        "means": [_float_str(value) for value in model["means"]],
        "stds": [_float_str(value) for value in model["stds"]],
        "intercept": _float_str(model["intercept"]),
        "coefficients": [_float_str(value) for value in model["coefficients"]],
        "break_loss": {
            "n": len(breaks),
            "quantile": "p10",
            "p10_return": format(p10, "f"),
            "magnitude": format(loss, "f"),
        },
        "metrics": {
            "train_brier": _float_str(brier_score(train_pairs)),
            "train_log_loss": _float_str(log_loss(train_pairs)),
            "train_base_rate_brier": _float_str(brier_score(train_base_pairs)),
            "test_brier": None if not test_pairs else _float_str(brier_score(test_pairs)),
            "test_log_loss": None if not test_pairs else _float_str(log_loss(test_pairs)),
            "test_base_rate": _float_str(base_rate),
        },
        "expected_return_formula": "p_complete * spread - (1 - p_complete) * break_loss_magnitude",
        "retrofit": {
            "cockpit_module": "app/strategies/merger_edge.py",
            "cockpit_schema": "merger_calibration.v2",
            "cockpit_pinned_method": "wilson95_heldout_worst_case_prices_v2",
            "compatible_without_amendment": False,
            "reason": (
                "The cockpit accepts only the pinned Wilson worst-case document. "
                "This file is a deal-level logistic model."
            ),
        },
    }
    stored = {
        "intercept": artifact["intercept"],
        "coefficients": artifact["coefficients"],
        "means": artifact["means"],
        "stds": artifact["stds"],
        "kept_indices": artifact["kept_indices"],
    }
    # Round-trip: scoring from the stored strings must match the in-memory fit.
    for row, (probability, _label) in zip(train, train_pairs, strict=True):
        again = predict_probability(stored, row)
        if abs(again - probability) > 1e-12:
            raise ValueError("stored coefficients do not reproduce the fit")
    return validate(artifact, SCHEMA)


def math_ceil(value: float) -> int:
    import math

    return math.ceil(value)


def score_deal(artifact: dict, observation: dict) -> dict:
    """Score one already-built observation. Does not look at its outcome."""
    validate(artifact, SCHEMA)
    if artifact["method"] != METHOD or artifact["frozen"] is not True:
        raise ValueError("artifact is not a frozen logistic calibration")
    probability = predict_probability(artifact, observation)
    spread = Decimal(observation["spread"])
    loss = Decimal(artifact["break_loss"]["magnitude"])
    quantized = Decimal(_float_str(probability))
    return {
        "deal_id": observation["deal_id"],
        "p_complete": format(quantized, "f"),
        "spread": format(spread, "f"),
        "break_loss_magnitude": format(loss, "f"),
        "expected_return": format(expected_return(quantized, spread, loss), "f"),
    }
