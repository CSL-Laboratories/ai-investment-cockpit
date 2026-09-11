"""Apply frozen slice A metrics to the RW-27 shortlist. No LLM, no broker.

classify() is an ordered fail-closed protocol, not a statistical scorer.
Order (first match wins):

1. origin == quant_sim_loop -> reject / quant_sim_loop
2. not supplies_edge -> reject / no_edge
3. expected_return_model absent and no required data contract
   -> reject / no_expected_return_model
4. required data contract not authorized -> watch / data_contract_unauthorized
5. empirical_evidence_gate != closed -> watch / empirical_evidence_gate_open
6. else qualify

Statistical numeric thresholds are not invented here. The eight product
criterion names are frozen with qualify_requires=present; scoring them
"present" without an authorized measurement population would be the
invented threshold. This packet classifies on cited discrete corpus facts
only. freeze.v1.json is not mutated.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

from feasibility.validate import FROZEN_IDS, FREEZE_PATH, FeasibilityError, load_freeze

METRICS_VERSION = "feasibility.metrics.v1"
EVALUATION_VERSION = "feasibility.evaluation.v1"
EVALUATION_CLAIM_ID = "RW-28"
THRESHOLD_KIND = "discrete_protocol"
STATISTICAL_NUMERIC_THRESHOLDS = "not_applicable_until_authorized_measurement_population"
AMBIGUITY_TREATMENT = "fail_closed"
RW27_FREEZE_SHA256 = "26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8"

_HERE = Path(__file__).resolve().parent
METRICS_PATH = _HERE / "metrics.v1.json"
EVALUATION_PATH = _HERE / "evaluation.v1.json"
METRICS_SCHEMA_PATH = _HERE.parent / "schemas" / "json" / "FeasibilityMetrics.schema.json"
EVALUATION_SCHEMA_PATH = _HERE.parent / "schemas" / "json" / "FeasibilityEvaluation.schema.json"

CRITERION_IDS = (
    "evidence_attainability",
    "as_of_integrity",
    "data_coverage_cost",
    "execution_and_tax",
    "supported_horizons",
    "testability",
    "incremental_portfolio_exposure",
    "cost_adjusted_evaluation_feasibility",
)

MISSING_EVIDENCE = (
    "cash_merger: W-23 empirical evidence gate is still open (prospective cohort / current eligible target).",
    "cef_discount: daily NAV data contract is not authorized; below-NAV is not expected return.",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_document_sha256() -> str:
    digest = sha256_file(FREEZE_PATH)
    if digest != RW27_FREEZE_SHA256:
        raise FeasibilityError("freeze.v1.json bytes changed; start a new freeze claim")
    return digest


def load_metrics(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads((path or METRICS_PATH).read_text(encoding="utf-8"))
    jsonschema.validate(
        instance=payload,
        schema=json.loads(METRICS_SCHEMA_PATH.read_text(encoding="utf-8")),
    )
    if payload.get("empirical_pass_claimed") is not False:
        raise FeasibilityError("empirical_pass_claimed must be false")
    if payload.get("schema_version") != METRICS_VERSION:
        raise FeasibilityError("schema_version must be feasibility.metrics.v1")
    if payload.get("threshold_kind") != THRESHOLD_KIND:
        raise FeasibilityError("threshold_kind must be discrete_protocol")
    if payload.get("statistical_numeric_thresholds") != STATISTICAL_NUMERIC_THRESHOLDS:
        raise FeasibilityError("do not invent statistical numeric thresholds on this corpus")
    if payload.get("ambiguity_treatment") != AMBIGUITY_TREATMENT:
        raise FeasibilityError("ambiguity_treatment must be fail_closed")
    ids = tuple(item["candidate_id"] for item in payload["corpus_facts"])
    if ids != FROZEN_IDS:
        raise FeasibilityError("corpus_facts must match the frozen shortlist order")
    crit = tuple(item["id"] for item in payload["criteria"])
    if crit != CRITERION_IDS:
        raise FeasibilityError("criteria ids must match product-delivery section A")
    return payload


def classify(fact: dict[str, Any]) -> tuple[str, str, str]:
    if fact["origin"] == "quant_sim_loop":
        return (
            "reject",
            "quant_sim_loop is not a product launch strategy.",
            "quant_sim_loop",
        )
    if not fact["supplies_edge"]:
        return (
            "reject",
            "candidate supplies no edge and cannot be a launch strategy.",
            "no_edge",
        )
    needs_contract = fact["required_data_contract"] != "none"
    if fact["expected_return_model"] == "absent" and not needs_contract:
        return (
            "reject",
            "no point-in-time expected-return model.",
            "no_expected_return_model",
        )
    if needs_contract and not fact["data_contract_authorized"]:
        return (
            "watch",
            "required data contract is not authorized.",
            "data_contract_unauthorized",
        )
    if fact["empirical_evidence_gate"] != "closed":
        return (
            "watch",
            "empirical evidence gate is open.",
            "empirical_evidence_gate_open",
        )
    return ("qualify", "all frozen qualify predicates hold.", "none")


def apply_metrics(
    freeze: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    freeze = freeze if freeze is not None else load_freeze()
    metrics = metrics if metrics is not None else load_metrics()
    freeze_ids = tuple(item["id"] for item in freeze["shortlist"])
    if freeze_ids != FROZEN_IDS:
        raise FeasibilityError("freeze shortlist must match the frozen shortlist order")
    freeze_crit = tuple(item["id"] for item in freeze["comparison_criteria"])
    if freeze_crit != CRITERION_IDS:
        raise FeasibilityError("freeze comparison_criteria must match product-delivery section A")
    facts = {item["candidate_id"]: item for item in metrics["corpus_facts"]}
    results: list[dict[str, str]] = []
    for contract in freeze["shortlist"]:
        cid = str(contract["id"])
        fact = facts.get(cid)
        if fact is None:
            raise FeasibilityError(f"metrics missing corpus fact for {cid}")
        if fact["origin"] != contract["origin"]:
            raise FeasibilityError(f"{cid} origin disagrees between freeze and metrics")
        verdict, reason, rule = classify(fact)
        results.append(
            {
                "candidate_id": cid,
                "verdict": verdict,
                "reason": reason,
                "blocking_rule": rule,
            }
        )
    qualify_count = sum(1 for item in results if item["verdict"] == "qualify")
    return {
        "schema_version": EVALUATION_VERSION,
        "claim_id": EVALUATION_CLAIM_ID,
        "empirical_pass_claimed": False,
        "metrics_schema_version": METRICS_VERSION,
        "freeze_schema_version": freeze["schema_version"],
        "freeze_sha256": freeze_document_sha256(),
        "threshold_kind": str(metrics["threshold_kind"]),
        "ambiguity_treatment": str(metrics["ambiguity_treatment"]),
        "statistical_numeric_thresholds": str(metrics["statistical_numeric_thresholds"]),
        "held_out_rule": str(metrics["held_out_rule"]),
        "qualify_count": qualify_count,
        "results": results,
        "missing_evidence": list(MISSING_EVIDENCE),
    }


def validate_evaluation(payload: dict[str, Any]) -> None:
    jsonschema.validate(
        instance=payload,
        schema=json.loads(EVALUATION_SCHEMA_PATH.read_text(encoding="utf-8")),
    )
    expected = apply_metrics()
    if payload != expected:
        raise FeasibilityError("evaluation must equal apply_metrics() on the frozen files")
    if payload["qualify_count"] != 0:
        raise FeasibilityError("this corpus cannot qualify a launch strategy")
    if payload["empirical_pass_claimed"] is not False:
        raise FeasibilityError("empirical_pass_claimed must be false")
    if payload["freeze_sha256"] != RW27_FREEZE_SHA256:
        raise FeasibilityError("evaluation must pin the RW-27 freeze digest")
    if payload["threshold_kind"] != THRESHOLD_KIND:
        raise FeasibilityError("evaluation must carry discrete_protocol")
    watch_ids = [item["candidate_id"] for item in payload["results"] if item["verdict"] == "watch"]
    missing_blob = " ".join(payload["missing_evidence"])
    drifted = [cid for cid in watch_ids if cid not in missing_blob]
    if drifted:
        raise FeasibilityError(f"missing_evidence must cite watch candidates {drifted}")


def load_evaluation(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads((path or EVALUATION_PATH).read_text(encoding="utf-8"))
    validate_evaluation(payload)
    return payload
