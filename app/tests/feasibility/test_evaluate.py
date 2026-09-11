"""RW-28 — frozen metrics and fail-closed slice A evaluation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from feasibility.evaluate import (
    EVALUATION_PATH,
    METRICS_PATH,
    METRICS_SCHEMA_PATH,
    RW27_FREEZE_SHA256,
    apply_metrics,
    classify,
    freeze_document_sha256,
    load_evaluation,
    load_metrics,
    validate_evaluation,
)
from feasibility.validate import FREEZE_PATH, FROZEN_IDS, FeasibilityError, load_freeze

ROOT = Path(__file__).resolve().parents[3]


def test_checked_in_evaluation_matches_apply() -> None:
    payload = load_evaluation()
    assert payload["claim_id"] == "RW-28"
    assert payload["empirical_pass_claimed"] is False
    assert payload["qualify_count"] == 0
    assert payload["metrics_schema_version"] == "feasibility.metrics.v1"
    assert payload["freeze_sha256"] == RW27_FREEZE_SHA256
    assert tuple(item["candidate_id"] for item in payload["results"]) == FROZEN_IDS
    by_id = {item["candidate_id"]: item for item in payload["results"]}
    assert by_id["cash_merger"]["verdict"] == "watch"
    assert by_id["cash_merger"]["blocking_rule"] == "empirical_evidence_gate_open"
    assert by_id["cef_discount"]["verdict"] == "watch"
    assert by_id["cef_discount"]["blocking_rule"] == "data_contract_unauthorized"
    assert by_id["mega_cap_signal"]["verdict"] == "reject"
    assert by_id["mega_cap_signal"]["blocking_rule"] == "no_expected_return_model"
    assert by_id["awareness_fallback"]["verdict"] == "reject"
    assert by_id["awareness_fallback"]["blocking_rule"] == "no_edge"
    assert by_id["momentum"]["verdict"] == "reject"
    assert by_id["momentum"]["blocking_rule"] == "quant_sim_loop"
    assert by_id["pairs"]["verdict"] == "reject"
    assert by_id["pairs"]["blocking_rule"] == "quant_sim_loop"


def test_freeze_artifact_stays_unverified() -> None:
    freeze = load_freeze()
    assert freeze["metrics_schema_version"] is None
    assert all(item["verdict"] == "unverified" for item in freeze["results"])
    assert freeze_document_sha256() == RW27_FREEZE_SHA256
    assert FREEZE_PATH.read_bytes()
    assert json.loads(FREEZE_PATH.read_text(encoding="utf-8"))["claim_id"] == "RW-27"


def test_no_qualify_in_this_corpus() -> None:
    payload = apply_metrics()
    assert payload["qualify_count"] == 0
    assert not any(item["verdict"] == "qualify" for item in payload["results"])


def test_cash_merger_qualifies_only_when_evidence_gate_closes() -> None:
    freeze = load_freeze()
    metrics = copy.deepcopy(load_metrics())
    cash = next(item for item in metrics["corpus_facts"] if item["candidate_id"] == "cash_merger")
    cash["empirical_evidence_gate"] = "closed"
    payload = apply_metrics(freeze, metrics)
    by_id = {item["candidate_id"]: item for item in payload["results"]}
    assert by_id["cash_merger"]["verdict"] == "qualify"
    assert payload["qualify_count"] == 1
    assert by_id["cef_discount"]["verdict"] == "watch"


def test_cef_stays_watch_if_evidence_gate_closes() -> None:
    freeze = load_freeze()
    metrics = copy.deepcopy(load_metrics())
    cef = next(item for item in metrics["corpus_facts"] if item["candidate_id"] == "cef_discount")
    cef["empirical_evidence_gate"] = "closed"
    payload = apply_metrics(freeze, metrics)
    by_id = {item["candidate_id"]: item for item in payload["results"]}
    assert by_id["cef_discount"]["verdict"] == "watch"
    assert by_id["cef_discount"]["blocking_rule"] == "data_contract_unauthorized"


def test_quant_sim_loop_beats_closed_gate() -> None:
    freeze = load_freeze()
    metrics = copy.deepcopy(load_metrics())
    momentum = next(item for item in metrics["corpus_facts"] if item["candidate_id"] == "momentum")
    momentum["supplies_edge"] = True
    momentum["expected_return_model"] = "present"
    momentum["empirical_evidence_gate"] = "closed"
    payload = apply_metrics(freeze, metrics)
    by_id = {item["candidate_id"]: item for item in payload["results"]}
    assert by_id["momentum"]["verdict"] == "reject"
    assert by_id["momentum"]["blocking_rule"] == "quant_sim_loop"


def test_origin_mismatch_is_fail_closed() -> None:
    freeze = load_freeze()
    metrics = copy.deepcopy(load_metrics())
    cash = next(item for item in metrics["corpus_facts"] if item["candidate_id"] == "cash_merger")
    cash["origin"] = "quant_sim_loop"
    with pytest.raises(FeasibilityError, match="origin disagrees"):
        apply_metrics(freeze, metrics)


def test_tampered_evaluation_rejected() -> None:
    payload = copy.deepcopy(load_evaluation())
    payload["results"][0]["verdict"] = "qualify"
    payload["results"][0]["blocking_rule"] = "none"
    payload["qualify_count"] = 1
    with pytest.raises(FeasibilityError, match="must equal apply_metrics"):
        validate_evaluation(payload)


def test_unverified_evaluation_verdict_rejected_by_schema() -> None:
    payload = copy.deepcopy(apply_metrics())
    payload["results"][0]["verdict"] = "unverified"
    payload["results"][0]["blocking_rule"] = "none"
    with pytest.raises(jsonschema.ValidationError):
        validate_evaluation(payload)


def test_pass_claimed_metrics_rejected() -> None:
    metrics = copy.deepcopy(load_metrics())
    metrics["empirical_pass_claimed"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=metrics,
            schema=json.loads(METRICS_SCHEMA_PATH.read_text(encoding="utf-8")),
        )


def test_invented_numeric_thresholds_rejected() -> None:
    metrics = copy.deepcopy(load_metrics())
    metrics["statistical_numeric_thresholds"] = "sharpe>=1.0"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=metrics,
            schema=json.loads(METRICS_SCHEMA_PATH.read_text(encoding="utf-8")),
        )


def test_metrics_sources_exist() -> None:
    missing: list[str] = []
    for item in load_metrics()["corpus_facts"]:
        target = ROOT / item["source"]
        if not target.is_file() and not target.is_dir():
            missing.append(item["source"])
    assert not missing, missing


def test_criteria_match_freeze_and_product_names() -> None:
    freeze = load_freeze()
    metrics = load_metrics()
    freeze_ids = tuple(item["id"] for item in freeze["comparison_criteria"])
    metric_ids = tuple(item["id"] for item in metrics["criteria"])
    assert freeze_ids == metric_ids
    assert all(item["qualify_requires"] == "present" for item in metrics["criteria"])
    assert metrics["threshold_kind"] == "discrete_protocol"


def test_classify_order() -> None:
    base = {
        "origin": "dec03",
        "supplies_edge": True,
        "expected_return_model": "present",
        "required_data_contract": "none",
        "data_contract_authorized": True,
        "empirical_evidence_gate": "closed",
    }
    assert classify({**base, "origin": "quant_sim_loop"})[2] == "quant_sim_loop"
    assert classify({**base, "supplies_edge": False})[2] == "no_edge"
    assert classify({**base, "expected_return_model": "absent"})[2] == "no_expected_return_model"
    assert classify(
        {
            **base,
            "expected_return_model": "absent",
            "required_data_contract": "daily_nav",
            "data_contract_authorized": False,
        }
    )[2] == "data_contract_unauthorized"
    assert classify({**base, "empirical_evidence_gate": "open"})[2] == "empirical_evidence_gate_open"
    assert classify(base) == ("qualify", "all frozen qualify predicates hold.", "none")


def test_checked_in_files_are_readable() -> None:
    assert METRICS_PATH.is_file()
    assert EVALUATION_PATH.is_file()
    load_metrics()
    load_evaluation()
