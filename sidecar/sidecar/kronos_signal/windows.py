"""Walk-forward windows. The context never includes the label bar."""

from __future__ import annotations

from datetime import date, timedelta

from sidecar.kronos_signal.cutoff import EVALUATION_START, PRETRAINING_THROUGH


class LeakageError(ValueError):
    """A window would score a label inside the pretraining span, or show the model the future."""


def decision_indexes(
    bars,
    *,
    pred_len: int,
    stride: int,
    max_decisions: int | None,
    eval_start: date = EVALUATION_START,
) -> list[int]:
    if pred_len < 1 or stride < 1:
        raise ValueError("pred_len and stride must be positive")
    start = next((index for index, bar in enumerate(bars) if bar.day >= eval_start), None)
    if start is None:
        return []
    chosen = []
    for index in range(start, len(bars) - pred_len, stride):
        chosen.append(index)
        if max_decisions is not None and len(chosen) >= max_decisions:
            break
    return chosen


def make_window(bars, index: int, *, lookback: int, pred_len: int) -> dict:
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if index - lookback + 1 < 0:
        raise LeakageError("not enough history before the decision")
    if index + pred_len >= len(bars):
        raise LeakageError("not enough future bars to score the horizon")
    context = list(bars[index - lookback + 1 : index + 1])
    future = list(bars[index + 1 : index + 1 + pred_len])
    decision = context[-1].day
    if decision < EVALUATION_START:
        raise LeakageError("decision is before the July 2024 evaluation start")
    if any(bar.day > decision for bar in context):
        raise LeakageError("context contains a bar after the decision")
    if any(bar.day <= decision for bar in future):
        raise LeakageError("a label bar is not strictly after the decision")
    if future[-1].day <= PRETRAINING_THROUGH:
        raise LeakageError("label ends inside the pretraining window")
    return {"index": index, "decision": decision, "context": context, "future": future}


def placeholder_future(decision: date, pred_len: int) -> list[date]:
    """Weekday dates after ``decision`` for an unscored live context.

    These are time features only. They are not observed sessions and they carry no prices.
    """
    days = []
    cursor = decision
    while len(days) < pred_len:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days.append(cursor)
    return days
