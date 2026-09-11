"""Deterministic slice A freeze validation.

No LLM, no broker, no orders. Reads the freeze JSON and the JSON Schema
files only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_VERSION = "feasibility.freeze.v1"
CLAIM_ID = "RW-27"
FROZEN_IDS = (
    "cash_merger",
    "cef_discount",
    "mega_cap_signal",
    "awareness_fallback",
    "momentum",
    "pairs",
)
FORBIDDEN_IDS = frozenset({"market_maker"})  # belt-and-suspenders; schema enum also excludes it
DECISIVE_VERDICTS = frozenset({"qualify", "watch", "reject"})

_HERE = Path(__file__).resolve().parent
FREEZE_PATH = _HERE / "freeze.v1.json"
SCHEMA_PATH = _HERE.parent / "schemas" / "json" / "FeasibilityFreeze.schema.json"


class FeasibilityError(ValueError):
    """Freeze failed closed."""


class DecisiveVerdictBlocked(FeasibilityError):
    """qualify/watch/reject are forbidden until metrics_schema_version is frozen."""


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_freeze(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads((path or FREEZE_PATH).read_text(encoding="utf-8"))
    validate_freeze(payload)
    return payload


def validate_freeze(payload: dict[str, Any]) -> None:
    jsonschema.validate(instance=payload, schema=load_schema())
    if payload.get("empirical_pass_claimed") is not False:
        raise FeasibilityError("empirical_pass_claimed must be false")
    ids = tuple(item["id"] for item in payload["shortlist"])
    if ids != FROZEN_IDS:
        raise FeasibilityError(f"shortlist ids must be {FROZEN_IDS} in order, got {ids}")
    if FORBIDDEN_IDS.intersection(ids):
        raise FeasibilityError("market_maker is not on this freeze")
    if "cash_merger" not in ids:
        raise FeasibilityError("cash_merger must be compared")
    result_ids = tuple(item["candidate_id"] for item in payload["results"])
    if result_ids != ids:
        raise FeasibilityError("results must cover the shortlist in the same order")
    metrics = payload.get("metrics_schema_version")
    if not metrics:
        for item in payload["results"]:
            if item["verdict"] in DECISIVE_VERDICTS:
                raise DecisiveVerdictBlocked(
                    "qualify/watch/reject blocked until metrics_schema_version is frozen"
                )
