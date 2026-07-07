"""WP1a — round-trip validation for the TradeProposal agent-output JSON Schema.

Confirms a minimal valid proposal validates and that the schema is closed
(additionalProperties:false) so a hijacked model cannot smuggle an out-of-schema
command field like execute_now / skip_risk / notional_override
(prompt-injection-defense.md §4 M4).
"""
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
        / "TradeProposal.schema.json"
    ).read_text(encoding="utf-8")
)

VALID = {
    "proposal_id": "prop_01HZX3",
    "symbol": "AAPL",
    "side": "buy",
    "asset_type": "equity",
    "order_type": "limit",
    "time_horizon": "short",
    "thesis": "Beat-and-raise into the print; gap-fill setup holding VWAP.",
    "invalidation": "Close below 182.00 invalidates the continuation thesis.",
    "sizing_basis": "fixed-fraction: 1% account risk to the invalidation level",
    "source_refs": ["parquet:2026-07-06/AAPL#42", "parquet:2026-07-06/AAPL#57"],
}


def test_trade_proposal_valid():
    jsonschema.validate(VALID, SCHEMA)


def test_trade_proposal_valid_with_optional_fields():
    instance = {
        **VALID,
        "quantity": 100,
        "notional": None,
        "generated_by": {
            "runtime": "claude",
            "model": "opus",
            "session_id": None,
        },
    }
    jsonschema.validate(instance, SCHEMA)


def test_trade_proposal_rejects_unknown_field():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, "execute_now": True}, SCHEMA)


@pytest.mark.parametrize("field", ["skip_risk", "notional_override", "execute_now"])
def test_trade_proposal_rejects_injection_command_fields(field):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, field: True}, SCHEMA)


@pytest.mark.parametrize(
    "missing",
    [
        "proposal_id",
        "symbol",
        "side",
        "asset_type",
        "order_type",
        "time_horizon",
        "thesis",
        "invalidation",
        "sizing_basis",
        "source_refs",
    ],
)
def test_trade_proposal_requires_field(missing):
    instance = {k: v for k, v in VALID.items() if k != missing}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance, SCHEMA)


def test_trade_proposal_rejects_bad_enum():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, "asset_type": "crypto"}, SCHEMA)
