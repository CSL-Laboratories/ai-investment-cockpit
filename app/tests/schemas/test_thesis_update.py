import json
import pathlib

import jsonschema
import pytest

SCHEMA = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[2]
        / "schemas"
        / "json"
        / "ThesisUpdate.schema.json"
    ).read_text(encoding="utf-8")
)

VALID = {
    "position_symbol": "AAPL",
    "thesis": "Services margin expansion drives re-rating over the next 4 quarters.",
    "invalidation": "price < 142.00 or services revenue growth turns negative",
    "status": "invalidation_watch",
    "machine_watchable": True,
    "source_refs": ["proposal:abc123", "event:def456"],
    "updated_at": "2026-07-03T14:30:00Z",
}


def test_thesis_update_valid():
    jsonschema.validate(VALID, SCHEMA)


def test_thesis_update_rejects_unknown_field():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, "execute_now": True}, SCHEMA)
