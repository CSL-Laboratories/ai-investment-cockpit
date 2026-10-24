"""Read a finished Kronos eval report. This module does not load the model."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sidecar.kronos_signal.cutoff import EVALUATION_START, PRETRAINING_THROUGH
from sidecar.sizer.kelly import suggest_notional

MAX_SIGNAL_AGE_DAYS = 10


class KronosGateError(RuntimeError):
    """The Kronos strategy is not allowed to trade from this report."""


def load_kronos_gate(path: Path) -> dict:
    report_path = Path(path)
    if not report_path.is_file():
        raise KronosGateError(
            "kronos has no offline evaluation report. "
            "Run `python -m sidecar.kronos_signal.evaluate --profile report` "
            f"and pass that file. Looked for {report_path}."
        )
    document = json.loads(report_path.read_text(encoding="utf-8"))
    if document.get("schema_version") != "sidecar.kronos_eval.v1":
        raise KronosGateError("kronos report schema is not sidecar.kronos_eval.v1")
    if document.get("evaluation_start") != EVALUATION_START.isoformat():
        raise KronosGateError("kronos report evaluation_start is not 2024-07-01")
    if document.get("pretraining_through") != PRETRAINING_THROUGH.isoformat():
        raise KronosGateError("kronos report pretraining_through is not 2024-06-30")
    if document.get("live_eligible") is not True:
        raise KronosGateError("kronos report is not marked live_eligible")
    if document.get("orders_submitted") not in (0, None):
        raise KronosGateError("kronos report claims a broker order")
    if not document.get("tradable_signals"):
        raise KronosGateError("kronos report has no tradable signals")
    return document


def targets_from_report(
    document: dict,
    *,
    equity: Decimal,
    symbols: tuple[str, ...],
    session_day: date,
    enforce_age: bool,
) -> dict[str, Decimal]:
    """Size each allowlisted name from the report's last unscored forecast."""
    by_symbol = {}
    for row in document["tradable_signals"]:
        symbol = str(row.get("symbol", "")).upper()
        if symbol in symbols:
            by_symbol[symbol] = row
    missing = [symbol for symbol in symbols if symbol not in by_symbol]
    if missing:
        raise KronosGateError(f"kronos report has no signal for {missing}")
    horizon = None
    targets = {}
    for symbol in symbols:
        row = by_symbol[symbol]
        as_of = date.fromisoformat(str(row["as_of"]))
        if as_of < EVALUATION_START:
            raise KronosGateError("kronos signal as_of is inside the pretraining window")
        age = (session_day - as_of).days
        if age < 0:
            raise KronosGateError("kronos signal as_of is after the session date")
        if enforce_age and age > MAX_SIGNAL_AGE_DAYS:
            raise KronosGateError(
                f"kronos signal as_of {as_of.isoformat()} is more than {MAX_SIGNAL_AGE_DAYS} days before the session"
            )
        edge = Decimal(str(row["edge"]))
        vol = Decimal(str(row["vol"]))
        pred_len = int(row["horizon_bars"])
        if horizon is None:
            horizon = pred_len
        elif horizon != pred_len:
            raise KronosGateError("kronos signals do not share a horizon")
        target_vol = (Decimal("0.01") ** 2 * Decimal(pred_len)).sqrt()
        if edge > 0 and vol > 0:
            targets[symbol] = suggest_notional(edge, vol, target_vol, Decimal("0.25"), equity, equity).suggested
        else:
            targets[symbol] = Decimal(0)
    total = sum(targets.values(), Decimal(0))
    if total > equity and total > 0:
        scale = equity / total
        targets = {symbol: value * scale for symbol, value in targets.items()}
    return targets
