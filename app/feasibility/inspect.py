"""Inspect a bounded sample of cited operational cases. No LLM, no broker.

RW-28 left qualify_count at 0. Product section A then requires useful
research: inspect actual useful/empty/rejected runs, classify acquisition /
extraction / reasoning / eligibility / delivery / scheduling separately, and
name the evidence or scope decision that would change each result.

This packet reads checked-in evidence notes only. It does not ingest
gitignored raw packets, mutate the freeze or evaluation, authorize a feed,
or close an empirical gate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

from feasibility.evaluate import EVALUATION_PATH, load_evaluation
from feasibility.validate import FROZEN_IDS, FREEZE_PATH, FeasibilityError

CASES_VERSION = "feasibility.cases.v1"
CASES_CLAIM_ID = "RW-29"
RW27_FREEZE_SHA256 = "26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8"
RW28_EVALUATION_SHA256 = "665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31"

FAILURE_CLASSES = (
    "acquisition",
    "extraction",
    "reasoning",
    "eligibility",
    "delivery",
    "scheduling",
)
CASE_IDS = (
    "w24_sep04_identity_unmapped",
    "w24_sep04_selection_empty",
    "w24_sep03_bhf_not_date_ready",
    "w24_intake_cash_parse_refuse",
    "w24_bhf_date_saft_refuse",
    "w22_briefing_edge_unavailable",
    "w22_wake_never_fired",
    "research_value_unmeasured",
    "w23_impl_not_empirical",
)
DECISION_IDS = (
    "cash_merger_prospective_cohort_and_current_target",
    "cef_discount_daily_nav_contract",
    "independent_extraction_labels",
    "authorized_measurement_population",
    "paired_awareness_time_study",
)

_HERE = Path(__file__).resolve().parent
CASES_PATH = _HERE / "cases.v1.json"
CASES_SCHEMA_PATH = _HERE.parent / "schemas" / "json" / "FeasibilityCases.schema.json"
REPO_ROOT = _HERE.parents[1]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_cases(payload: dict[str, Any]) -> None:
    jsonschema.validate(
        instance=payload,
        schema=json.loads(CASES_SCHEMA_PATH.read_text(encoding="utf-8")),
    )
    if payload.get("empirical_pass_claimed") is not False:
        raise FeasibilityError("empirical_pass_claimed must be false")
    if payload.get("schema_version") != CASES_VERSION:
        raise FeasibilityError("schema_version must be feasibility.cases.v1")
    if payload.get("qualify_count") != 0:
        raise FeasibilityError("this corpus cannot qualify a launch strategy")
    freeze_digest = sha256_file(FREEZE_PATH)
    if freeze_digest != RW27_FREEZE_SHA256 or payload["freeze_sha256"] != RW27_FREEZE_SHA256:
        raise FeasibilityError("cases must pin the unchanged RW-27 freeze digest")
    evaluation_digest = sha256_file(EVALUATION_PATH)
    if (
        evaluation_digest != RW28_EVALUATION_SHA256
        or payload["evaluation_sha256"] != RW28_EVALUATION_SHA256
    ):
        raise FeasibilityError("cases must pin the unchanged RW-28 evaluation digest")
    evaluation = load_evaluation()
    if evaluation["qualify_count"] != 0:
        raise FeasibilityError("pinned evaluation must still have qualify_count 0")
    ids = tuple(item["case_id"] for item in payload["cases"])
    if ids != CASE_IDS:
        raise FeasibilityError("case_ids must match the frozen sample order")
    classes = {item["failure_class"] for item in payload["cases"]}
    if classes != set(FAILURE_CLASSES):
        raise FeasibilityError("all six product failure classes must appear")
    kinds = {item["kind"] for item in payload["cases"]}
    if "empty" not in kinds or "rejected" not in kinds:
        raise FeasibilityError("sample must include empty and rejected runs")
    if not any(item["kind"] == "useful" for item in payload["cases"]):
        raise FeasibilityError("sample must include useful research")
    if not any(item["kind"] == "unverified" for item in payload["cases"]):
        raise FeasibilityError("sample must include an unverified remainder")
    for item in payload["cases"]:
        source = REPO_ROOT / item["source"]
        if not source.is_file():
            raise FeasibilityError(f"missing case source {item['source']}")
        extra = set(item["candidate_ids"]) - set(FROZEN_IDS)
        if extra:
            raise FeasibilityError(f"candidate_ids not on the freeze: {sorted(extra)}")
        if item["raw_packet_status"] != "cited_summary_only":
            raise FeasibilityError("this packet inspects cited summaries only")
    decision_ids = tuple(item["id"] for item in payload["missing_decisions"])
    if decision_ids != DECISION_IDS:
        raise FeasibilityError("missing_decisions must match the frozen remainder")
    if any(item["authorized_by_this_claim"] is not False for item in payload["missing_decisions"]):
        raise FeasibilityError("RW-29 authorizes none of the missing decisions")
    route_source = REPO_ROOT / payload["model_routes"]["source"]
    if not route_source.is_file():
        raise FeasibilityError("model_routes source must exist")


def load_cases(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads((path or CASES_PATH).read_text(encoding="utf-8"))
    validate_cases(payload)
    return payload
