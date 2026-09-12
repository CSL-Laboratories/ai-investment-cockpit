"""RW-30 — pre-measurement and model-judge protocol freeze."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from feasibility.evaluate import EVALUATION_PATH, load_evaluation
from feasibility.inspect import CASES_PATH, load_cases
from feasibility.protocol import (
    DESIGN_SOURCE_SHA256,
    HELD_OUT_RULE,
    JUDGE_FIELDS,
    PROTOCOL_PATH,
    PROTOCOL_SCHEMA_PATH,
    RW27_FREEZE_SHA256,
    RW28_EVALUATION_SHA256,
    RW29_CASES_SHA256,
    load_protocol,
    sha256_file,
    validate_protocol,
)
from feasibility.validate import FREEZE_PATH, FeasibilityError

ROOT = Path(__file__).resolve().parents[3]


def test_checked_in_protocol_loads() -> None:
    payload = load_protocol()
    assert payload["claim_id"] == "RW-30"
    assert payload["empirical_pass_claimed"] is False
    assert payload["qualify_count"] == 0
    assert payload["measurement_status"] == "not_started"
    assert payload["freeze_sha256"] == RW27_FREEZE_SHA256
    assert payload["evaluation_sha256"] == RW28_EVALUATION_SHA256
    assert payload["cases_sha256"] == RW29_CASES_SHA256
    assert payload["thresholds"]["numeric_values"] == "unset"
    assert payload["held_out_rule"] == HELD_OUT_RULE
    assert payload["design_source_sha256"] == DESIGN_SOURCE_SHA256
    assert payload["evaluation_tuning_separation"]["later_held_out_excludes_rw11_regressions"] is True
    assert payload["evaluation_tuning_separation"]["later_held_out_excludes_rw28_evaluation"] is True
    assert payload["model_judges"]["status"] == "not_started"
    assert tuple(payload["model_judges"]["fields"]) == JUDGE_FIELDS
    assert all(value is False for value in payload["resource_allowance"].values())


def test_prior_artifacts_bytes_unchanged() -> None:
    assert sha256_file(FREEZE_PATH) == RW27_FREEZE_SHA256
    assert sha256_file(EVALUATION_PATH) == RW28_EVALUATION_SHA256
    assert sha256_file(CASES_PATH) == RW29_CASES_SHA256
    assert load_evaluation()["qualify_count"] == 0
    assert load_cases()["qualify_count"] == 0


def test_design_source_exists() -> None:
    payload = load_protocol()
    source = ROOT / payload["design_source"]
    assert source.is_file()
    assert sha256_file(source) == DESIGN_SOURCE_SHA256


def test_held_out_rule_drift_rejected() -> None:
    payload = copy.deepcopy(load_protocol())
    payload["held_out_rule"] = "this evaluation may be reused as held-out labels"
    with pytest.raises(jsonschema.ValidationError):
        validate_protocol(payload)


def test_starting_measurement_rejected() -> None:
    payload = copy.deepcopy(load_protocol())
    payload["measurement_status"] = "started"
    with pytest.raises(jsonschema.ValidationError):
        validate_protocol(payload)


def test_invented_numeric_thresholds_rejected() -> None:
    payload = copy.deepcopy(load_protocol())
    payload["thresholds"]["numeric_values"] = "sharpe>=1.0"
    with pytest.raises(jsonschema.ValidationError):
        validate_protocol(payload)


def test_implementer_labelling_rejected() -> None:
    payload = copy.deepcopy(load_protocol())
    payload["model_judges"]["implementer_may_label_held_out"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_protocol(payload)


def test_collection_authorization_rejected() -> None:
    payload = copy.deepcopy(load_protocol())
    payload["resource_allowance"]["collection"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_protocol(payload)


def test_nav_feed_authorization_rejected() -> None:
    payload = copy.deepcopy(load_protocol())
    payload["population_selection"]["cef_nav_authorized"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_protocol(payload)


def test_wrong_judge_field_order_rejected() -> None:
    payload = copy.deepcopy(load_protocol())
    payload["model_judges"]["fields"] = list(reversed(JUDGE_FIELDS))
    with pytest.raises(FeasibilityError, match="frozen label set"):
        jsonschema.validate(
            instance=payload,
            schema=json.loads(PROTOCOL_SCHEMA_PATH.read_text(encoding="utf-8")),
        )
        validate_protocol(payload)


def test_pass_claimed_rejected() -> None:
    payload = copy.deepcopy(load_protocol())
    payload["empirical_pass_claimed"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=payload,
            schema=json.loads(PROTOCOL_SCHEMA_PATH.read_text(encoding="utf-8")),
        )


def test_unknown_field_rejected() -> None:
    payload = copy.deepcopy(load_protocol())
    payload["activate_now"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_protocol(payload)


def test_checked_in_file_is_readable() -> None:
    assert PROTOCOL_PATH.is_file()
    load_protocol()
