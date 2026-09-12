"""RW-31 — GET-only screen ledger and cohort/label plan."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from feasibility.evaluate import EVALUATION_PATH, load_evaluation
from feasibility.inspect import CASES_PATH, load_cases
from feasibility.protocol import (
    JUDGE_FIELDS,
    PROTOCOL_PATH,
    load_protocol,
)
from feasibility.screens import (
    HELD_OUT_RULE,
    LATEST_SCREEN_ID,
    NEXT_OWNER_ACTION,
    RW27_FREEZE_SHA256,
    RW28_EVALUATION_SHA256,
    RW29_CASES_SHA256,
    RW30_PROTOCOL_SHA256,
    SCREEN_IDS,
    SCREENS_PATH,
    SCREENS_SCHEMA_PATH,
    SEP09_NOTE,
    SEP09_NOTE_SHA256,
    SEP09_SUPPORTED_SYMBOLS,
    SEP12_NOTE,
    SEP12_NOTE_SHA256,
    load_screens,
    sha256_file,
    validate_screens,
)
from feasibility.validate import FREEZE_PATH, FeasibilityError

ROOT = Path(__file__).resolve().parents[3]


def test_checked_in_screens_load() -> None:
    payload = load_screens()
    assert payload["claim_id"] == "RW-31"
    assert payload["empirical_pass_claimed"] is False
    assert payload["qualify_count"] == 0
    assert payload["measurement_status"] == "not_started"
    assert payload["labelling_authorized"] is False
    assert payload["watchlist_seed_authorized"] is False
    assert payload["activation_authorized"] is False
    assert payload["cef_nav_authorized"] is False
    assert payload["meets_cohort_gate"] is False
    assert payload["latest_screen_id"] == LATEST_SCREEN_ID
    assert payload["held_out_rule"] == HELD_OUT_RULE
    assert payload["freeze_sha256"] == RW27_FREEZE_SHA256
    assert payload["evaluation_sha256"] == RW28_EVALUATION_SHA256
    assert payload["cases_sha256"] == RW29_CASES_SHA256
    assert payload["protocol_sha256"] == RW30_PROTOCOL_SHA256
    assert tuple(item["id"] for item in payload["screens"]) == SCREEN_IDS
    latest = payload["screens"][-1]
    assert latest["id"] == "w24_2026_09_12"
    assert latest["provenance"] == "operator_board"
    assert latest["raw_packet_status"] == "cited_summary_only"
    assert latest["identity_list_status"] == "not_in_board_summary"
    assert "supported_symbols" not in latest
    assert latest["counts"] == {
        "pool": 197,
        "supported_heads": 32,
        "cash_parse_hits": 0,
        "selection_ready": 0,
        "date_ready": 0,
        "cohort": 0,
    }
    assert payload["cohort_plan"]["next_owner_action"] == NEXT_OWNER_ACTION
    assert payload["cohort_plan"]["meets_gate"] is False
    assert payload["label_plan"]["labelling_authorized"] is False
    assert payload["label_plan"]["candidate_extraction_refusal_n"] == 32
    assert payload["label_plan"]["candidate_cash_merger_n"] == 0
    assert tuple(payload["label_plan"]["fields"]) == JUDGE_FIELDS
    assert all(value is False for value in payload["resource_allowance"].values())


def test_prior_artifacts_bytes_unchanged() -> None:
    assert sha256_file(FREEZE_PATH) == RW27_FREEZE_SHA256
    assert sha256_file(EVALUATION_PATH) == RW28_EVALUATION_SHA256
    assert sha256_file(CASES_PATH) == RW29_CASES_SHA256
    assert sha256_file(PROTOCOL_PATH) == RW30_PROTOCOL_SHA256
    assert load_evaluation()["qualify_count"] == 0
    assert load_cases()["qualify_count"] == 0
    assert load_protocol()["measurement_status"] == "not_started"
    assert load_protocol()["qualify_count"] == 0


def test_evidence_notes_exist_and_match_pins() -> None:
    sep09 = ROOT / SEP09_NOTE
    sep12 = ROOT / SEP12_NOTE
    assert sep09.is_file()
    assert sep12.is_file()
    assert sha256_file(sep09) == SEP09_NOTE_SHA256
    assert sha256_file(sep12) == SEP12_NOTE_SHA256
    first = load_screens()["screens"][0]
    assert first["evidence_note"] == SEP09_NOTE
    assert first["supported_symbols"] == list(SEP09_SUPPORTED_SYMBOLS)
    assert len(SEP09_SUPPORTED_SYMBOLS) == 32


def test_sep12_must_not_invent_symbol_list() -> None:
    payload = copy.deepcopy(load_screens())
    payload["screens"][1]["supported_symbols"] = list(SEP09_SUPPORTED_SYMBOLS)
    with pytest.raises(FeasibilityError, match="must not copy or invent"):
        validate_screens(payload)


def test_sep12_must_not_invent_soak_sha() -> None:
    payload = copy.deepcopy(load_screens())
    payload["screens"][1]["soak_sha_observed_live"] = "5d50b25194c14beaac7bb979dcd559cc21a2fb95"
    with pytest.raises(FeasibilityError, match="must not invent soak"):
        validate_screens(payload)


def test_held_out_rule_drift_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["held_out_rule"] = "this ledger may be reused as held-out labels"
    with pytest.raises(jsonschema.ValidationError):
        validate_screens(payload)


def test_labelling_authorization_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["labelling_authorized"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_screens(payload)


def test_watchlist_seed_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["watchlist_seed_authorized"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_screens(payload)


def test_cohort_gate_pass_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["meets_cohort_gate"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_screens(payload)


def test_starting_measurement_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["measurement_status"] = "started"
    with pytest.raises(jsonschema.ValidationError):
        validate_screens(payload)


def test_nav_feed_authorization_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["cef_nav_authorized"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_screens(payload)


def test_collection_authorization_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["resource_allowance"]["collection"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_screens(payload)


def test_wrong_judge_field_order_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["label_plan"]["fields"] = list(reversed(JUDGE_FIELDS))
    with pytest.raises(FeasibilityError, match="frozen judge set"):
        jsonschema.validate(
            instance=payload,
            schema=json.loads(SCREENS_SCHEMA_PATH.read_text(encoding="utf-8")),
        )
        validate_screens(payload)


def test_wrong_protocol_pin_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["protocol_sha256"] = "0" * 64
    with pytest.raises(FeasibilityError, match="RW-30 protocol"):
        validate_screens(payload)


def test_sep09_symbol_drift_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    symbols = list(SEP09_SUPPORTED_SYMBOLS)
    symbols[-1] = "AAPL"
    payload["screens"][0]["supported_symbols"] = symbols
    with pytest.raises(FeasibilityError, match="supported_symbols"):
        validate_screens(payload)


def test_pass_claimed_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["empirical_pass_claimed"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=payload,
            schema=json.loads(SCREENS_SCHEMA_PATH.read_text(encoding="utf-8")),
        )


def test_unknown_field_rejected() -> None:
    payload = copy.deepcopy(load_screens())
    payload["activate_now"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_screens(payload)


def test_checked_in_file_is_readable() -> None:
    assert SCREENS_PATH.is_file()
    load_screens()
