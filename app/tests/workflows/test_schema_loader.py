"""G8 — schema_loader unit tests (Invariant 3, docs/build-scaffold-ultracode.md WP8).

Every decision-stage AgentSpec must carry the loaded jsonschema dict; this enforces the
loader resolves each WP8 output contract from app/schemas/json/ and caches it.
"""
from __future__ import annotations

import pytest

from workflows.schema_loader import load_schema

NAMES = ["TriageResult", "MorningBrief", "TradeProposal", "GroundingCheck",
         "InterrogationAnswer"]


@pytest.mark.parametrize("name", NAMES)
def test_loads_each_wp8_contract(name):
    schema = load_schema(name)
    assert schema["title"] == name
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False


def test_cached_same_object():
    assert load_schema("TriageResult") is load_schema("TriageResult")


def test_unknown_schema_raises():
    with pytest.raises(FileNotFoundError):
        load_schema("NoSuchContract")


def test_required_fields_present():
    tp = load_schema("TradeProposal")
    assert "source_refs" in tp["required"]
    # M4: deliberately NO execution-bypass fields
    for banned in ("execute_now", "skip_risk", "notional_override"):
        assert banned not in tp["properties"]
