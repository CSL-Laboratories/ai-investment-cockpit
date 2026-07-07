"""WP1a — JSON-Schema round-trip test for the TriageResult agent-output contract."""
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
        / "TriageResult.schema.json"
    ).read_text(encoding="utf-8")
)

VALID = {
    "batch_id": "batch_2026-07-06T13:30:00Z_0001",
    "window_start": "2026-07-06T13:00:00Z",
    "window_end": "2026-07-06T13:30:00Z",
    "scored": [
        {
            "article_id": "art_abc123",
            "symbols": ["AAPL", "MSFT"],
            "classification": "material",
            "score": 72,
            "rationale": "Guidance raise corroborated by two cited source items.",
        },
        {
            "article_id": "art_def456",
            "symbols": [],
            "classification": "ignore",
            "score": 3,
            "rationale": "Off-topic press release, no watchlist/holdings entity match.",
        },
    ],
}


def test_triage_result_valid():
    jsonschema.validate(VALID, SCHEMA)


def test_triage_result_rejects_unknown_field():
    # No escalation-bypass field exists; an invented one must fail validation.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, "escalate_now": True}, SCHEMA)


def test_triage_result_rejects_unknown_field_in_scored_item():
    bad = {**VALID, "scored": [{**VALID["scored"][0], "skip_risk": True}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)


def test_triage_result_rejects_bad_classification():
    bad = {**VALID, "scored": [{**VALID["scored"][0], "classification": "buy"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)


def test_triage_result_rejects_score_out_of_range():
    bad = {**VALID, "scored": [{**VALID["scored"][0], "score": 101}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)
