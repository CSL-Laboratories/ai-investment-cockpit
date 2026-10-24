"""Append-only judgment log and a stable entity mask.

The mask is the Glasserman-Lin style control for a historical prompt: replace tickers before
the text is shown to a model. It is not a secret. Anyone with the ticker list can invert it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from sidecar.chainlog import ChainLog


def anonymize_text(text: str, symbols: list[str]) -> str:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    ordered = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()}, key=len, reverse=True)
    masked = text
    for symbol in ordered:
        token = "ENT_" + hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8]
        masked = re.sub(rf"\b{re.escape(symbol)}\b", token, masked)
    return masked


class JudgmentLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._log = ChainLog(self.path)

    def append(self, judgment: dict, *, recorded_at: datetime | None = None) -> dict:
        required = ("judgment_id", "route", "as_of", "symbol", "horizon", "probability", "model_id")
        missing = [name for name in required if name not in judgment]
        if missing:
            raise ValueError(f"judgment is missing {missing}")
        probability = float(judgment["probability"])
        if not 0.0 <= probability <= 1.0:
            raise ValueError("probability must be in [0, 1]")
        moment = recorded_at or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        existing = {row["judgment_id"] for row in self._log.entries()}
        if judgment["judgment_id"] in existing:
            raise ValueError("judgment_id already logged")
        record = {
            "judgment_id": str(judgment["judgment_id"]),
            "route": str(judgment["route"]),
            "as_of": str(judgment["as_of"]),
            "symbol": str(judgment["symbol"]).upper(),
            "horizon": str(judgment["horizon"]),
            "probability": probability,
            "model_id": str(judgment["model_id"]),
            "confidence": judgment.get("confidence"),
            "thesis_hash": judgment.get("thesis_hash"),
            "entity_anonymized": bool(judgment.get("entity_anonymized", False)),
            "model_training_cutoff": judgment.get("model_training_cutoff"),
            "outcome_up": judgment.get("outcome_up"),
            "outcome_return": judgment.get("outcome_return"),
            "outcome_at": judgment.get("outcome_at"),
            "baseline_probability": judgment.get("baseline_probability"),
            "logged_at": moment.astimezone(timezone.utc).isoformat(),
        }
        return self._log.append(record)

    def entries(self) -> list[dict]:
        return self._log.entries()
