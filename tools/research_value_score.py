"""Read-only research arithmetic. No imports from app, broker, credentials or network.

Inputs are separately collected evidence, not authority. Declared provenance is retained,
not independently authenticated by this calculator. No result grants an execution gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from statistics import median

SHA = re.compile(r"[0-9a-f]{64}")


def _number(value, *, nonnegative=False):
    if isinstance(value, bool):
        raise TypeError("boolean is not a measurement")
    result = Decimal(str(value))
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError("measurement must be finite and within its declared range")
    return result


def _ratio(a, b):
    return str(Decimal(a) / Decimal(b)) if b else None


def _provenance(document):
    if not document.get("study_id") or not SHA.fullmatch(str(document.get("protocol_sha256", ""))):
        raise ValueError("study identity and frozen protocol hash are required")
    return {key: document[key] for key in ("study_id", "protocol_sha256")}


def score_extraction(predictions, labels):
    """One id identifies one independently labelled field in the frozen population."""
    if predictions is None or labels is None:
        return {"status": "unmeasured", "reason": "separate predictions and labels required"}
    provenance = _provenance(predictions)
    if _provenance(labels) != provenance:
        raise ValueError("prediction and label populations differ")
    if not labels.get("reviewer") or not labels.get("independence_procedure"):
        raise ValueError("label reviewer and independence procedure must be declared")
    if not predictions.get("parser_revision"):
        raise ValueError("parser revision is required")

    def index(rows):
        result = {}
        for row in rows:
            if not row.get("id") or row["id"] in result:
                raise ValueError("duplicate or missing population identity")
            if not SHA.fullmatch(str(row.get("source_sha256", ""))):
                raise ValueError("every field requires its raw-source hash")
            result[row["id"]] = row
        return result

    predicted, truth = index(predictions["rows"]), index(labels["rows"])
    if predicted.keys() != truth.keys():
        raise ValueError("every population field, including refusals, needs a separate label")
    fields = {}
    for row_id, row in predicted.items():
        label = truth[row_id]
        if (row["source_sha256"] != label["source_sha256"]
                or not row.get("field") or row["field"] != label["field"]):
            raise ValueError("field/source binding mismatch")
        state = label["truth"]
        value, target = row["value"], label["value"]
        if state not in {"present", "absent", "ambiguous"}:
            raise ValueError("unknown truth state")
        if (state == "present") != (target is not None):
            raise ValueError("only present truth may carry an exact value")
        counts = fields.setdefault(row["field"], {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "ambiguous": 0})
        if state == "ambiguous":
            counts["ambiguous"] += 1
        elif state == "absent":
            counts["tn" if value is None else "fp"] += 1
        elif type(value) is type(target) and value == target:
            counts["tp"] += 1
        else:
            counts["fn"] += 1
            if value is not None:
                counts["fp"] += 1
    for counts in fields.values():
        counts["precision"] = _ratio(counts["tp"], counts["tp"] + counts["fp"])
        counts["recall"] = _ratio(counts["tp"], counts["tp"] + counts["fn"])
    return {
        "status": "computed" if predicted else "unmeasured", **provenance,
        "population_fields": len(predicted), "fields": fields,
        "parser_revision": predictions["parser_revision"], "reviewer": labels["reviewer"],
        "independence_procedure": labels["independence_procedure"],
        "independence_verified_by_calculator": False,
    }


def score_time(document):
    if document is None:
        return {"status": "unmeasured", "pairs": 0}
    provenance = _provenance(document)
    saved, costs, seen = [], Decimal(0), set()
    for row in document["rows"]:
        if not row.get("pair_id") or row["pair_id"] in seen:
            raise ValueError("duplicate or missing paired observation")
        seen.add(row["pair_id"])
        saved.append(
            _number(row["manual_minutes"], nonnegative=True)
            - _number(row["assisted_minutes"], nonnegative=True)
            - _number(row["maintenance_minutes"], nonnegative=True)
        )
        costs += _number(row["allocated_cost_usd"], nonnegative=True)
    hours = sum(saved, Decimal(0)) / 60
    rate = document.get("hourly_value_usd")
    net_value = None if rate is None or not saved else hours * _number(rate, nonnegative=True) - costs
    return {
        "status": "computed" if saved else "unmeasured", **provenance,
        "pairs": len(saved), "net_hours_saved": str(hours) if saved else None,
        "median_minutes_saved": str(median(saved)) if saved else None,
        "allocated_cost_usd": str(costs) if saved else None,
        "net_value_usd": str(net_value) if net_value is not None else None,
    }


def score_strategy(document):
    """Aligned daily time-weighted returns, expressed as fractions, after cash-flow adjustment.

    Benchmark inputs must already include their own execution/operating costs. This function
    does not source prices, infer fills, repair missing days or establish statistical significance.
    """
    if document is None:
        return {"status": "unmeasured", "days": 0}
    provenance = _provenance(document)
    curves = {name: Decimal(1) for name in ("strategy", "cash", "broad_equity", "simple_merger")}
    peaks, drawdowns = dict(curves), {name: Decimal(0) for name in curves}
    previous_day = ""
    for row in document["rows"]:
        day = row["date"]
        if date.fromisoformat(day).isoformat() != day or day <= previous_day:
            raise ValueError("days must be canonical, unique and chronological")
        previous_day = day
        returns = {name: _number(row[f"{name}_net_return"]) for name in curves if name != "strategy"}
        returns["strategy"] = (
            _number(row["strategy_gross_return"])
            - _number(row["execution_cost_return"], nonnegative=True)
            - _number(row["operating_cost_return"], nonnegative=True)
        )
        for name, value in returns.items():
            if value < -1:
                raise ValueError("unlevered daily return below total loss is invalid")
            curves[name] *= 1 + value
            peaks[name] = max(peaks[name], curves[name])
            drawdowns[name] = max(drawdowns[name], 1 - curves[name] / peaks[name])
    count = len(document["rows"])
    return {
        "status": "computed" if count else "unmeasured", **provenance, "days": count,
        "net_cumulative_return": {k: str(v - 1) for k, v in curves.items()} if count else None,
        "max_drawdown": {k: str(v) for k, v in drawdowns.items()} if count else None,
        "cost_adjusted_excess_return": {
            k: str(curves["strategy"] - v) for k, v in curves.items() if k != "strategy"
        } if count else None,
        "statistical_significance": "not_established",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("predictions", "labels", "time", "strategy"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()

    data, hashes = {}, {}
    for name, path in vars(args).items():
        if path is not None:
            raw = path.read_bytes()
            data[name] = json.loads(raw)
            hashes[name] = hashlib.sha256(raw).hexdigest()
    result = {
        "authority": "research_only_no_gate_change",
        "calculator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_sha256": hashes,
        "extraction": score_extraction(data.get("predictions"), data.get("labels")),
        "time": score_time(data.get("time")), "strategy": score_strategy(data.get("strategy")),
    }
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
