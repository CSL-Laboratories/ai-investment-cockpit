"""WP1a — JSON-Schema round-trip test for InterrogationAnswer (agent output)."""
import json
import pathlib

import jsonschema
import pytest

SCHEMA = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[2]
        / "schemas"
        / "json"
        / "InterrogationAnswer.schema.json"
    ).read_text(encoding="utf-8")
)

VALID = {
    "question": "Why this position size?",
    "answer": "Sizing basis is 1% of buying power; concentration check passed.",
    "pattern": "fresh",
    "session_id": None,
    "risk_decision_ref": "rd_01HZY8Q0",
    "generated_at": "2026-07-06T13:45:00Z",
}


def test_interrogation_answer_valid():
    jsonschema.validate(VALID, SCHEMA)


def test_interrogation_answer_resume_pattern_valid():
    jsonschema.validate(
        {**VALID, "pattern": "resume", "session_id": "sess_abc123"}, SCHEMA
    )


def test_interrogation_answer_rejects_unknown_field():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, "execute_now": True}, SCHEMA)


def test_interrogation_answer_rejects_bad_pattern():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, "pattern": "reset"}, SCHEMA)
