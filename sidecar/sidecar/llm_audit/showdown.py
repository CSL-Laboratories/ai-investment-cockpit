"""Showdown review of a logged decision.

The decision row is written before the outcome row. Scoring reads the two rows and
does not rewrite the thesis. A flat realized return is not a directional hit.

The four cells partition every scored row:

- right for the right reasons: the direction hit, the stated driver happened, and
  the attribution says that driver is why the price moved
- right for the wrong reasons: the direction hit, but the driver did not happen or
  was not why the price moved
- wrong for the right reasons: the driver happened and the direction missed
  (sound process, unlucky)
- wrong for the wrong reasons: the driver did not happen and the direction missed

``happened`` does not look at the realized return.

- ``market``: ``beta * market_return`` is strictly on the called side of zero
- ``catalyst``: the news or sentiment log says the named driver occurred and was
  not already priced
- ``process``: a fact stored on the decision supports the call. The trend stand-in
  stores a nonzero trailing return of the called sign. Figgie stores the cited
  evidence count. The outcome row cannot change this bit.

``was_why`` is the attribution test, and it is required for the first cell only.

- ``market``: the market component is strictly larger in absolute value than the
  idiosyncratic residual ``realized - beta * market``
- ``catalyst``: the idiosyncratic residual is on the called side of zero and at
  least as large in absolute value as the market component
- ``process``: the same bit as ``happened``. The stand-in and the card bench do
  not claim a beta decomposition.

A market or catalyst driver can land in the unlucky cell when the cited piece
happened and a larger residual flipped the total return. ``was_why`` implies the
total return has the called sign, so that stronger claim never sits in the
unlucky cell.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sidecar.chainlog import ChainLog
from sidecar.jsonutil import validate

SCHEMA = Path(__file__).with_name("showdown.schema.json")

CELLS = (
    "right_for_right_reasons",
    "right_for_wrong_reasons",
    "wrong_for_right_reasons",
    "wrong_for_wrong_reasons",
)
DRIVER_CLASSES = ("market", "catalyst", "process")
MARKET_DRIVERS = frozenset({"market", "macro", "beta", "macro_print"})

_DECISION_FIELDS = (
    "judgment_id",
    "route",
    "as_of",
    "horizon_end",
    "symbol",
    "horizon",
    "thesis",
    "driver",
    "driver_class",
    "expected_direction",
    "confidence",
)
_OUTCOME_LEAKS = (
    "outcome_at",
    "realized_return",
    "market_return",
    "catalyst_occurred",
    "catalyst_already_priced",
)


def thesis_hash(thesis: str) -> str:
    return hashlib.sha256(thesis.encode("utf-8")).hexdigest()


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("showdown timestamps must be timezone-aware")
    return parsed


def _finite(value: object, name: str) -> float:
    number = float(value)  # type: ignore[arg-type]
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite")
    return number


def _sign_matches(value: float, direction: str) -> bool:
    if direction == "up":
        return value > 0.0
    if direction == "down":
        return value < 0.0
    raise ValueError("expected_direction must be up or down")


def classify(decision: dict, outcome: dict) -> dict:
    """Partition one pair. This is the only 2x2 used by the card bench."""
    direction = str(decision["expected_direction"])
    if direction not in ("up", "down"):
        raise ValueError("expected_direction must be up or down")
    klass = str(decision["driver_class"])
    if klass not in DRIVER_CLASSES:
        raise ValueError(f"unknown driver_class {klass}")
    realized = _finite(outcome["realized_return"], "realized_return")
    beta = _finite(outcome["beta"], "beta")
    market = _finite(outcome["market_return"], "market_return")
    component = beta * market
    idiosyncratic = realized - component
    direction_right = _sign_matches(realized, direction)
    if klass == "market":
        happened = _sign_matches(component, direction)
        was_why = happened and abs(component) > abs(idiosyncratic)
    elif klass == "catalyst":
        happened = bool(outcome["catalyst_occurred"]) and not bool(outcome.get("catalyst_already_priced", False))
        was_why = happened and _sign_matches(idiosyncratic, direction) and abs(idiosyncratic) >= abs(component)
    else:
        if "process_supported" not in decision:
            raise ValueError("process decisions require process_supported")
        happened = bool(decision["process_supported"])
        was_why = happened
    if direction_right and happened and was_why:
        cell = CELLS[0]
    elif direction_right:
        cell = CELLS[1]
    elif happened:
        cell = CELLS[2]
    else:
        cell = CELLS[3]
    magnitude = decision.get("expected_magnitude")
    magnitude_error = None if magnitude is None else abs(realized - _finite(magnitude, "expected_magnitude"))
    return {
        "cell": cell,
        "direction_right": direction_right,
        "happened": happened,
        "was_why": was_why,
        "realized_return": realized,
        "market_component": component,
        "idiosyncratic": idiosyncratic,
        "magnitude_error": magnitude_error,
    }


def _week(as_of: str) -> str:
    year, week, _day = _dt(as_of).isocalendar()
    return f"{year}-W{week:02d}"


def _brier(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((prob - label) ** 2 for prob, label in pairs) / len(pairs)


def _reliability(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for prob, label in pairs:
        index = min(bins - 1, max(0, int(prob * bins)))
        if prob == 1.0:
            index = bins - 1
        buckets[index].append((prob, label))
    curve = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        curve.append(
            {
                "bin_low": index / bins,
                "bin_high": (index + 1) / bins,
                "n": len(bucket),
                "mean_confidence": sum(prob for prob, _label in bucket) / len(bucket),
                "direction_hit_rate": sum(label for _prob, label in bucket) / len(bucket),
            }
        )
    return curve


def _share(count: int, total: int) -> float:
    if total == 0:
        return 0.0
    return count / total


class ShowdownLog:
    """Append-only decision, then a later outcome. The thesis is not updated."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._log = ChainLog(self.path)

    def append_decision(self, decision: dict, *, recorded_at: datetime) -> dict:
        missing = [name for name in _DECISION_FIELDS if name not in decision]
        if missing:
            raise ValueError(f"decision is missing {missing}")
        leaked = [name for name in _OUTCOME_LEAKS if name in decision]
        if leaked:
            raise ValueError(f"decision must not carry outcome fields {leaked}")
        if recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        direction = str(decision["expected_direction"])
        if direction not in ("up", "down"):
            raise ValueError("expected_direction must be up or down")
        klass = str(decision["driver_class"])
        if klass not in DRIVER_CLASSES:
            raise ValueError(f"unknown driver_class {klass}")
        confidence = _finite(decision["confidence"], "confidence")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        as_of = _dt(decision["as_of"])
        horizon_end = _dt(decision["horizon_end"])
        if horizon_end <= as_of:
            raise ValueError("horizon_end must be strictly after as_of")
        if klass == "process" and "process_supported" not in decision:
            raise ValueError("process decisions require process_supported")
        thesis = str(decision["thesis"])
        if thesis == "":
            raise ValueError("thesis must be non-empty")
        judgment_id = str(decision["judgment_id"])
        if any(row.get("judgment_id") == judgment_id and row.get("kind") == "decision" for row in self._log.entries()):
            raise ValueError("judgment_id already logged")
        magnitude = decision.get("expected_magnitude")
        record = {
            "kind": "decision",
            "judgment_id": judgment_id,
            "route": str(decision["route"]),
            "as_of": as_of.astimezone(timezone.utc).isoformat(),
            "horizon_end": horizon_end.astimezone(timezone.utc).isoformat(),
            "symbol": str(decision["symbol"]).upper(),
            "horizon": str(decision["horizon"]),
            "thesis": thesis,
            "thesis_hash": thesis_hash(thesis),
            "driver": str(decision["driver"]),
            "driver_class": klass,
            "expected_direction": direction,
            "expected_magnitude": None if magnitude is None else _finite(magnitude, "expected_magnitude"),
            "confidence": confidence,
            "process_supported": None if klass != "process" else bool(decision["process_supported"]),
            "model_id": str(decision.get("model_id", "unspecified")),
            "logged_at": recorded_at.astimezone(timezone.utc).isoformat(),
        }
        return self._log.append(record)

    def append_outcome(self, outcome: dict, *, recorded_at: datetime) -> dict:
        if recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        required = ("judgment_id", "outcome_at", "realized_return", "market_return", "beta", "catalyst_occurred")
        missing = [name for name in required if name not in outcome]
        if missing:
            raise ValueError(f"outcome is missing {missing}")
        if "thesis" in outcome or "thesis_hash" in outcome:
            raise ValueError("outcome must not restate the thesis")
        judgment_id = str(outcome["judgment_id"])
        decision = next(
            (row for row in self._log.entries() if row.get("kind") == "decision" and row.get("judgment_id") == judgment_id),
            None,
        )
        if decision is None:
            raise ValueError("outcome has no decision")
        if any(row.get("kind") == "outcome" and row.get("judgment_id") == judgment_id for row in self._log.entries()):
            raise ValueError("outcome already logged")
        moment = recorded_at.astimezone(timezone.utc)
        if moment <= _dt(decision["logged_at"]):
            raise ValueError("outcome must be logged strictly after the decision")
        outcome_at = _dt(outcome["outcome_at"])
        if outcome_at < _dt(decision["horizon_end"]):
            raise ValueError("outcome_at is before the horizon")
        if outcome_at <= _dt(decision["as_of"]):
            raise ValueError("outcome_at must be strictly after as_of")
        record = {
            "kind": "outcome",
            "judgment_id": judgment_id,
            "outcome_at": outcome_at.astimezone(timezone.utc).isoformat(),
            "realized_return": _finite(outcome["realized_return"], "realized_return"),
            "market_return": _finite(outcome["market_return"], "market_return"),
            "beta": _finite(outcome["beta"], "beta"),
            "catalyst_occurred": bool(outcome["catalyst_occurred"]),
            "catalyst_already_priced": bool(outcome.get("catalyst_already_priced", False)),
            "logged_at": moment.isoformat(),
        }
        return self._log.append(record)

    def entries(self) -> list[dict]:
        return self._log.entries()


def score_showdown(rows: list[dict]) -> dict:
    decisions = {}
    outcomes = {}
    excluded: dict[str, int] = defaultdict(int)
    for row in rows:
        kind = row.get("kind")
        judgment_id = row.get("judgment_id")
        if kind == "decision" and judgment_id not in decisions:
            decisions[judgment_id] = row
        elif kind == "outcome":
            if judgment_id in outcomes:
                excluded["duplicate_outcome"] += 1
                continue
            outcomes[judgment_id] = row
        else:
            excluded["unknown_kind"] += 1
    scored_rows = []
    pairs: list[tuple[float, int]] = []
    magnitude_errors = []
    for judgment_id, decision in decisions.items():
        outcome = outcomes.get(judgment_id)
        if outcome is None:
            excluded["unresolved"] += 1
            continue
        try:
            if decision.get("thesis_hash") != thesis_hash(str(decision["thesis"])):
                raise ValueError("thesis hash mismatch")
            if _dt(outcome["logged_at"]) <= _dt(decision["logged_at"]):
                excluded["outcome_not_after_decision_log"] += 1
                continue
            if _dt(outcome["outcome_at"]) < _dt(decision["horizon_end"]):
                excluded["outcome_before_horizon"] += 1
                continue
            if _dt(outcome["outcome_at"]) <= _dt(decision["as_of"]):
                excluded["outcome_not_after_as_of"] += 1
                continue
            judged = classify(decision, outcome)
        except (TypeError, ValueError, KeyError):
            excluded["bad_row"] += 1
            continue
        confidence = float(decision["confidence"])
        label = 1 if judged["direction_right"] else 0
        pairs.append((confidence, label))
        if judged["magnitude_error"] is not None:
            magnitude_errors.append(judged["magnitude_error"])
        scored_rows.append(
            {
                "judgment_id": str(judgment_id),
                "symbol": str(decision["symbol"]),
                "route": str(decision["route"]),
                "driver": str(decision["driver"]),
                "driver_class": str(decision["driver_class"]),
                "cell": judged["cell"],
                "direction_right": judged["direction_right"],
                "happened": judged["happened"],
                "was_why": judged["was_why"],
                "confidence": confidence,
                "realized_return": judged["realized_return"],
                "market_component": judged["market_component"],
                "idiosyncratic": judged["idiosyncratic"],
                "week": _week(decision["as_of"]),
                "thesis_hash": str(decision["thesis_hash"]),
                "magnitude_error": judged["magnitude_error"],
            }
        )
    for judgment_id in outcomes:
        if judgment_id not in decisions:
            excluded["orphan_outcome"] += 1
    cell_counts = {cell: sum(1 for row in scored_rows if row["cell"] == cell) for cell in CELLS}
    n = len(scored_rows)
    by_class = {}
    for klass in DRIVER_CLASSES:
        items = [row for row in scored_rows if row["driver_class"] == klass]
        if not items:
            continue
        by_class[klass] = {
            "n": len(items),
            "cells": {cell: sum(1 for row in items if row["cell"] == cell) for cell in CELLS},
            "direction_hit_rate": _share(sum(1 for row in items if row["direction_right"]), len(items)),
            "happened_rate": _share(sum(1 for row in items if row["happened"]), len(items)),
        }
    weeks = []
    week_ids = sorted({row["week"] for row in scored_rows})
    for week in week_ids:
        items = [row for row in scored_rows if row["week"] == week]
        week_pairs = [(row["confidence"], 1 if row["direction_right"] else 0) for row in items]
        weeks.append(
            {
                "week": week,
                "n": len(items),
                "cells": {cell: sum(1 for row in items if row["cell"] == cell) for cell in CELLS},
                "brier": _brier(week_pairs),
            }
        )
    model_brier = _brier(pairs)
    null_brier = _brier([(0.5, label) for _prob, label in pairs]) if pairs else None
    document = {
        "schema_version": "sidecar.showdown.v1",
        "threshold_status": "not_frozen",
        "yield_claim": False,
        "orders_submitted": 0,
        "n_decisions": len(decisions),
        "n_outcomes": len(outcomes),
        "n_scored": n,
        "excluded": dict(excluded),
        "cells": {cell: {"n": cell_counts[cell], "share": _share(cell_counts[cell], n)} for cell in CELLS},
        "direction_hit_rate": None if n == 0 else _share(sum(1 for row in scored_rows if row["direction_right"]), n),
        "reason_happened_rate": None if n == 0 else _share(sum(1 for row in scored_rows if row["happened"]), n),
        "by_driver_class": by_class,
        "brier": model_brier,
        "null_brier_constant_half": null_brier,
        "brier_minus_null": None if model_brier is None or null_brier is None else model_brier - null_brier,
        "reliability": _reliability(pairs),
        "mean_abs_magnitude_error": None if not magnitude_errors else sum(magnitude_errors) / len(magnitude_errors),
        "n_magnitude": len(magnitude_errors),
        "weeks": weeks,
        "rows": scored_rows,
        "notes": [
            "Decision rows are appended before outcome rows. Scoring does not rewrite the thesis.",
            "Direction is a hit only when the realized simple return is strictly on the called side of zero.",
            "Right reasons require the stated driver to have happened. The first cell also requires the attribution test.",
            "Wrong for the right reasons means the driver happened and the direction missed.",
            "Confidence is the decision-time probability that the direction hits. The null is 0.5.",
            "threshold_status is not_frozen. This score is not a promotion and not an edge claim.",
            "orders_submitted is 0. This module does not place orders.",
        ],
    }
    return validate(document, SCHEMA)


def render_weekly(scored: dict) -> str:
    """Markdown weekly report. Cell counts are descriptive."""
    lines = [
        "# Showdown weekly report",
        "",
        "Each row was logged as a decision before its outcome. The thesis hash is the decision-time text. A later row cannot replace it.",
        "",
        "Direction hits only when the realized simple return is strictly positive for an up call or strictly negative for a down call. A flat return is a miss.",
        "",
        "The stated driver happened when a market call has `beta * market_return` on the called side, when a catalyst call matches a news or sentiment event that was not already priced, or when a process call's decision-time evidence bit is true. The unlucky cell is that bit plus a missed direction. The first cell also requires the attribution test: the market component dominates for a market call, and the idiosyncratic residual dominates and has the called sign for a catalyst. Process calls have no beta test.",
        "",
        f"`threshold_status` is `{scored['threshold_status']}`. `yield_claim` is false. Orders submitted: {scored['orders_submitted']}.",
        "",
        f"Scored {scored['n_scored']} of {scored['n_decisions']} decisions. Excluded: {scored['excluded'] or '{}'}.",
        "",
        "## Cells",
        "",
        "| Cell | n | share |",
        "| --- | --- | --- |",
    ]
    for cell in CELLS:
        item = scored["cells"][cell]
        lines.append(f"| {cell} | {item['n']} | {item['share']:.4f} |")
    lines.extend(
        [
            "",
            f"Direction hit rate: {_fmt(scored['direction_hit_rate'])}.",
            f"Driver-happened rate: {_fmt(scored['reason_happened_rate'])}.",
            f"Brier of confidence against the direction hit: {_fmt(scored['brier'])}.",
            f"Constant-0.5 null Brier: {_fmt(scored['null_brier_constant_half'])}.",
            f"Mean absolute magnitude error ({scored['n_magnitude']} rows): {_fmt(scored['mean_abs_magnitude_error'])}.",
            "",
            "## Reliability by confidence",
            "",
            "| bin | n | mean confidence | direction hit rate |",
            "| --- | --- | --- | --- |",
        ]
    )
    if not scored["reliability"]:
        lines.append("|  | 0 |  |  |")
    for bucket in scored["reliability"]:
        lines.append(
            f"| {bucket['bin_low']:.1f}-{bucket['bin_high']:.1f} | {bucket['n']} | {bucket['mean_confidence']:.4f} | {bucket['direction_hit_rate']:.4f} |"
        )
    lines.extend(["", "## By driver class", ""])
    if not scored["by_driver_class"]:
        lines.append("No scored rows.")
    for klass, item in sorted(scored["by_driver_class"].items()):
        counts = ", ".join(f"{cell}={item['cells'][cell]}" for cell in CELLS)
        lines.append(
            f"- `{klass}` n={item['n']}, direction hit rate {item['direction_hit_rate']:.4f}, happened rate {item['happened_rate']:.4f}. {counts}."
        )
    lines.extend(["", "## Weeks", ""])
    if not scored["weeks"]:
        lines.append("No scored weeks.")
    for week in scored["weeks"]:
        counts = ", ".join(f"{cell}={week['cells'][cell]}" for cell in CELLS)
        lines.append(f"- `{week['week']}` n={week['n']}, Brier {_fmt(week['brier'])}. {counts}.")
    lines.extend(
        [
            "",
            "Synthetic tapes and card games test whether the score can separate a lucky direction from the stated reason. They do not show an edge.",
            "",
        ]
    )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.6f}"
