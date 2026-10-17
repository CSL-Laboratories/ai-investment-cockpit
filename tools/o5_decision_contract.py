"""Provider-free validator for the CSL-217 O5 candidate decision contract.

This module checks checked-in JSON artifacts. It does not import a provider SDK,
open a socket, read credentials, start a subprocess, or submit an order. A passing
run admits the candidate packet at maturity ``researched``. It does not freeze the
contract, qualify an installed route, or report an empirical PASS.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_REL = "docs/replacement/outcomes/O5/fixtures/manifest.json"
SCHEMA_DIR_REL = "docs/replacement/outcomes/O5/schemas"
OUTCOME_PREFIX = "docs/replacement/outcomes/O5/"
MONTH_PROGRAM_REL = "docs/replacement/MONTH-PROGRAM.md"
ACCEPTED_DIGESTS = {
    "docs/replacement/SUCCESSOR-PRODUCT.md": "6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e",
    "docs/replacement/ISSUE-CONTRACT.md": "f1fb50dab4b166ca476698fc3974d6a1c2003b16df1d89ff8930060ad04b5094",
    "docs/replacement/MONTH-PROGRAM.md": "f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001",
    "docs/replacement/SCOPE-AUDIT.md": "d23dcc769e743d199ae4142f0e20ad5feebfe310c71d9f57fdd9d647c230db8c",
}
AUTHORITY_RESEARCH_REL = "docs/replacement/AUTHORITY-COMPLIANCE-RESEARCH.md"
AUTHORITY_RESEARCH_SHA256 = "b023b4d7f12e6b764dd466ac61fb0de61e7ec2962670201ab98bd33fd45008d6"
NOT_CLAIMED = (
    "human_decided",
    "contract_frozen",
    "prototyped",
    "implemented",
    "provider_free_verified",
    "demo_or_paper_qualified",
    "shadow_qualified",
    "limited_live_qualified",
    "release_qualified",
)
AUTHORITY_RANK = {
    "evidence_only": 0,
    "proposal_only": 1,
    "model_assisted": 2,
    "bounded_direct": 3,
    "order_submission": 4,
    "broker_tool": 5,
}
MOCK_TRANSPORTS = frozenset({"mock", "http_mock", "fixture"})
FORBIDDEN_CAPABILITIES = frozenset({"broker_tools", "order_submission", "credential_custody"})
FORBIDDEN_DISCLOSURE = frozenset({"credentials", "order_instructions"})
REFUSAL_BY_ACTION = {
    "submit_order": "order_authority_refused",
    "reveal_credential": "credential_refused",
    "widen_budget": "budget_widening_refused",
}
WIRE_ORDER = (
    "task_suite",
    "capability_manifest",
    "route_manifest",
    "budget_ledger",
    "privacy_disclosure",
    "measure_report",
    "experiment_list",
    "qualification_record",
    "fallback_policy",
    "route_comparison",
    "decision_request",
    "decision_result",
    "decision_refusal",
)


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class Admission:
    admitted: bool
    code: str
    detail: str


@dataclass(frozen=True)
class NegativeResult:
    id: str
    expected_code: str
    code: str
    detail: str
    admitted: bool


@dataclass(frozen=True)
class Report:
    ok: bool
    errors: tuple[str, ...]
    negatives: tuple[NegativeResult, ...]
    maturity: str = "researched"
    qualification_claimed: bool = False
    empirical_pass_claimed: bool = False
    contract_frozen_claimed: bool = False


@dataclass
class Context:
    root: Path
    suite_digest: str
    suite: dict[str, Any]
    cases: dict[str, dict[str, Any]]
    route_ids: set[str]
    negative_ids: set[str]


def _refuse(code: str, detail: str) -> Admission:
    return Admission(False, code, detail)


def _admit() -> Admission:
    return Admission(True, "admitted", "admitted at maturity researched")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ContractError, RecursionError) as exc:
        raise ContractError(f"invalid JSON {path}: {exc}") from exc


def _safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")):
        raise ContractError(f"unsafe path: {relative!r}")
    if "\\" in relative or ".." in Path(relative).parts:
        raise ContractError(f"unsafe path: {relative!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ContractError(f"unsafe path: {relative!r}")
    if not path.is_file():
        raise ContractError(f"missing file: {relative}")
    return path


def _decimal(value: Any, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be a decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ContractError(f"{label} must be a decimal string") from exc
    if not number.is_finite() or number < 0:
        raise ContractError(f"{label} must be a non-negative finite decimal")
    return number


def _unit(value: Any, label: str) -> Decimal:
    number = _decimal(value, label)
    if number > 1:
        raise ContractError(f"{label} must be in [0, 1]")
    return number


def _schema_problems(document: Any, schema: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema)
    problems = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path)):
        location = "/".join(str(part) for part in error.absolute_path) or "(root)"
        problems.append(f"{location}: {error.message}")
    return problems


def _envelope(document: dict[str, Any]) -> Admission | None:
    if document.get("claimed_maturity") != "researched":
        return _refuse("maturity_overclaim", "claimed maturity must stay researched")
    if document.get("not_claimed") != list(NOT_CLAIMED):
        return _refuse("maturity_overclaim", "not_claimed must list every higher maturity and nothing else")
    if document.get("qualification_claimed") is not False:
        return _refuse("maturity_overclaim", "installed-route qualification is not claimed")
    if document.get("empirical_pass_claimed") is not False:
        return _refuse("maturity_overclaim", "empirical PASS is not claimed")
    if document.get("contract_frozen_claimed") is not False:
        return _refuse("maturity_overclaim", "contract_frozen is not claimed")
    if document.get("issue") != "CSL-217":
        return _refuse("schema_invalid", "issue must be CSL-217")
    return None


def _json_equal(left: Any, right: Any) -> bool:
    """Structural JSON equality. Booleans stay distinct from numbers, and empty containers stay present."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return False
        return all(_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(_json_equal(item, other) for item, other in zip(left, right, strict=True))
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if left is None and right is None:
        return True
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return False


def _score_measurement(case: dict[str, Any]) -> tuple[bool, bool]:
    schema_ok = Draft202012Validator(case["output_schema"]).is_valid(case["output"])
    value_ok = schema_ok and _json_equal(case["output"], case["ground_truth"])
    return schema_ok, value_ok


def _score_grounding(case: dict[str, Any]) -> bool:
    output = case["output"]
    refs = output.get("source_refs")
    if not isinstance(refs, list) or not refs:
        return False
    dispatched = set(case["dispatched_refs"])
    if any(not isinstance(ref, str) or ref not in dispatched for ref in refs):
        return False
    cited: dict[str, Any] = {}
    for ref in refs:
        source = case["sources"].get(ref)
        if not isinstance(source, dict):
            return False
        fields = source.get("fields")
        if not isinstance(fields, dict):
            return False
        cited.update(fields)
    claims = output.get("claims")
    if not isinstance(claims, dict) or not claims:
        return False
    return all(key in cited and claims[key] == cited[key] for key in claims)


def _admit_task_suite(document: dict[str, Any], ctx: Context) -> Admission:
    if document.get("provider_results_may_rewrite") is not False:
        return _refuse("provider_result_rewrites_protocol", "provider results cannot rewrite the frozen suite")
    consumption = document["o4_consumption"]
    if consumption.get("fragment") != "docs/replacement/MONTH-PROGRAM.md#o4":
        return _refuse("protocol_pin_mismatch", "O5 must consume the O4 month-program fragment")
    if consumption.get("month_program_sha256") != ACCEPTED_DIGESTS[MONTH_PROGRAM_REL]:
        return _refuse("protocol_pin_mismatch", "O4 fragment digest is not the accepted month program")
    if consumption.get("o4_outcome_artifact") != "pending_outcome_artifact":
        return _refuse("protocol_pin_mismatch", "the O4 outcome artifact is not checked in")
    if consumption.get("o4_claimed_maturity") != "researched":
        return _refuse("maturity_overclaim", "O4 maturity is researched, not a frozen protocol")
    if consumption.get("provider_results_may_rewrite_protocol") is not False:
        return _refuse("provider_result_rewrites_protocol", "provider results cannot rewrite the O4/O5 protocol")
    if consumption.get("o4_population_artifact_frozen") is not False:
        return _refuse("protocol_pin_mismatch", "the O4 population artifact is not frozen")
    required_metrics = {
        "schema_compliance",
        "value_correctness",
        "grounding",
        "calibration_ece",
        "cost_usd",
        "latency_ms_p95",
        "reliability",
        "refusal",
    }
    if not required_metrics <= set(consumption.get("metrics") or []):
        return _refuse("protocol_pin_mismatch", "O5 metrics dropped a required measure")
    if consumption.get("floors") != {
        "correctness": "not_worse_than_baseline",
        "grounding": "not_worse_than_baseline",
        "calibration_ece": "not_worse_than_baseline",
    }:
        return _refuse("protocol_pin_mismatch", "correctness, grounding and calibration are floors")
    non_substitutes = set(consumption.get("non_substitutes") or [])
    required_non_substitutes = {
        "cost_usd",
        "latency_ms_p95",
        "schema_compliance_alone",
        "model_agreement",
        "preference",
    }
    if not required_non_substitutes <= non_substitutes:
        return _refuse("protocol_pin_mismatch", "cost, latency, schema-only, agreement and preference are not correctness")
    seen: set[str] = set()
    for case in document["cases"]:
        case_id = case["id"]
        if case_id in seen:
            return _refuse("schema_invalid", f"duplicate task {case_id}")
        seen.add(case_id)
        polarity = case["polarity"]
        expected = case["expected"]
        if polarity == "measurement":
            schema_ok, value_ok = _score_measurement(case)
            if expected.get("schema_compliance") is not schema_ok or expected.get("value_correctness") is not value_ok:
                return _refuse("suite_oracle_mismatch", f"{case_id} expected scores do not match the deterministic scorer")
            if schema_ok and value_ok is False and expected.get("value_correctness") is not False:
                return _refuse("suite_oracle_mismatch", f"{case_id} treats schema validity as value correctness")
        elif polarity == "grounding":
            grounded = _score_grounding(case)
            if expected.get("grounding_ok") is not grounded:
                return _refuse("suite_oracle_mismatch", f"{case_id} grounding label does not match the deterministic scorer")
        elif polarity == "refusal":
            derived = REFUSAL_BY_ACTION.get(case.get("request_asks"))
            if derived is None:
                return _refuse("schema_invalid", f"{case_id} request_asks has no refusal code")
            if case.get("refusal_code") != derived or expected.get("refusal_code") != derived:
                return _refuse("suite_oracle_mismatch", f"{case_id} refusal code is not derived from the requested action")
            if case.get("order_emitted") is not False or case.get("credential_emitted") is not False:
                return _refuse("order_authority_claimed", f"{case_id} refusal still emits an order or credential")
            if expected.get("order_emitted") is not False or expected.get("credential_emitted") is not False:
                return _refuse("order_authority_claimed", f"{case_id} expected refusal emits an order or credential")
        else:
            return _refuse("schema_invalid", f"unknown polarity {polarity}")
    if set(document["required_negative_ids"]) != ctx.negative_ids:
        return _refuse("negative_set_mismatch", "suite negative ids differ from the fixture manifest")
    return _admit()


def _admit_capability(document: dict[str, Any], _ctx: Context) -> Admission:
    seen: set[str] = set()
    for item in document["capabilities"]:
        capability_id = item["id"]
        if capability_id in seen:
            return _refuse("schema_invalid", f"duplicate capability {capability_id}")
        seen.add(capability_id)
        if item.get("grants_authority") is not False:
            return _refuse("capability_grants_authority", f"{capability_id} grants authority")
        if capability_id in FORBIDDEN_CAPABILITIES and item.get("status") != "forbidden":
            return _refuse("forbidden_capability_enabled", f"{capability_id} is not forbidden")
    if not FORBIDDEN_CAPABILITIES <= seen:
        return _refuse("forbidden_capability_enabled", "forbidden capabilities are missing from the manifest")
    return _admit()


def _admit_routes(document: dict[str, Any], ctx: Context) -> Admission:
    for route in document["routes"]:
        route_id = route["id"]
        if route_id in ctx.route_ids:
            return _refuse("schema_invalid", f"duplicate route {route_id}")
        ctx.route_ids.add(route_id)
        if route.get("installed_route_qualified") is not False:
            if route.get("evidence_class") == "simulation" or route.get("transport") in MOCK_TRANSPORTS:
                return _refuse("mock_is_not_installed_route", f"{route_id} reports a mock as installed-route evidence")
            return _refuse("installed_route_not_qualified", f"{route_id} claims installed-route qualification")
        if route.get("qualification_claim") != "none":
            return _refuse("installed_route_not_qualified", f"{route_id} qualification claim is not none")
        if route.get("order_authority") is not False or route.get("broker_tools") is not False:
            return _refuse("order_authority_claimed", f"{route_id} carries order authority or broker tools")
        if route.get("credentials_required_for_this_packet") is not False:
            return _refuse("credential_claimed", f"{route_id} requires credentials in this packet")
    return _admit()


def _known_route(route_id: str, ctx: Context) -> Admission | None:
    if route_id not in ctx.route_ids:
        return _refuse("unknown_route", f"unknown route {route_id}")
    return None


def _admit_budget(document: dict[str, Any], _ctx: Context) -> Admission:
    if document.get("spend_authorized") is not False or document.get("paid_calls_executed") is not False:
        return _refuse("spend_authorized", "this packet authorizes no spend and executes no paid call")
    for entry in document["entries"]:
        if entry.get("widening_allowed") is not False:
            return _refuse("fallback_widens_budget", f"{entry['id']} allows a budget to widen")
        if entry.get("owner_decision") != "undecided":
            return _refuse("spend_authorized", f"{entry['id']} records a spend decision")
    return _admit()


def _admit_privacy(document: dict[str, Any], _ctx: Context) -> Admission:
    if document.get("status") != "proposal_only" or document.get("decided") is not False:
        return _refuse("privacy_decided_without_receipt", "the disclosure matrix is a proposal, not a decision")
    if document.get("owner") != "Lucas":
        return _refuse("privacy_decided_without_receipt", "disclosure ownership stays with Lucas")
    for item in document["classes"]:
        if item.get("admitted") is not False:
            return _refuse("disclosure_forbidden", f"{item['id']} is admitted without a human receipt")
        if item["id"] in FORBIDDEN_DISCLOSURE and item.get("proposed_default") != "forbidden":
            return _refuse("disclosure_forbidden", f"{item['id']} must stay forbidden")
    return _admit()


def _admit_measures(document: dict[str, Any], _ctx: Context) -> Admission:
    if document.get("measured") is not False:
        return _refuse("measures_not_observed", "no provider measure has been run")
    for item in document["measures"]:
        if item.get("may_offset_correctness") is not False:
            return _refuse("worse_correctness_accepted", f"{item['id']} may offset correctness")
        if item["id"] == "grounding" and item.get("clearing_authority") is not False:
            return _refuse("capability_grants_authority", "grounding cannot clear an order")
        if item.get("status") == "observed":
            return _refuse("measures_not_observed", f"{item['id']} is marked observed")
    return _admit()


def _admit_experiments(document: dict[str, Any], _ctx: Context) -> Admission:
    if document.get("any_executed") is not False:
        return _refuse("experiment_executed", "experiments are listed and were not run")
    if not document["experiments"]:
        return _refuse("schema_invalid", "experiment list is empty")
    for item in document["experiments"]:
        if item.get("status") != "listed_not_run" or item.get("executed") is not False:
            return _refuse("experiment_executed", f"{item['id']} is marked executed")
        if item.get("uses_credentials") is not False or item.get("paid_call") is not False:
            return _refuse("credential_claimed", f"{item['id']} uses credentials or a paid call")
        if item.get("uses_network") is not False:
            return _refuse("experiment_executed", f"{item['id']} uses the network")
    return _admit()


def _admit_qualification(document: dict[str, Any], ctx: Context) -> Admission:
    unknown = _known_route(document["route_id"], ctx)
    if unknown:
        return unknown
    claim = document.get("qualification_claim")
    qualified = document.get("installed_route_qualified")
    mockish = document.get("evidence_class") == "simulation" or document.get("transport") in MOCK_TRANSPORTS
    if claim == "installed_route" or qualified is True:
        if mockish:
            return _refuse(
                "mock_is_not_installed_route",
                "a mock, fixture, or simulation cannot be installed-route evidence",
            )
        return _refuse("installed_route_not_qualified", "installed-route qualification was not run")
    if document.get("experiments_executed") is not False or document.get("retained_outputs") is not False:
        return _refuse("experiment_executed", "qualification outputs were not retained because the experiment was not run")
    if claim != "none" or qualified is not False:
        return _refuse("installed_route_not_qualified", "qualification claim must stay none")
    return _admit()


def _admit_fallback(document: dict[str, Any], ctx: Context) -> Admission:
    primary = document["primary"]
    fallback = document["fallback"]
    for route_id in (primary["route_id"], fallback["route_id"]):
        unknown = _known_route(route_id, ctx)
        if unknown:
            return unknown
    if primary.get("broker_tools") is not False or fallback.get("broker_tools") is not False:
        return _refuse("capability_grants_authority", "fallback broker tools are forbidden")
    if AUTHORITY_RANK[fallback["authority_ceiling"]] > AUTHORITY_RANK[primary["authority_ceiling"]]:
        return _refuse("fallback_widens_authority", "fallback authority ceiling is wider than the primary ceiling")
    extra_disclosure = set(fallback["disclosure_classes"]) - set(primary["disclosure_classes"])
    if extra_disclosure or (FORBIDDEN_DISCLOSURE & set(fallback["disclosure_classes"])):
        return _refuse("fallback_widens_disclosure", "fallback disclosure is wider than the primary disclosure")
    if (
        _decimal(fallback["max_cost_usd"], "fallback.max_cost_usd") > _decimal(primary["max_cost_usd"], "primary.max_cost_usd")
        or int(fallback["max_latency_ms"]) > int(primary["max_latency_ms"])
    ):
        return _refuse("fallback_widens_budget", "fallback cost or latency budget is wider than the primary budget")
    if document.get("effect") != "withhold_and_record":
        return _refuse("order_authority_claimed", "fallback effect must withhold and record")
    if primary["authority_ceiling"] != "evidence_only":
        return _refuse("fallback_widens_authority", "primary authority must stay evidence_only in this packet")
    return _admit()


def _admit_comparison(document: dict[str, Any], ctx: Context) -> Admission:
    for side in ("baseline", "candidate"):
        unknown = _known_route(document[side]["route_id"], ctx)
        if unknown:
            return unknown
    if document.get("measures_are_observed") is not False:
        return _refuse("measures_not_observed", "comparison measures are illustrative, not observed")
    if document.get("qualification_claim") != "none":
        return _refuse("installed_route_not_qualified", "a comparison cannot qualify a route")
    baseline = document["baseline"]
    candidate = document["candidate"]
    if document.get("decision") == "accept":
        if _unit(candidate["correctness"], "candidate.correctness") < _unit(baseline["correctness"], "baseline.correctness"):
            return _refuse(
                "worse_correctness_accepted",
                "lower cost or latency cannot justify worse correctness",
            )
        if _unit(candidate["grounding"], "candidate.grounding") < _unit(baseline["grounding"], "baseline.grounding"):
            return _refuse(
                "worse_grounding_accepted",
                "lower cost or latency cannot justify worse grounding",
            )
        if _unit(candidate["calibration_ece"], "candidate.calibration_ece") > _unit(
            baseline["calibration_ece"], "baseline.calibration_ece"
        ):
            return _refuse(
                "worse_calibration_accepted",
                "lower cost or latency cannot justify worse calibration",
            )
        return _refuse("acceptance_is_not_qualification", "meeting illustrative floors does not qualify a route")
    if document.get("decision") != "withhold":
        return _refuse("schema_invalid", "comparison decision must withhold or be refused")
    return _admit()


def _admit_request(document: dict[str, Any], ctx: Context) -> Admission:
    unknown = _known_route(document["route_id"], ctx)
    if unknown:
        return unknown
    if document.get("suite_digest") != ctx.suite_digest:
        return _refuse("suite_digest_mismatch", "request is not bound to the frozen task suite")
    if document.get("task_id") not in ctx.cases:
        return _refuse("suite_digest_mismatch", "request task is not in the frozen suite")
    prompt_hash = hashlib.sha256(document["prompt_text"].encode("utf-8")).hexdigest()
    if document.get("prompt_sha256") != prompt_hash:
        return _refuse("schema_invalid", "prompt_sha256 does not match prompt_text")
    if document.get("authority_ceiling") != "evidence_only":
        return _refuse("order_authority_claimed", "a decision request cannot raise authority")
    if document.get("order_requested") is not False or document.get("credential_requested") is not False:
        return _refuse("credential_claimed", "a decision request cannot ask for an order or a credential")
    if document.get("budget_authorized") is not False:
        return _refuse("spend_authorized", "a decision request authorizes no spend")
    if set(document.get("disclosure_classes") or []):
        return _refuse("disclosure_forbidden", "no disclosure class is admitted")
    return _admit()


def _admit_result(document: dict[str, Any], ctx: Context) -> Admission:
    unknown = _known_route(document["route_id"], ctx)
    if unknown:
        return unknown
    if document.get("suite_digest") != ctx.suite_digest:
        return _refuse("suite_digest_mismatch", "result is not bound to the frozen task suite")
    case = ctx.cases.get(document.get("task_id"))
    if case is None:
        return _refuse("result_contradicts_suite", "result task is not in the frozen suite")
    if case["polarity"] == "measurement":
        if document.get("schema_compliance") is not case["expected"]["schema_compliance"]:
            return _refuse("result_contradicts_suite", "result schema_compliance contradicts the frozen task")
        if document.get("value_correctness") is not case["expected"]["value_correctness"]:
            return _refuse("result_contradicts_suite", "result value_correctness contradicts the frozen task")
    elif case["polarity"] == "grounding":
        if document.get("grounding_ok") is not case["expected"]["grounding_ok"]:
            return _refuse("result_contradicts_suite", "result grounding_ok contradicts the frozen task")
    else:
        return _refuse("result_contradicts_suite", "a refusal task is answered by a decision_refusal, not a result")
    if document.get("authority_granted") is not False or document.get("order_emitted") is not False:
        return _refuse("order_authority_claimed", "a decision result grants no authority and emits no order")
    if document.get("credentials_present") is not False:
        return _refuse("credential_claimed", "a decision result contains no credential")
    if document.get("disposition") != "withhold":
        return _refuse("acceptance_is_not_qualification", "a result disposition must withhold")
    if document.get("evidence_class") == "independently_verified":
        return _refuse("installed_route_not_qualified", "a result is not independently verified evidence")
    return _admit()


def _admit_refusal(document: dict[str, Any], ctx: Context) -> Admission:
    unknown = _known_route(document["route_id"], ctx)
    if unknown:
        return unknown
    if document.get("suite_digest") != ctx.suite_digest:
        return _refuse("suite_digest_mismatch", "refusal is not bound to the frozen task suite")
    case = ctx.cases.get(document.get("task_id"))
    if case is None or case["polarity"] != "refusal":
        return _refuse("suite_oracle_mismatch", "refusal is not bound to a frozen refusal task")
    derived = REFUSAL_BY_ACTION.get(case.get("request_asks"))
    if derived is None or document.get("code") != derived:
        return _refuse("suite_oracle_mismatch", "refusal code is not derived from the requested action")
    if document.get("order_emitted") is not False or document.get("credential_emitted") is not False:
        return _refuse("order_authority_claimed", "a refusal emits no order and no credential")
    if document.get("authority_granted") is not False:
        return _refuse("order_authority_claimed", "a refusal grants no authority")
    if _decimal(document["budget_delta_usd"], "budget_delta_usd") != 0:
        return _refuse("fallback_widens_budget", "a refusal cannot change the budget")
    if document.get("disclosure_classes"):
        return _refuse("fallback_widens_disclosure", "a refusal cannot add disclosure")
    return _admit()


def _admit_amendment(document: dict[str, Any], ctx: Context) -> Admission:
    if document.get("source") == "provider_result" and document.get("rewrites_task_suite") is True:
        return _refuse(
            "provider_result_rewrites_protocol",
            "a provider result cannot replace or amend the frozen task suite",
        )
    if document.get("rewrites_task_suite") is True:
        return _refuse("provider_result_rewrites_protocol", "the frozen task suite cannot be rewritten by this packet")
    if document.get("replacement_suite_sha256") != ctx.suite_digest:
        return _refuse("suite_digest_mismatch", "amendment digest does not match the frozen suite")
    return _admit()


HANDLERS = {
    "task_suite": _admit_task_suite,
    "capability_manifest": _admit_capability,
    "route_manifest": _admit_routes,
    "budget_ledger": _admit_budget,
    "privacy_disclosure": _admit_privacy,
    "measure_report": _admit_measures,
    "experiment_list": _admit_experiments,
    "qualification_record": _admit_qualification,
    "fallback_policy": _admit_fallback,
    "route_comparison": _admit_comparison,
    "decision_request": _admit_request,
    "decision_result": _admit_result,
    "decision_refusal": _admit_refusal,
    "protocol_amendment": _admit_amendment,
}


def _admit_document(document: Any, schema: dict[str, Any], ctx: Context) -> Admission:
    if not isinstance(document, dict):
        return _refuse("schema_invalid", "document must be an object")
    problems = _schema_problems(document, schema)
    if problems:
        return _refuse("schema_invalid", "; ".join(problems[:4]))
    envelope = _envelope(document)
    if envelope is not None:
        return envelope
    kind = document.get("kind")
    handler = HANDLERS.get(kind)
    if handler is None:
        return _refuse("schema_invalid", f"unknown kind {kind}")
    try:
        return handler(document, ctx)
    except (ContractError, KeyError, TypeError, ValueError) as exc:
        return _refuse("schema_invalid", str(exc))


def _check_pins(root: Path, errors: list[str]) -> None:
    for relative, expected in ACCEPTED_DIGESTS.items():
        path = _safe_path(root, relative)
        if _sha256(path) != expected:
            errors.append(f"accepted contract digest changed: {relative}")
    authority = _safe_path(root, AUTHORITY_RESEARCH_REL)
    if _sha256(authority) != AUTHORITY_RESEARCH_SHA256:
        errors.append("authority research digest changed")


def _load_schemas(root: Path, manifest: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    directory = _safe_path(root, SCHEMA_DIR_REL + "/o5-wire.schema.json").parent
    on_disk = sorted(path.relative_to(root).as_posix() for path in directory.glob("*.schema.json"))
    declared = manifest.get("schemas")
    if not isinstance(declared, list) or not declared:
        errors.append("schema manifest missing")
        return {}
    declared_paths = [item.get("path") for item in declared if isinstance(item, dict)]
    if declared_paths != on_disk:
        errors.append("schema file set differs from the fixture manifest")
        return {}
    schemas: dict[str, dict[str, Any]] = {}
    for item in declared:
        relative = item["path"]
        path = _safe_path(root, relative)
        if _sha256(path) != item.get("sha256"):
            errors.append(f"schema digest mismatch: {relative}")
            continue
        schema = _load_json(path)
        if not isinstance(schema, dict) or not isinstance(schema.get("$defs"), dict):
            errors.append(f"schema root must define $defs: {relative}")
            continue
        Draft202012Validator.check_schema(schema)
        for kind, definition in schema["$defs"].items():
            if kind in schemas:
                errors.append(f"duplicate schema kind {kind}")
            Draft202012Validator.check_schema(definition)
            schemas[kind] = definition
    return schemas


def evaluate(root: Path | None = None) -> Report:
    root = (root or ROOT).resolve()
    errors: list[str] = []
    try:
        _check_pins(root, errors)
        manifest_path = _safe_path(root, MANIFEST_REL)
        manifest = _load_json(manifest_path)
    except ContractError as exc:
        return Report(False, (str(exc),), ())
    if errors:
        return Report(False, tuple(errors), ())
    if not isinstance(manifest, dict):
        return Report(False, ("manifest must be an object",), ())
    try:
        schemas = _load_schemas(root, manifest, errors)
    except (ContractError, jsonschema.SchemaError) as exc:
        return Report(False, (str(exc),), ())
    if errors:
        return Report(False, tuple(errors), ())
    manifest_schema = schemas.get("fixture_manifest")
    if manifest_schema is None:
        return Report(False, ("fixture_manifest schema missing",), ())
    manifest_problems = _schema_problems(manifest, manifest_schema)
    if manifest_problems:
        return Report(False, tuple(f"manifest schema: {item}" for item in manifest_problems[:6]), ())
    envelope = _envelope(manifest)
    if envelope is not None:
        return Report(False, (envelope.detail,), ())

    negatives_index: list[dict[str, Any]] = list(manifest["negatives"])
    negative_ids = {item["id"] for item in negatives_index}
    if len(negative_ids) != len(negatives_index):
        return Report(False, ("duplicate negative id",), ())
    try:
        suite_entry = next(item for item in manifest["wire"] if item["kind"] == "task_suite")
        suite_path = _safe_path(root, suite_entry["path"])
        if _sha256(suite_path) != suite_entry["sha256"]:
            return Report(False, ("task suite digest mismatch",), ())
        suite = _load_json(suite_path)
    except (StopIteration, ContractError) as exc:
        return Report(False, (f"task suite unavailable: {exc}",), ())
    if not isinstance(suite, dict):
        return Report(False, ("task suite must be an object",), ())
    ctx = Context(
        root=root,
        suite_digest=_sha256(suite_path),
        suite=suite,
        cases={},
        route_ids=set(),
        negative_ids=negative_ids,
    )
    wire_kinds = [item["kind"] for item in manifest["wire"]]
    if wire_kinds != list(WIRE_ORDER):
        return Report(False, ("wire manifest order changed",), ())

    for item in manifest["pins"]:
        try:
            path = _safe_path(root, item["path"])
        except ContractError as exc:
            errors.append(str(exc))
            continue
        if _sha256(path) != item["sha256"]:
            errors.append(f"pin digest mismatch: {item['path']}")
        if item["class"] == "prose" and not str(item["path"]).endswith(".md"):
            errors.append(f"prose pin is not markdown: {item['path']}")
        allowed = item["path"] == MONTH_PROGRAM_REL or item["path"] == AUTHORITY_RESEARCH_REL or item["path"].startswith(OUTCOME_PREFIX)
        if not allowed:
            errors.append(f"pin escapes the O5 packet: {item['path']}")

    for item in manifest["wire"]:
        try:
            path = _safe_path(root, item["path"])
        except ContractError as exc:
            errors.append(str(exc))
            continue
        if _sha256(path) != item["sha256"]:
            errors.append(f"wire digest mismatch: {item['path']}")
            continue
        if not item["path"].startswith(OUTCOME_PREFIX):
            errors.append(f"wire path escapes the O5 packet: {item['path']}")
            continue
        document = _load_json(path)
        schema = schemas.get(item["kind"])
        if schema is None:
            errors.append(f"missing schema for {item['kind']}")
            continue
        if isinstance(document, dict) and document.get("kind") != item["kind"]:
            errors.append(f"kind mismatch for {item['path']}")
            continue
        admission = _admit_document(document, schema, ctx)
        if item["kind"] == "task_suite" and isinstance(document, dict):
            ctx.cases = {case["id"]: case for case in document.get("cases", []) if isinstance(case, dict)}
        if not admission.admitted:
            errors.append(f"positive {item['kind']} refused: {admission.code}: {admission.detail}")

    results: list[NegativeResult] = []
    for item in negatives_index:
        try:
            path = _safe_path(root, item["path"])
        except ContractError as exc:
            errors.append(str(exc))
            continue
        if _sha256(path) != item["sha256"]:
            errors.append(f"negative digest mismatch: {item['id']}")
            continue
        document = _load_json(path)
        schema = schemas.get(item["kind"])
        if schema is None:
            errors.append(f"missing negative schema for {item['id']}")
            continue
        admission = _admit_document(document, schema, ctx)
        results.append(
            NegativeResult(
                id=item["id"],
                expected_code=item["expected_code"],
                code=admission.code,
                detail=admission.detail,
                admitted=admission.admitted,
            )
        )
        if admission.admitted or admission.code != item["expected_code"]:
            errors.append(
                f"negative {item['id']} expected {item['expected_code']} but got "
                f"{admission.code} admitted={admission.admitted}"
            )
    return Report(not errors, tuple(errors), tuple(results))


def _line(item: NegativeResult) -> str:
    if item.admitted or item.code != item.expected_code:
        state = "UNEXPECTEDLY admitted"
    else:
        state = "failed as designed"
    return f"O5 negative control {state}: {item.id} — {item.code}: {item.detail}"


def main(argv: list[str] | None = None) -> int:
    del argv
    report = evaluate(ROOT)
    for item in report.negatives:
        print(_line(item))
    if report.ok:
        print("O5 candidate: positives admitted; negatives failed as designed")
        print("maturity_claimed: researched")
        print("qualification_claimed: false")
        print("empirical_pass_claimed: false")
        print("contract_frozen_claimed: false")
        return 0
    for error in report.errors:
        print(f"O5 candidate: FAIL: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
