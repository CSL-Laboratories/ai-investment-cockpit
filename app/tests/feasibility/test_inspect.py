"""RW-29 — bounded operational-case inspection and missing-evidence ledger."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from feasibility.evaluate import EVALUATION_PATH, load_evaluation
from feasibility.inspect import (
    CASE_IDS,
    CASES_PATH,
    CASES_SCHEMA_PATH,
    DECISION_IDS,
    FAILURE_CLASSES,
    RW27_FREEZE_SHA256,
    RW28_EVALUATION_SHA256,
    load_cases,
    sha256_file,
    validate_cases,
)
from feasibility.validate import FREEZE_PATH, FeasibilityError

ROOT = Path(__file__).resolve().parents[3]


def test_checked_in_cases_load() -> None:
    payload = load_cases()
    assert payload["claim_id"] == "RW-29"
    assert payload["empirical_pass_claimed"] is False
    assert payload["qualify_count"] == 0
    assert payload["freeze_sha256"] == RW27_FREEZE_SHA256
    assert payload["evaluation_sha256"] == RW28_EVALUATION_SHA256
    assert tuple(item["case_id"] for item in payload["cases"]) == CASE_IDS
    assert tuple(item["id"] for item in payload["missing_decisions"]) == DECISION_IDS
    assert {item["failure_class"] for item in payload["cases"]} == set(FAILURE_CLASSES)
    kinds = {item["kind"] for item in payload["cases"]}
    assert {"useful", "empty", "rejected", "unverified"} <= kinds
    assert all(item["authorized_by_this_claim"] is False for item in payload["missing_decisions"])
    assert payload["model_routes"]["status"] == "cited_not_measured"


def test_rw28_evaluation_bytes_unchanged() -> None:
    assert sha256_file(EVALUATION_PATH) == RW28_EVALUATION_SHA256
    assert sha256_file(FREEZE_PATH) == RW27_FREEZE_SHA256
    evaluation = load_evaluation()
    assert evaluation["qualify_count"] == 0
    assert not any(item["verdict"] == "qualify" for item in evaluation["results"])


def test_case_sources_exist() -> None:
    missing: list[str] = []
    for item in load_cases()["cases"]:
        target = ROOT / item["source"]
        if not target.is_file():
            missing.append(item["source"])
    assert not missing, missing
    assert (ROOT / "CLAUDE.md").is_file()
    assert (ROOT / "docs/research/gate-feasibility-and-value-2026-09-06.md").is_file()


def test_watch_candidates_have_named_decisions() -> None:
    payload = load_cases()
    decision_candidates = {
        cid for item in payload["missing_decisions"] for cid in item["candidate_ids"]
    }
    assert "cash_merger" in decision_candidates
    assert "cef_discount" in decision_candidates


def test_tampered_qualify_count_rejected() -> None:
    payload = copy.deepcopy(load_cases())
    payload["qualify_count"] = 1
    with pytest.raises(jsonschema.ValidationError):
        validate_cases(payload)


def test_authorizing_a_decision_rejected() -> None:
    payload = copy.deepcopy(load_cases())
    payload["missing_decisions"][1]["authorized_by_this_claim"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_cases(payload)


def test_raw_packet_ingest_rejected() -> None:
    payload = copy.deepcopy(load_cases())
    payload["cases"][0]["raw_packet_status"] = "in_git"
    with pytest.raises(jsonschema.ValidationError):
        validate_cases(payload)


def test_missing_failure_class_rejected() -> None:
    payload = copy.deepcopy(load_cases())
    payload["cases"][0]["failure_class"] = "eligibility"
    with pytest.raises(FeasibilityError, match="six product failure classes"):
        jsonschema.validate(
            instance=payload,
            schema=json.loads(CASES_SCHEMA_PATH.read_text(encoding="utf-8")),
        )
        validate_cases(payload)


def test_pass_claimed_rejected() -> None:
    payload = copy.deepcopy(load_cases())
    payload["empirical_pass_claimed"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=payload,
            schema=json.loads(CASES_SCHEMA_PATH.read_text(encoding="utf-8")),
        )


def test_unknown_field_rejected() -> None:
    payload = copy.deepcopy(load_cases())
    payload["activate_now"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_cases(payload)


def test_checked_in_file_is_readable() -> None:
    assert CASES_PATH.is_file()
    load_cases()
