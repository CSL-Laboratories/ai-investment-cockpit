"""Fit a three-parameter Almgren-style cost on arrival shortfall.

Specification ``almgren_linear_v1``, in units of daily volatility::

    arrival_shortfall_fraction / sigma = epsilon + eta * sqrt(q / ADV) + gamma * (q / ADV)

``epsilon`` absorbs a spread-like term scaled by volatility. ``eta`` is temporary
square-root impact. ``gamma`` is permanent impact linear in participation. Rows with
non-positive sigma or ADV are dropped. Fewer than eight usable rows, or a non-positive
impact coefficient, does not produce recommended parameters.
"""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path

from sidecar.jsonutil import validate
from sidecar.tca.shortfall import TCAError, implementation_shortfall

SCHEMA = Path(__file__).with_name("cost_params.schema.json")
MIN_ROWS = 8


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    size = len(rhs)
    aug = [row[:] + [rhs[index]] for index, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-14:
            raise TCAError("impact design is singular")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        for index in range(col, size + 1):
            aug[col][index] /= scale
        for row in range(size):
            if row == col:
                continue
            factor = aug[row][col]
            for index in range(col, size + 1):
                aug[row][index] -= factor * aug[col][index]
    return [aug[row][size] for row in range(size)]


def _num(value: float) -> str:
    if not math.isfinite(value):
        raise TCAError("non-finite impact coefficient")
    return format(value, ".12g")


def fit_impact(fills: list[dict]) -> dict:
    design_rows: list[list[float]] = []
    targets: list[float] = []
    for fill in fills:
        sigma = fill.get("sigma")
        adv = fill.get("adv")
        if sigma is None or adv is None:
            continue
        sigma_f = float(sigma)
        adv_f = float(adv)
        qty = float(fill["qty"])
        if sigma_f <= 0.0 or adv_f <= 0.0 or qty <= 0.0:
            continue
        part = qty / adv_f
        if part <= 0.0 or part > 1.0:
            continue
        costed = implementation_shortfall(fill)
        # Arrival shortfall excluding the fee, as a fraction of arrival notional.
        arrival_cost = Decimal(costed["execution_cost"])
        arrival_notional = Decimal(fill["arrival_mid"]) * Decimal(str(fill["qty"]))
        fraction = float(arrival_cost / arrival_notional)
        design_rows.append([1.0, math.sqrt(part), part])
        targets.append(fraction / sigma_f)
    distinct_part = len({round(row[2], 8) for row in design_rows})
    base = {
        "schema_version": "sidecar.cost_params.v1",
        "specification": "almgren_linear_v1",
        "does_not_replace_fee_schedule": True,
        "paper_venue_note": (
            "If these fills came from Alpaca paper or from a local simulator, the fit is a "
            "venue-simulation calibration, not a live-market impact estimate."
        ),
    }
    if len(design_rows) < MIN_ROWS or distinct_part < 3:
        document = {
            **base,
            "status": "insufficient_sample",
            "n_fills": len(design_rows),
            "recommended": None,
        }
        return validate(document, SCHEMA)
    width = 3
    xtx = [[0.0] * width for _ in range(width)]
    xty = [0.0] * width
    for row, target in zip(design_rows, targets, strict=True):
        for i in range(width):
            xty[i] += row[i] * target
            for j in range(width):
                xtx[i][j] += row[i] * row[j]
    try:
        epsilon, eta, gamma = _solve(xtx, xty)
    except TCAError:
        document = {
            **base,
            "status": "singular",
            "n_fills": len(design_rows),
            "recommended": None,
        }
        return validate(document, SCHEMA)
    fitted = [epsilon + eta * row[1] + gamma * row[2] for row in design_rows]
    residual = [target - hat for target, hat in zip(targets, fitted, strict=True)]
    mean_y = sum(targets) / len(targets)
    ss_tot = sum((target - mean_y) ** 2 for target in targets)
    ss_res = sum(value * value for value in residual)
    r2 = None if ss_tot <= 0.0 else 1.0 - ss_res / ss_tot
    usable = eta > 0.0 and gamma >= 0.0 and epsilon > 0.0
    document = {
        **base,
        "status": "fit",
        "n_fills": len(design_rows),
        "epsilon": _num(epsilon),
        "temporary_eta": _num(eta),
        "permanent_gamma": _num(gamma),
        "r_squared": None if r2 is None else _num(r2),
        "recommended": (
            None
            if not usable
            else {
                "epsilon": _num(epsilon),
                "temporary_eta": _num(eta),
                "permanent_gamma": _num(gamma),
            }
        ),
    }
    return validate(document, SCHEMA)
