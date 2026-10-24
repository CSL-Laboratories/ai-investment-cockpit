"""Perold implementation shortfall against the decision price and the arrival mid.

A buy that prints above the reference is a positive cost. Fees are added as a positive
cost on both sides. Components are in currency and in basis points of the decision notional.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


class TCAError(ValueError):
    """A fill record cannot be costed."""


def _dec(value, field: str) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise TCAError(f"{field} is not a decimal") from exc
    if not number.is_finite():
        raise TCAError(f"{field} must be finite")
    return number


def _bps(cost: Decimal, notional: Decimal) -> Decimal:
    if notional <= 0:
        raise TCAError("notional must be positive")
    return cost / notional * Decimal(10000)


def implementation_shortfall(fill: dict) -> dict:
    side = str(fill.get("side", "")).strip().lower()
    if side not in {"buy", "sell"}:
        raise TCAError("side must be buy or sell")
    sign = Decimal(1) if side == "buy" else Decimal(-1)
    qty = _dec(fill.get("qty"), "qty")
    decision = _dec(fill.get("decision_price"), "decision_price")
    arrival = _dec(fill.get("arrival_mid"), "arrival_mid")
    price = _dec(fill.get("fill_price"), "fill_price")
    fee = _dec(fill.get("fee", "0"), "fee")
    if qty <= 0 or decision <= 0 or arrival <= 0 or price <= 0:
        raise TCAError("qty and prices must be positive")
    if fee < 0:
        raise TCAError("fee must be non-negative")
    delay = sign * (arrival - decision) * qty
    execution = sign * (price - arrival) * qty
    total = delay + execution + fee
    decision_notional = decision * qty
    return {
        "symbol": str(fill.get("symbol", "")),
        "side": side,
        "qty": format(qty, "f"),
        "delay_cost": format(delay, "f"),
        "execution_cost": format(execution, "f"),
        "fee": format(fee, "f"),
        "implementation_shortfall": format(total, "f"),
        "implementation_shortfall_bps": format(_bps(total, decision_notional), "f"),
        "arrival_shortfall_bps": format(_bps(execution + fee, arrival * qty), "f"),
        "decision_notional": format(decision_notional, "f"),
    }


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _percentile(values: list[Decimal], q: Decimal) -> Decimal:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def summarize_fills(fills: list[dict]) -> dict:
    """Aggregate shortfall and predicted-minus-realized error.

    ``predicted_cost_bps`` is optional. Rows without it are skipped in the error block.
    Arrival shortfall is omitted from the impact discussion when the fill price equals the
    arrival mid: there is no intra-trade dispersion to attribute to impact.
    """
    rows = [implementation_shortfall(fill) for fill in fills]
    if not rows:
        return {
            "schema_version": "sidecar.tca_summary.v1",
            "status": "no_fills",
            "n_fills": 0,
            "venue_label": None,
        }
    shortfalls = [Decimal(row["implementation_shortfall_bps"]) for row in rows]
    labels = {str(fill.get("venue_label", "unspecified")) for fill in fills}
    errors = []
    for fill, row in zip(fills, rows, strict=True):
        if fill.get("predicted_cost_bps") is None:
            continue
        predicted = _dec(fill["predicted_cost_bps"], "predicted_cost_bps")
        errors.append(Decimal(row["implementation_shortfall_bps"]) - predicted)
    dispersed = 0
    for fill in fills:
        if _dec(fill["fill_price"], "fill_price") != _dec(fill["arrival_mid"], "arrival_mid"):
            dispersed += 1
    summary = {
        "schema_version": "sidecar.tca_summary.v1",
        "status": "ok",
        "n_fills": len(rows),
        "venue_label": sorted(labels)[0] if len(labels) == 1 else "mixed",
        "median_implementation_shortfall_bps": format(_median(shortfalls), "f"),
        "mean_implementation_shortfall_bps": format(sum(shortfalls) / len(shortfalls), "f"),
        "p95_abs_implementation_shortfall_bps": format(
            _percentile([abs(value) for value in shortfalls], Decimal("0.95")), "f"
        ),
        "n_with_prediction": len(errors),
        "median_realized_minus_predicted_bps": (
            None if not errors else format(_median(errors), "f")
        ),
        "mean_realized_minus_predicted_bps": (
            None if not errors else format(sum(errors) / len(errors), "f")
        ),
        "fills_with_execution_dispersion": dispersed,
    }
    return summary
