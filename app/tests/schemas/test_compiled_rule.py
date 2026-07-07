"""WP1a — JSON-Schema round-trip test for the CompiledRule agent-output contract."""
import json
import pathlib

import jsonschema
import pytest

SCHEMA = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[2]
        / "schemas"
        / "json"
        / "CompiledRule.schema.json"
    ).read_text(encoding="utf-8")
)

VALID = {
    "rule_id": "rule_01HZ9K",
    "natural_language": "auto-approve rebalancing trades under $200 in existing positions",
    "predicate": {
        "op": "and",
        "clauses": [
            {"field": "side", "op": "in", "value": ["buy", "sell"]},
            {"field": "notional", "op": "lte", "value": 200.00},
        ],
    },
    "worst_case_notional_per_interval": 200.00,
    "symbols_scope": ["AAPL", "MSFT"],
    "existing_positions_only": True,
    "expires_at": "2026-08-01T00:00:00Z",
    "confirmed_hash": "9f2c1a7be04d",
}


def test_compiled_rule_valid():
    jsonschema.validate(VALID, SCHEMA)


def test_compiled_rule_rejects_unknown_field():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, "execute_now": True}, SCHEMA)
