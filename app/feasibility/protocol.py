"""Freeze the pre-measurement and model-judge protocol. No LLM, no broker.

Product section A requires provenance, population selection, as-of
bounds, evaluation/tuning separation, threshold declaration, ambiguity
treatment, and independent model-judge rules to be frozen before any
measurement starts. This packet records that contract. It does not
collect data, invent numeric thresholds, qualify a judge, authorize a
feed, or mutate freeze/evaluation/cases.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

from feasibility.evaluate import EVALUATION_PATH, load_evaluation
from feasibility.inspect import CASES_PATH, load_cases
from feasibility.validate import FREEZE_PATH, FeasibilityError

PROTOCOL_VERSION = "feasibility.protocol.v1"
PROTOCOL_CLAIM_ID = "RW-30"
RW27_FREEZE_SHA256 = "26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8"
RW28_EVALUATION_SHA256 = "665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31"
RW29_CASES_SHA256 = "1f4f90a8dd03bc0970f8526bc951f425e46d4a0393bb2a6f74ce2f365bf3be41"
JUDGE_FIELDS = (
    "cash_amount",
    "target_security",
    "consideration_mix",
    "lifecycle",
    "expected_date",
    "outside_date",
)
DESIGN_SOURCE = "docs/research/gate-feasibility-and-value-2026-09-06.md"
DESIGN_SOURCE_SHA256 = "8a6484c56ce618282409a51cfc709dc6223724a573b1cc38d21af52f4ad04944"
HELD_OUT_RULE = (
    "This file freezes how a later measurement must be sealed. "
    "It is not a measurement, not held-out evidence, and must not be reused as labels or outcomes."
)

_HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = _HERE / "protocol.v1.json"
PROTOCOL_SCHEMA_PATH = _HERE.parent / "schemas" / "json" / "FeasibilityProtocol.schema.json"
REPO_ROOT = _HERE.parents[1]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_protocol(payload: dict[str, Any]) -> None:
    jsonschema.validate(
        instance=payload,
        schema=json.loads(PROTOCOL_SCHEMA_PATH.read_text(encoding="utf-8")),
    )
    if payload.get("empirical_pass_claimed") is not False:
        raise FeasibilityError("empirical_pass_claimed must be false")
    if payload.get("schema_version") != PROTOCOL_VERSION:
        raise FeasibilityError("schema_version must be feasibility.protocol.v1")
    if payload.get("qualify_count") != 0:
        raise FeasibilityError("this corpus cannot qualify a launch strategy")
    if payload.get("measurement_status") != "not_started":
        raise FeasibilityError("this packet does not start measurement")
    freeze_digest = sha256_file(FREEZE_PATH)
    if freeze_digest != RW27_FREEZE_SHA256 or payload["freeze_sha256"] != RW27_FREEZE_SHA256:
        raise FeasibilityError("protocol must pin the unchanged RW-27 freeze digest")
    evaluation_digest = sha256_file(EVALUATION_PATH)
    if (
        evaluation_digest != RW28_EVALUATION_SHA256
        or payload["evaluation_sha256"] != RW28_EVALUATION_SHA256
    ):
        raise FeasibilityError("protocol must pin the unchanged RW-28 evaluation digest")
    cases_digest = sha256_file(CASES_PATH)
    if cases_digest != RW29_CASES_SHA256 or payload["cases_sha256"] != RW29_CASES_SHA256:
        raise FeasibilityError("protocol must pin the unchanged RW-29 cases digest")
    evaluation = load_evaluation()
    if evaluation["qualify_count"] != 0:
        raise FeasibilityError("pinned evaluation must still have qualify_count 0")
    cases = load_cases()
    if cases["qualify_count"] != 0:
        raise FeasibilityError("pinned cases ledger must still have qualify_count 0")
    if tuple(payload["model_judges"]["fields"]) != JUDGE_FIELDS:
        raise FeasibilityError("model_judges.fields must match the frozen label set")
    if payload["model_judges"]["status"] != "not_started":
        raise FeasibilityError("model judges are not qualified by this packet")
    if payload["thresholds"]["numeric_values"] != "unset":
        raise FeasibilityError("do not invent statistical numeric thresholds")
    allowance = payload["resource_allowance"]
    if any(allowance[key] is not False for key in allowance):
        raise FeasibilityError("RW-30 authorizes no collection, feed, soak, labelling, or activation")
    source = REPO_ROOT / payload["design_source"]
    if not source.is_file():
        raise FeasibilityError("design_source must exist")
    if sha256_file(source) != DESIGN_SOURCE_SHA256 or payload["design_source_sha256"] != DESIGN_SOURCE_SHA256:
        raise FeasibilityError("design_source bytes must match the pinned digest")
    if payload["held_out_rule"] != HELD_OUT_RULE:
        raise FeasibilityError("held_out_rule must match the frozen sentence")
    split = payload["evaluation_tuning_separation"]
    if (
        split["later_held_out_excludes_rw11_regressions"] is not True
        or split["later_held_out_excludes_rw28_evaluation"] is not True
    ):
        raise FeasibilityError("later held-out must exclude RW-11 regressions and RW-28 evaluation")


def load_protocol(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads((path or PROTOCOL_PATH).read_text(encoding="utf-8"))
    validate_protocol(payload)
    return payload
