"""WP1a round-trip test for the GroundingCheck agent-output schema.

GroundingCheck is the canonical cross-model grounding verifier output
(docs/prompt-injection-defense.md §7). It is flag-only: it can flag but never
clear a proposal. This test asserts the schema accepts a minimal valid instance
and rejects any unknown field (additionalProperties: false, Invariant 3).
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
        / "GroundingCheck.schema.json"
    ).read_text(encoding="utf-8")
)

VALID = {
    "proposal_id": "prop_01HZY3K8QweRt",
    "grounded": True,
    "injection_suspected": False,
    "unsupported_claims": [],
    "source_refs_checked": ["raw:alpaca:benzinga:abc123"],
    "notes": "Thesis catalyst present in cited packet; no injection markers.",
}


def test_grounding_check_valid():
    jsonschema.validate(VALID, SCHEMA)


def test_grounding_check_rejects_unknown_field():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**VALID, "clear_order": True}, SCHEMA)
