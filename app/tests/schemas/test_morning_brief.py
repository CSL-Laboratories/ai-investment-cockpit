"""WP1a — JSON-Schema round-trip test for the MorningBrief agent output."""
from __future__ import annotations

import json
import pathlib

import jsonschema
import pytest

SCHEMA = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[2]
        / "schemas"
        / "json"
        / "MorningBrief.schema.json"
    ).read_text(encoding="utf-8")
)

VALID = {
    "date": "2026-07-06",
    "market_regime": "risk-on, low volatility",
    "portfolio_changes": [
        {"symbol": "AAPL", "change": "+2.1%", "note": "overnight gap up"}
    ],
    "watch_items": [
        {"symbol": "NVDA", "reason": "earnings in 2 days"}
    ],
    "thesis_updates": [
        {"symbol": "MSFT", "status": "invalidation_watch"}
    ],
    "risk_flags": [
        {"kind": "concentration", "detail": "tech > 40% exposure"}
    ],
    "proposed_actions": [
        {"symbol": "AAPL", "action": "trim", "rationale": "rebalance"}
    ],
    "source_refs": ["captures/news-2026-07-06.parquet#42", "event:abc123"],
}


def test_morning_brief_valid():
    jsonschema.validate(VALID, SCHEMA)


def test_morning_brief_rejects_unknown_field():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, "execute_now": True}, SCHEMA)
