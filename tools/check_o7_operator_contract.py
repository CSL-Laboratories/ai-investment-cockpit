"""Provider-free checker for the CSL-219 O7 operator candidate.

Exact command:

    python -X utf8 -B tools/check_o7_operator_contract.py

Exit 0 means the checked-in candidate is internally consistent and every
negative control fails as designed. It does not claim outcome qualification,
and it does not open a network connection.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / "docs" / "replacement" / "outcomes" / "O7"
PROGRAM_CHECKER = REPO / "tools" / "check_replacement_program.py"
API_VERSION = "o7.operator.v0"
MATURITY = "researched"
SOLE_ROUTE = "risk_pass_mode_approval_order_gateway"
MARKER = (
    "<!-- o7-candidate: maturity=researched qualification_claimed=false "
    "contract_frozen_claimed=false framework_selected=false -->"
)
DECISION_HEADING = "## Unresolved decisions (owner: Lucas)"
EXIT_CODES = {
    "ok": 0,
    "usage": 1,
    "refused": 2,
    "stale_or_unreconciled": 3,
    "unavailable": 4,
}
REQUIRED_CONTROLS = (
    "gui_cli_semantic_divergence",
    "hidden_refusal",
    "hidden_uncertainty",
    "hidden_stale_data",
    "hidden_unreconciled",
    "privileged_order_bypass",
    "color_only_signal",
    "success_exit_on_refusal",
    "contradictory_status_text",
    "unconfirmed_intervention",
    "unscoped_intervention",
    "stale_not_watch_only",
)
COMMAND_SCHEMA_CONTROLS = frozenset({"unconfirmed_intervention", "unscoped_intervention"})
REQUIRED_PRIVILEGED = {
    "order_path": ("privileged_order_bypass", "order_path", "surface_private", "none"),
    "enforcement_route": (
        "privileged_order_bypass",
        "enforcement_route",
        "broker_direct",
        SOLE_ROUTE,
    ),
    "command": (
        "privileged_order_bypass",
        "command",
        "operator.orders.submit",
        "operator.positions.inspect",
    ),
    "authority_effect": ("authority_effect_mismatch", "authority_effect", "direct_order", "none"),
    "order_submitted": ("privileged_order_bypass", "result.order_submitted", True, None),
    "forbidden_key": ("privileged_order_bypass", "bypass_gateway", True, None),
}
PRIVILEGED_BASELINE_ID = "req_positions"
SCHEMA_VALID_CONTROLS = frozenset(REQUIRED_CONTROLS) - {"privileged_order_bypass", "stale_not_watch_only", "contradictory_status_text"} - COMMAND_SCHEMA_CONTROLS
CONTROL_REASONS = {
    "gui_cli_semantic_divergence": "semantic_projection_mismatch",
    "hidden_refusal": "refusal_not_visible",
    "hidden_uncertainty": "uncertainty_not_visible",
    "hidden_stale_data": "stale_not_visible",
    "hidden_unreconciled": "unreconciled_not_visible",
    "privileged_order_bypass": "surface_order_path",
    "color_only_signal": "color_only_signal",
    "success_exit_on_refusal": "exit_code_hides_refusal",
    "contradictory_status_text": "status_text_mismatch",
    "unconfirmed_intervention": "intervention_not_explicit",
    "unscoped_intervention": "intervention_not_scoped",
    "stale_not_watch_only": "stale_not_watch_only",
}
ORACLE_KINDS = ("responses", "pairs", "commands", "events", "errors")
REQUIRED_ORACLE_PAIRS = ("positions.inspect.parity", "decisions.inspect.parity")
REQUIRED_ORACLE_PAIR_COMMANDS = {
    "positions.inspect.parity": "operator.positions.inspect",
    "decisions.inspect.parity": "operator.decisions.inspect",
}
REQUIRED_JOURNEYS = (
    "evidence_provenance",
    "strategies",
    "positions_collateral",
    "decisions_refusals",
    "uncertainty",
    "health",
    "budgets",
    "compliance_profiles",
    "interventions",
    "audit",
    "recovery",
    "export",
)
REQUIRED_COMMANDS = {
    "operator.audit.export": {
        "journey": "audit",
        "effect": "read",
        "fields": ("export_id", "chain_head_seq", "includes_refusals", "secrets_included"),
        "must_show": ("includes_refusals", "secrets_included"),
    },
    "operator.budgets.inspect": {
        "journey": "budgets",
        "effect": "read",
        "fields": ("budgets",),
        "must_show": ("budgets",),
    },
    "operator.decisions.inspect": {
        "journey": "decisions_refusals",
        "effect": "read",
        "fields": ("decisions", "refusals"),
        "must_show": ("refusals",),
    },
    "operator.evidence.inspect": {
        "journey": "evidence_provenance",
        "effect": "read",
        "fields": (
            "evidence_id",
            "source_id",
            "retrieved_at",
            "raw_ref",
            "summary_ref",
            "raw_separated_from_summary",
            "provenance",
        ),
        "must_show": ("provenance", "raw_separated_from_summary"),
    },
    "operator.export.bundle": {
        "journey": "export",
        "effect": "read",
        "fields": ("bundle_id", "contents", "secrets_included"),
        "must_show": ("contents", "secrets_included"),
    },
    "operator.health.inspect": {
        "journey": "health",
        "effect": "read",
        "fields": ("watch_only", "stale", "dependencies"),
        "must_show": ("stale", "watch_only"),
    },
    "operator.interventions.request_reconciliation": {
        "journey": "interventions",
        "effect": "intervention",
        "fields": (
            "intervention",
            "recorded",
            "survives_restart",
            "restart_requires_reconciliation",
            "order_submitted",
        ),
        "must_show": ("intervention", "order_submitted"),
    },
    "operator.interventions.request_revocation": {
        "journey": "interventions",
        "effect": "intervention",
        "fields": (
            "intervention",
            "recorded",
            "survives_restart",
            "restart_requires_reconciliation",
            "order_submitted",
        ),
        "must_show": ("intervention", "order_submitted"),
    },
    "operator.interventions.request_stop": {
        "journey": "interventions",
        "effect": "intervention",
        "fields": (
            "intervention",
            "recorded",
            "survives_restart",
            "restart_requires_reconciliation",
            "order_submitted",
        ),
        "must_show": ("intervention", "order_submitted"),
    },
    "operator.positions.inspect": {
        "journey": "positions_collateral",
        "effect": "read",
        "fields": ("positions", "collateral"),
        "must_show": ("positions", "collateral"),
    },
    "operator.profiles.inspect": {
        "journey": "compliance_profiles",
        "effect": "read",
        "fields": (
            "active_profile_class",
            "local_control_is_account_wide",
            "surfaces_share_one_profile",
            "qualification_reference",
            "bounded_direct_authorized",
        ),
        "must_show": ("active_profile_class", "local_control_is_account_wide"),
    },
    "operator.recovery.inspect": {
        "journey": "recovery",
        "effect": "read",
        "fields": (
            "reconciliation",
            "unknown_orders",
            "restart_allowed",
            "restart_requires_reconciliation",
        ),
        "must_show": ("reconciliation", "unknown_orders"),
    },
    "operator.strategies.inspect": {
        "journey": "strategies",
        "effect": "read",
        "fields": ("strategy_id", "qualification", "baseline_id"),
        "must_show": ("qualification",),
    },
    "operator.uncertainty.inspect": {
        "journey": "uncertainty",
        "effect": "read",
        "fields": ("items",),
        "must_show": ("items",),
    },
}
REQUIRED_MUST_SHOW = {name: spec["must_show"] for name, spec in REQUIRED_COMMANDS.items()}
REQUIRED_DECISION_CLASSES = (
    "architecture",
    "release",
    "spend",
    "credentials",
    "tax_legal",
    "live",
    "authority",
)
REQUIRED_DECISIONS = {
    "gui-framework": "architecture",
    "gui-distribution-and-os": "architecture",
    "cli-implementation-stack": "architecture",
    "tui-authorization": "architecture",
    "legacy-surface-migration": "architecture",
    "month-slice-surfaces": "release",
    "operator-surface-spend": "spend",
    "operator-authentication": "credentials",
    "surface-eligibility-language": "tax_legal",
    "live-surface-effects": "live",
    "intervention-scope": "authority",
}
REQUIRED_CITATIONS = (
    "search_08b86fcb75c9012e38baca48941cbf74",
    "search_d8e17f0eba7853c179f525c121ae9b51",
    "https://www.w3.org/TR/WCAG22/",
    "https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22",
    "https://clig.dev/",
    "https://primer.style/accessibility/foundations/cli-accessibility/",
    "https://dl.acm.org/doi/fullHtml/10.1145/3411764.3445544",
    "https://github.blog/engineering/user-experience/building-a-more-accessible-github-cli/",
    "full HTML not retrieved",
)
FORBIDDEN_KEYS = frozenset(
    {
        "bypass_gateway",
        "submit_order",
        "execute_now",
        "broker_order",
        "api_key",
        "password",
        "credential",
        "live_order",
    }
)
EVENT_COMMANDS = {
    "evidence_viewed": frozenset({"operator.evidence.inspect"}),
    "refusal_visible": frozenset(REQUIRED_MUST_SHOW),
    "uncertainty_visible": frozenset(REQUIRED_MUST_SHOW),
    "stale_visible": frozenset(REQUIRED_MUST_SHOW),
    "unreconciled_visible": frozenset(REQUIRED_MUST_SHOW),
    "intervention_recorded": frozenset(
        {
            "operator.interventions.request_stop",
            "operator.interventions.request_reconciliation",
            "operator.interventions.request_revocation",
        }
    ),
    "export_recorded": frozenset({"operator.audit.export", "operator.export.bundle"}),
}
EVENT_VISIBILITY = {
    "evidence_viewed": "visible",
    "refusal_visible": "visible",
    "uncertainty_visible": "visible",
    "stale_visible": "visible",
    "unreconciled_visible": "unreconciled_visible",
    "intervention_recorded": "visible",
    "export_recorded": "visible",
}
EVENT_STATUSES = {
    "evidence_viewed": frozenset({"ok", "stale_or_unreconciled", "unavailable"}),
    "refusal_visible": frozenset({"refused", "stale_or_unreconciled", "unavailable"}),
    "uncertainty_visible": frozenset({"ok", "refused", "stale_or_unreconciled", "unavailable"}),
    "stale_visible": frozenset({"stale_or_unreconciled", "unavailable"}),
    "unreconciled_visible": frozenset({"stale_or_unreconciled", "unavailable"}),
    "intervention_recorded": frozenset({"ok", "stale_or_unreconciled", "unavailable"}),
    "export_recorded": frozenset({"ok", "stale_or_unreconciled", "unavailable"}),
}
INTERVENTION_NAMES = {
    "operator.interventions.request_stop": "stop",
    "operator.interventions.request_reconciliation": "reconciliation",
    "operator.interventions.request_revocation": "revocation",
}
SUMMARY = (
    "o7-operator-contract: candidate_consistent maturity=researched "
    "qualification_claimed=false contract_frozen_claimed=false framework_selected=false"
)


class ContractError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key!r}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_unique_object)
    except (OSError, ValueError, RecursionError) as exc:
        raise ContractError(f"invalid JSON {path.name}: {exc}") from exc


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


RESULT_STATUSES = ("ok", "refused", "stale_or_unreconciled", "unavailable")


def _schema_command_result_fields(schema: dict[str, Any]) -> dict[str, dict[str, list[Any]]]:
    """Map each command to the result fields required for every non-usage status."""
    found: dict[str, dict[str, list[Any]]] = {}
    for clause in schema.get("allOf", []):
        if not isinstance(clause, dict):
            continue
        properties = clause.get("if", {}).get("properties", {})
        command = properties.get("command", {}).get("const") if isinstance(properties, dict) else None
        then = clause.get("then")
        required = then.get("properties", {}).get("result", {}).get("required") if isinstance(then, dict) else None
        if not isinstance(command, str) or not isinstance(required, list):
            continue
        if set(properties) != {"command", "status_code"}:
            continue
        status = properties.get("status_code")
        status_code = status.get("const") if isinstance(status, dict) else None
        if status_code not in RESULT_STATUSES:
            raise ContractError(f"schema result fields are not per status for {command}")
        by_status = found.setdefault(command, {})
        if status_code in by_status:
            raise ContractError(f"schema result fields repeated for {command}")
        by_status[status_code] = required
    return found


def _unknown_order_count(result: dict[str, Any]) -> int | None:
    value = result.get("unknown_orders")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _reconciled_with_unknown_orders(result: dict[str, Any]) -> bool:
    count = _unknown_order_count(result)
    return result.get("reconciliation") == "reconciled" and count is not None and count > 0


def _is_unreconciled(result: dict[str, Any]) -> bool:
    if _reconciled_with_unknown_orders(result):
        return False
    count = _unknown_order_count(result)
    return result.get("reconciliation") == "unreconciled" or (count is not None and count > 0)


def _intervention_probe(name: str, *, confirmation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "candidate_maturity": MATURITY,
        "surface": "cli",
        "command": name,
        "request_id": "req_cmd_probe",
        "arguments": arguments,
        "confirmation": confirmation,
    }


def _require_intervention_safeguards(validator: Draft202012Validator) -> None:
    """Each intervention command must reject a missing confirmation and a missing scope."""
    for name in INTERVENTION_NAMES:
        explicit = _intervention_probe(
            name, confirmation="explicit", arguments={"scope": "operator_session"}
        )
        if _schema_error(validator, explicit) is not None:
            raise ContractError(f"intervention command schema rejects {name}")
        unconfirmed = _intervention_probe(
            name, confirmation="not_required", arguments={"scope": "operator_session"}
        )
        if _schema_error(validator, unconfirmed) is None:
            raise ContractError(f"unconfirmed intervention schema accepts {name}")
        unscoped = _intervention_probe(name, confirmation="explicit", arguments={})
        if _schema_error(validator, unscoped) is None:
            raise ContractError(f"unscoped intervention schema accepts {name}")


def _validator(schema: dict[str, Any]) -> Draft202012Validator:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ContractError(f"schema is not draft 2020-12: {exc.message}") from exc
    return Draft202012Validator(schema)


def _schema_error(validator: Draft202012Validator, instance: Any) -> str | None:
    error = next(validator.iter_errors(instance), None)
    if error is None:
        return None
    location = "/".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{location}: {error.message}"


def _project(doc: dict[str, Any]) -> dict[str, Any]:
    presentation = dict(doc.get("presentation") or {})
    presentation.pop("rendered", None)
    return {
        "api_version": doc.get("api_version"),
        "candidate_maturity": doc.get("candidate_maturity"),
        "command": doc.get("command"),
        "request_id": doc.get("request_id"),
        "profile": doc.get("profile"),
        "enforcement_route": doc.get("enforcement_route"),
        "authority_effect": doc.get("authority_effect"),
        "order_path": doc.get("order_path"),
        "status_code": doc.get("status_code"),
        "visibility": doc.get("visibility"),
        "result": doc.get("result"),
        "errors": doc.get("errors"),
        "presentation": presentation,
    }


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _refusal_disclosed(refusals: list[dict[str, Any]], refused_decisions: list[dict[str, Any]]) -> bool:
    if any(item.get("visible") is not True for item in refusals):
        return False
    covered = {
        (item.get("decision_id"), item.get("reason_code"))
        for item in refusals
    }
    return all((item.get("decision_id"), item.get("reason_code")) in covered for item in refused_decisions)


def _refusal_names_recorded_decision(
    refusals: list[dict[str, Any]], decision_rows: list[dict[str, Any]]
) -> bool:
    """True when a refusal row names a decision whose outcome is recorded."""
    recorded = {
        item.get("decision_id") for item in decision_rows if item.get("outcome") == "recorded"
    }
    return any(item.get("decision_id") in recorded for item in refusals)


def _contains_ansi(value: Any) -> bool:
    """True when any serialized string contains an ANSI escape."""
    if isinstance(value, str):
        return "\x1b" in value
    if isinstance(value, dict):
        return any(_contains_ansi(key) or _contains_ansi(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_ansi(item) for item in value)
    return False


def _rendered_status_tokens(rendered: str) -> list[str]:
    """Every delimited ``status=`` field, in order, without deduplication."""
    return re.findall(r"(?:^|\s)status=(\S*)", rendered)


def _status_text_mismatch(status: Any, text_status: Any, rendered: Any) -> bool:
    if text_status != status or not isinstance(rendered, str):
        return True
    return _rendered_status_tokens(rendered) != [status]


def classify_response(doc: dict[str, Any], allowed_commands: set[str]) -> str | None:
    """Return the negative-control code for an unsafe response, or None."""
    if not isinstance(doc, dict):
        return "privileged_order_bypass"
    keys = {str(key).lower() for key in _walk_keys(doc)}
    result = doc.get("result") if isinstance(doc.get("result"), dict) else {}
    visibility = doc.get("visibility") if isinstance(doc.get("visibility"), dict) else {}
    presentation = doc.get("presentation") if isinstance(doc.get("presentation"), dict) else {}
    profile = doc.get("profile") if isinstance(doc.get("profile"), dict) else {}
    if keys & FORBIDDEN_KEYS:
        return "privileged_order_bypass"
    if doc.get("enforcement_route") != SOLE_ROUTE or doc.get("order_path") != "none":
        return "privileged_order_bypass"
    if doc.get("status_code") == "usage":
        if doc.get("authority_effect") != "none":
            return "authority_effect_mismatch"
        if result:
            return "usage_carries_result"
        command = doc.get("command")
        if command is not None and not isinstance(command, str):
            return "privileged_order_bypass"
    else:
        if doc.get("command") not in allowed_commands:
            return "privileged_order_bypass"
        expected_effect = "intervention_request" if doc.get("command") in INTERVENTION_NAMES else "none"
        if doc.get("authority_effect") != expected_effect:
            return "authority_effect_mismatch"
        expected_intervention = INTERVENTION_NAMES.get(doc.get("command"))
        if expected_intervention is None and any(
            field in result for field in ("intervention", "recorded", "survives_restart")
        ):
            return "read_claims_intervention"
        if expected_intervention is not None and result.get("intervention") != expected_intervention:
            return "intervention_mismatch"
        if expected_intervention is not None and result.get("recorded") is True and result.get("survives_restart") is not True:
            return "recorded_intervention_not_persistent"
        if expected_intervention is not None and doc.get("status_code") == "ok" and (
            result.get("recorded") is not True or result.get("survives_restart") is not True
        ):
            return "unrecorded_intervention_success"
        if (
            doc.get("command") == "operator.profiles.inspect"
            and result.get("active_profile_class") != profile.get("profile_class")
        ):
            return "profile_class_mismatch"
    if result.get("order_submitted") is True:
        return "privileged_order_bypass"
    if profile.get("signature_state") != "illustrative_unsigned_fixture":
        return "privileged_order_bypass"
    refusals = _dicts(result.get("refusals"))
    decision_rows = _dicts(result.get("decisions"))
    decision_ids = [item.get("decision_id") for item in decision_rows]
    if len(decision_ids) != len(set(decision_ids)):
        return "duplicate_decision_id"
    if _refusal_names_recorded_decision(refusals, decision_rows):
        return "refusal_on_recorded_decision"
    refused_decisions = [item for item in decision_rows if item.get("outcome") == "refused"]
    dependencies = _dicts(result.get("dependencies"))
    unknown_items = [
        item for item in _dicts(result.get("items")) if item.get("state") in {"unknown", "uncertain"}
    ]
    collateral = result.get("collateral") if isinstance(result.get("collateral"), dict) else {}
    has_refusal = bool(refusals or refused_decisions)
    stale = result.get("stale") is True or any(item.get("status") == "stale" for item in dependencies)
    unavailable = any(item.get("status") == "unavailable" for item in dependencies)
    if _reconciled_with_unknown_orders(result):
        return "reconciled_with_unknown_orders"
    unreconciled = _is_unreconciled(result)
    higher_status = stale or unreconciled or unavailable
    has_uncertainty = bool(unknown_items) or collateral.get("state") == "unknown"
    claims_refusal = visibility.get("refusal") == "visible"
    if visibility.get("refusal") == "hidden" or has_refusal or claims_refusal:
        disclosed = claims_refusal and has_refusal and _refusal_disclosed(refusals, refused_decisions)
        if not disclosed or (not higher_status and doc.get("status_code") != "refused"):
            return "hidden_refusal"
    claims_uncertainty = visibility.get("uncertainty") == "visible"
    if visibility.get("uncertainty") == "hidden" or has_uncertainty or claims_uncertainty:
        evidenced = (
            claims_uncertainty
            and has_uncertainty
            and all(item.get("visible") is True for item in unknown_items)
        )
        if not evidenced:
            return "hidden_uncertainty"
    claims_stale = visibility.get("stale_data") == "visible"
    if visibility.get("stale_data") == "hidden" or stale or claims_stale:
        expected_status = "unavailable" if unavailable else "stale_or_unreconciled"
        visible = stale and claims_stale and doc.get("status_code") == expected_status
        if not visible:
            return "hidden_stale_data"
    claims_unreconciled = visibility.get("reconciliation") == "unreconciled_visible"
    if visibility.get("reconciliation") == "hidden" or unreconciled or claims_unreconciled:
        expected_status = "unavailable" if unavailable else "stale_or_unreconciled"
        visible = (
            unreconciled
            and claims_unreconciled
            and doc.get("status_code") == expected_status
            and result.get("restart_allowed") is False
        )
        if not visible:
            return "hidden_unreconciled"
    if doc.get("status_code") == "unavailable" and not unavailable:
        return "false_unavailable"
    if unavailable and doc.get("status_code") != "unavailable":
        return "hidden_unavailable"
    if (stale or unavailable) and result.get("watch_only") is not True:
        return "stale_not_watch_only"
    if (
        presentation.get("color_only_signal") is True
        or presentation.get("ansi_in_machine_output") is True
        or presentation.get("machine_readable") is not True
        or not presentation.get("text_status")
        or _contains_ansi(doc)
    ):
        return "color_only_signal"
    expected_exit = EXIT_CODES.get(doc.get("status_code"))
    if expected_exit is None or presentation.get("exit_code") != expected_exit:
        if doc.get("status_code") == "refused" and presentation.get("exit_code") == 0:
            return "success_exit_on_refusal"
        return "exit_code_mismatch"
    if _status_text_mismatch(doc.get("status_code"), presentation.get("text_status"), presentation.get("rendered")):
        return "contradictory_status_text"
    if doc.get("candidate_maturity") != MATURITY:
        return "maturity_promotion"
    errors = doc.get("errors")
    status = doc.get("status_code")
    if not isinstance(errors, list):
        return "status_error_mismatch"
    active_codes: set[str] = set()
    if unavailable:
        active_codes.add("unavailable")
    if stale or unreconciled:
        active_codes.add("stale_or_unreconciled")
    if has_refusal:
        active_codes.add("refused")
    if status == "usage":
        active_codes.add(status)
    if status == "ok":
        if errors or active_codes:
            return "status_error_mismatch"
    elif (
        status not in active_codes
        or not errors
        or any(
            not isinstance(item, dict)
            or item.get("code") not in active_codes
            or item.get("status_code") != item.get("code")
            or item.get("color_only") is not False
            for item in errors
        )
        or any(code not in {item.get("code") for item in errors if isinstance(item, dict)} for code in active_codes)
    ):
        return "status_error_mismatch"
    return None


def validate_response(
    validator: Draft202012Validator, doc: Any, allowed_commands: set[str]
) -> str | None:
    """Published validation path: response schema, then the semantic checks.

    JSON Schema cannot express unique ``decisions[*].decision_id``, so schema
    validation alone is not sufficient for a consumer.
    """
    problem = _schema_error(validator, doc)
    if problem is not None:
        return problem
    return classify_response(doc, allowed_commands)


def _event_error(event: dict[str, Any]) -> str | None:
    allowed = EVENT_COMMANDS.get(event.get("event_type"))
    if allowed is None or event.get("command") not in allowed:
        return "event type does not match the command"
    if event.get("visibility") != EVENT_VISIBILITY.get(event.get("event_type")):
        return "event visibility does not match the event type"
    if event.get("status_code") not in EVENT_STATUSES.get(event.get("event_type"), ()):
        return "event status does not match the event type"
    return None


def _response_claims_event(event_type: Any, response: dict[str, Any]) -> bool:
    """True when the response actually has the condition the event claims."""
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    visibility = response.get("visibility") if isinstance(response.get("visibility"), dict) else {}
    if event_type == "evidence_viewed":
        return response.get("command") == "operator.evidence.inspect" and "provenance" in result
    if event_type == "refusal_visible":
        refused = [item for item in _dicts(result.get("decisions")) if item.get("outcome") == "refused"]
        return visibility.get("refusal") == "visible" and bool(_dicts(result.get("refusals")) or refused)
    if event_type == "uncertainty_visible":
        unknown = [
            item for item in _dicts(result.get("items")) if item.get("state") in {"unknown", "uncertain"}
        ]
        collateral = result.get("collateral") if isinstance(result.get("collateral"), dict) else {}
        return visibility.get("uncertainty") == "visible" and bool(unknown or collateral.get("state") == "unknown")
    if event_type == "stale_visible":
        dependencies = _dicts(result.get("dependencies"))
        stale = result.get("stale") is True or any(item.get("status") == "stale" for item in dependencies)
        return visibility.get("stale_data") == "visible" and stale
    if event_type == "unreconciled_visible":
        return (
            visibility.get("reconciliation") == "unreconciled_visible"
            and _is_unreconciled(result)
            and not _reconciled_with_unknown_orders(result)
        )
    if event_type == "intervention_recorded":
        expected = INTERVENTION_NAMES.get(response.get("command"))
        return expected is not None and result.get("intervention") == expected and result.get("recorded") is True
    if event_type == "export_recorded":
        return response.get("command") in {"operator.audit.export", "operator.export.bundle"} and (
            "export_id" in result or "bundle_id" in result
        )
    return False


def _event_response_error(event: dict[str, Any], response: dict[str, Any] | None) -> str | None:
    """Return why an event does not belong to the response with its request id."""
    if response is None:
        return "event request does not match a response"
    if event.get("command") != response.get("command"):
        return "event command does not match the response"
    if event.get("status_code") != response.get("status_code"):
        return "event status does not match the response"
    if not _response_claims_event(event.get("event_type"), response):
        return "event condition does not match the response"
    return None


def _oracle_response_error(doc: dict[str, Any], commands: dict[str, dict[str, Any]]) -> str | None:
    command = commands.get(doc.get("command"))
    if command is None:
        return "command missing from catalog"
    effect = "intervention_request" if command["effect"] == "intervention" else "none"
    if doc.get("authority_effect") != effect:
        return "authority effect does not match the catalog"
    result = doc["result"]
    missing = [field for field in command["required_result_fields"] if field not in result]
    if missing:
        return f"missing result fields {missing}"
    if result.get("qualification") == "qualified":
        return "oracle claims strategy qualification"
    if result.get("secrets_included") is True or result.get("bounded_direct_authorized") is True:
        return "oracle claims a secret or a live authorization"
    if result.get("local_control_is_account_wide") is True:
        return "oracle treats a local control as account-wide"
    if doc.get("profile", {}).get("profile_class") != "A":
        return "illustrative oracle must stay on profile A"
    if (
        doc.get("command") == "operator.profiles.inspect"
        and result.get("active_profile_class") != doc.get("profile", {}).get("profile_class")
    ):
        return "active profile class does not match the response profile"
    expected_intervention = INTERVENTION_NAMES.get(doc["command"])
    if expected_intervention is None and any(
        field in result for field in ("intervention", "recorded", "survives_restart")
    ):
        return "read oracle carries an intervention field"
    if expected_intervention is not None:
        if result.get("intervention") != expected_intervention or result.get("recorded") is not True:
            return "intervention oracle is not a recorded non-order request"
        if result.get("survives_restart") is not True or result.get("order_submitted") is not False:
            return "stop or intervention oracle widens recovery or submits an order"
    text = doc["presentation"]["text_status"]
    rendered = doc["presentation"]["rendered"]
    if _status_text_mismatch(doc["status_code"], text, rendered) or "\x1b" in rendered:
        return "status text is missing from the rendered surface"
    return None


def _accepted_contracts() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("replacement_program", PROGRAM_CHECKER)
    if spec is None or spec.loader is None:
        raise ContractError("replacement program checker is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.EXPECTED_ACCEPTED_CONTRACTS)


def _check_registry_still_researched() -> None:
    program = _load_json(REPO / "docs" / "replacement" / "program.json")
    matches = [item for item in program.get("outcomes", []) if item.get("key") == "O7"]
    if len(matches) != 1:
        raise ContractError("O7 registry record missing")
    outcome = matches[0]
    evidence = outcome.get("evidence") if isinstance(outcome.get("evidence"), dict) else {}
    if evidence.get("claimed") != MATURITY:
        raise ContractError("O7 registry maturity is no longer researched")
    if outcome.get("completion_gate_status") != "pending_outcome_artifact":
        raise ContractError("O7 completion gate was promoted")
    if outcome.get("linear_issue") != "CSL-219":
        raise ContractError("O7 registry issue changed")
    contracts = _accepted_contracts()
    for path, digest in contracts.items():
        if _digest(REPO / path) != digest:
            raise ContractError(f"accepted CSL-212 digest changed: {path}")


def _check_contract_text(root: Path) -> None:
    text = (root / "CONTRACT.md").read_text(encoding="utf-8")
    if MARKER not in text or DECISION_HEADING not in text:
        raise ContractError("candidate marker or unresolved-decision heading missing")
    if "pending_outcome_artifact" not in text:
        raise ContractError("contract no longer records the pending outcome gate")
    for citation in REQUIRED_CITATIONS:
        if citation not in text:
            raise ContractError(f"research citation missing: {citation}")


def _check_supporting_documents(root: Path, commands: dict[str, dict[str, Any]]) -> None:
    catalog = _load_json(root / "catalog.json")
    gates = _load_json(root / "gates.json")
    parity = _load_json(root / "parity-matrix.json")
    packet = _load_json(root / "tui-decision-packet.json")
    decisions = _load_json(root / "unresolved-decisions.json")
    for document in (catalog, gates, parity, packet, decisions):
        if document.get("api_version") != API_VERSION or document.get("maturity") != MATURITY:
            raise ContractError("supporting document maturity drifted")
    if catalog.get("framework_selected") is not None or catalog.get("order_commands") != []:
        raise ContractError("catalog selects a framework or an order command")
    if gates.get("framework_selected") is not None or gates.get("framework_spike_ran") is not False:
        raise ContractError("gates record a framework selection or spike")
    if gates.get("tui_selected") is not False or gates.get("wcag_conformance_claimed") is not False:
        raise ContractError("gates claim a TUI or WCAG conformance")
    claims = gates.get("claims")
    if not isinstance(claims, dict) or any(value is not False for value in claims.values()):
        raise ContractError("gates claim a maturity promotion")
    if "contract_frozen" not in claims or "qualification" not in claims:
        raise ContractError("gates omit the non-claim flags")
    automation = gates.get("automation") if isinstance(gates.get("automation"), dict) else {}
    if automation.get("network_in_validator") is not False:
        raise ContractError("validator is marked networked")
    if automation.get("stable_exit_codes") != EXIT_CODES:
        raise ContractError("exit-code map drifted")
    authority = gates.get("authority") if isinstance(gates.get("authority"), dict) else {}
    if authority.get("enforcement_route") != SOLE_ROUTE or authority.get("surface_order_path") != "none":
        raise ContractError("authority gate widened the order path")
    if authority.get("widens_o6_authority") is not False or authority.get("local_control_implies_account_wide") is not False:
        raise ContractError("authority gate widens O6 or a local control")
    if authority.get("o6_fragment") != "docs/replacement/MONTH-PROGRAM.md#o6":
        raise ContractError("O6 fragment identifier changed")
    if authority.get("o6_outcome_artifact") != "pending_outcome_artifact":
        raise ContractError("O6 outcome artifact was treated as frozen")
    accepted = _accepted_contracts()
    month = "docs/replacement/MONTH-PROGRAM.md"
    if authority.get("o6_fragment_digest") != accepted[month] or _digest(REPO / month) != accepted[month]:
        raise ContractError("O6 fragment digest does not match the accepted month program")
    wcag = gates.get("wcag") if isinstance(gates.get("wcag"), dict) else {}
    if wcag.get("version") != "2.2" or wcag.get("level") != "AA" or wcag.get("parsing_4_1_1_required") is not False:
        raise ContractError("WCAG gate drifted")
    if wcag.get("aa_criteria_added_in_2_2") != ["2.4.11", "2.5.7", "2.5.8", "3.3.8"]:
        raise ContractError("WCAG 2.2 AA additions drifted")
    rows = parity.get("rows")
    if not isinstance(rows, list):
        raise ContractError("parity matrix missing")
    seen: list[str] = []
    journeys: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ContractError("parity row is not an object")
        name = row.get("command")
        if name not in commands or row.get("gui") != "same_api" or row.get("cli") != "same_api":
            raise ContractError("parity row is not the shared API")
        if row.get("order_path") != "none":
            raise ContractError("parity row has an order path")
        must_show = row.get("must_show")
        expected_show = REQUIRED_MUST_SHOW.get(name)
        if (
            not isinstance(must_show, list)
            or expected_show is None
            or set(must_show) != set(expected_show)
            or not set(must_show) <= set(commands[name]["required_result_fields"])
        ):
            raise ContractError(f"parity must_show drifted for {name}")
        if row.get("journey") != commands[name]["journey"]:
            raise ContractError(f"parity journey drifted for {name}")
        seen.append(name)
        journeys.add(row.get("journey"))
    if len(seen) != len(set(seen)) or set(seen) != set(commands) or set(journeys) != set(REQUIRED_JOURNEYS):
        raise ContractError("parity matrix does not cover the catalog")
    if (
        packet.get("human_decision") != "open"
        or packet.get("decided") is not False
        or packet.get("build_now") is not False
        or packet.get("owner") != "Lucas"
    ):
        raise ContractError("TUI packet decides the TUI")
    if decisions.get("decided_here") is not False or decisions.get("owner") != "Lucas":
        raise ContractError("unresolved decisions were marked decided")
    if set(REQUIRED_DECISIONS.values()) != set(REQUIRED_DECISION_CLASSES):
        raise ContractError("decision id pin dropped a class")
    seen_ids: list[str] = []
    for item in decisions.get("decisions", []):
        if not isinstance(item, dict):
            raise ContractError("decision row is not an object")
        if item.get("owner") != "Lucas" or item.get("status") != "open" or item.get("decided") is not False:
            raise ContractError("a human decision was closed in the candidate")
        decision_id = item.get("id")
        if REQUIRED_DECISIONS.get(decision_id) != item.get("class"):
            raise ContractError(f"unresolved decision drifted for {decision_id}")
        seen_ids.append(decision_id)
    if len(seen_ids) != len(set(seen_ids)) or set(seen_ids) != set(REQUIRED_DECISIONS):
        raise ContractError("unresolved decision ids drifted")


def _manifest_paths(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = manifest.get("files")
    if manifest.get("manifest_version") != 1 or manifest.get("maturity") != MATURITY:
        raise ContractError("fixture manifest header drifted")
    if not isinstance(files, list):
        raise ContractError("fixture manifest files missing")
    discovered = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.json")
        if path.name != "manifest.json"
    )
    listed = []
    for item in files:
        if not isinstance(item, dict):
            raise ContractError("manifest entry is not an object")
        rel = item.get("path")
        digest = item.get("sha256")
        if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts:
            raise ContractError("manifest path escapes the candidate")
        if item.get("role") not in {"oracle", "negative", "pinned"}:
            raise ContractError("manifest role is unknown")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ContractError("manifest digest is not sha256")
        target = root / rel
        if not target.is_file() or _digest(target) != digest:
            raise ContractError(f"frozen fixture digest mismatch: {rel}")
        listed.append(rel)
    if sorted(listed) != discovered or len(listed) != len(set(listed)):
        raise ContractError("manifest does not pin every candidate JSON file once")
    return files


def _control_line(control: str, outcome: str, detail: str = "") -> str:
    reason = CONTROL_REASONS[control]
    suffix = f" {detail}" if detail else ""
    return f"NEGATIVE CONTROL {control}: {outcome} ({reason}){suffix}"


def evaluate(root: Path | None = None) -> tuple[int, str, str]:
    """Check one candidate tree. Returns exit code, stdout, and stderr."""
    candidate = DEFAULT_ROOT if root is None else root
    try:
        _check_registry_still_researched()
        _check_contract_text(candidate)
        catalog = _load_json(candidate / "catalog.json")
        command_rows = catalog.get("commands")
        if not isinstance(command_rows, list) or not command_rows:
            raise ContractError("catalog commands missing")
        commands = {}
        for row in command_rows:
            if not isinstance(row, dict) or row.get("effect") not in {"read", "intervention"}:
                raise ContractError("catalog command is malformed")
            commands[row["name"]] = row
        if len(commands) != len(command_rows):
            raise ContractError("catalog command names repeat")
        if set(commands) != set(REQUIRED_COMMANDS):
            raise ContractError("catalog command set drifted")
        for name, row in commands.items():
            spec = REQUIRED_COMMANDS[name]
            if row.get("journey") != spec["journey"]:
                raise ContractError(f"catalog journey drifted for {name}")
            if row.get("effect") != spec["effect"]:
                raise ContractError(f"catalog effect drifted for {name}")
            if row.get("required_result_fields") != list(spec["fields"]):
                raise ContractError(f"catalog result fields drifted for {name}")
        _check_supporting_documents(candidate, commands)
        response_schema = _load_json(candidate / "schemas" / "operator-response.schema.json")
        command_schema = _load_json(candidate / "schemas" / "operator-command.schema.json")
        event_schema = _load_json(candidate / "schemas" / "operator-event.schema.json")
        error_schema = _load_json(candidate / "schemas" / "operator-error.schema.json")
        allowed = list(commands)
        for schema in (command_schema, event_schema):
            if schema["properties"]["command"]["enum"] != allowed:
                raise ContractError("schema command enum drifted from the catalog")
        response_enum = next(
            (
                clause.get("else", {}).get("properties", {}).get("command", {}).get("enum")
                for clause in response_schema.get("allOf", [])
                if isinstance(clause, dict) and "enum" in clause.get("else", {}).get("properties", {}).get("command", {})
            ),
            None,
        )
        if response_enum != allowed:
            raise ContractError("schema command enum drifted from the catalog")
        schema_fields = _schema_command_result_fields(response_schema)
        for name, spec in REQUIRED_COMMANDS.items():
            by_status = schema_fields.get(name)
            pinned = list(spec["fields"])
            if (
                not isinstance(by_status, dict)
                or set(by_status) != set(RESULT_STATUSES)
                or any(fields != pinned for fields in by_status.values())
            ):
                raise ContractError(f"schema result fields drifted for {name}")
        response_validator = _validator(response_schema)
        command_validator = _validator(command_schema)
        _require_intervention_safeguards(command_validator)
        event_validator = _validator(event_schema)
        error_validator = _validator(error_schema)
        manifest = _load_json(candidate / "fixtures" / "manifest.json")
        entries = _manifest_paths(candidate, manifest)
        oracle_kinds: dict[str, Any] = {}
        negatives: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if entry.get("role") == "oracle":
                document = _load_json(candidate / entry["path"])
                kind = document.get("kind") if isinstance(document, dict) else None
                if kind not in ORACLE_KINDS or kind in oracle_kinds:
                    raise ContractError(f"oracle kind missing or repeated in {entry['path']}")
                oracle_kinds[kind] = document
            elif entry.get("role") == "negative":
                control = entry.get("control")
                if control not in REQUIRED_CONTROLS or control in negatives:
                    raise ContractError(f"negative control missing or repeated in {entry['path']}")
                negatives[control] = _load_json(candidate / entry["path"])
        if set(oracle_kinds) != set(ORACLE_KINDS) or set(negatives) != set(REQUIRED_CONTROLS):
            raise ContractError("oracle kinds or negative controls are incomplete")
        _check_oracles(
            oracle_kinds,
            commands,
            response_validator,
            command_validator,
            event_validator,
            error_validator,
        )
        responses = oracle_kinds["responses"].get("responses")
        baseline = next(
            (
                item
                for item in responses
                if isinstance(item, dict) and item.get("request_id") == PRIVILEGED_BASELINE_ID
            ),
            None,
        ) if isinstance(responses, list) else None
        lines = [
            _check_negative(
                control,
                negatives[control],
                response_validator,
                command_validator,
                set(commands),
                baseline,
            )
            for control in REQUIRED_CONTROLS
        ]
    except (OSError, ContractError, SchemaError, KeyError, TypeError, AttributeError) as exc:
        return 1, "", f"o7-operator-contract: FAIL: {exc}\n"
    stdout = "\n".join([*lines, SUMMARY]) + "\n"
    if "UNEXPECTED_ACCEPT" in stdout or "WRONG_FAILURE" in stdout:
        return 1, stdout, "o7-operator-contract: FAIL: negative control did not fail as designed\n"
    return 0, stdout, ""


def _check_oracles(kinds: dict[str, Any], commands: dict[str, dict[str, Any]], response_validator, command_validator, event_validator, error_validator) -> None:
    responses = kinds["responses"].get("responses")
    if not isinstance(responses, list):
        raise ContractError("oracle responses missing")
    seen: set[str] = set()
    for doc in responses:
        label = doc.get("command") if isinstance(doc, dict) else "response"
        _require_schema(response_validator, doc, f"oracle response {label}")
        problem = classify_response(doc, set(commands))
        if problem is not None:
            raise ContractError(f"oracle response classified as {problem}")
        oracle_problem = _oracle_response_error(doc, commands)
        if oracle_problem is not None:
            raise ContractError(f"oracle response: {oracle_problem}")
        for error in doc["errors"]:
            _require_schema(error_validator, error, "oracle error")
        seen.add(doc["command"])
    if seen != set(commands):
        raise ContractError("oracle responses do not cover the catalog")
    by_request: dict[str, dict[str, Any]] = {}
    for doc in responses:
        if not isinstance(doc, dict):
            continue
        request_id = doc.get("request_id")
        if not isinstance(request_id, str) or request_id in by_request:
            raise ContractError("oracle response request ids drifted")
        by_request[request_id] = doc
    pairs = kinds["pairs"].get("pairs")
    if not isinstance(pairs, list):
        raise ContractError("oracle pairs missing")
    pair_ids = []
    for pair in pairs:
        gui, cli = pair.get("gui"), pair.get("cli")
        _require_schema(response_validator, gui, "oracle gui")
        _require_schema(response_validator, cli, "oracle cli")
        if classify_response(gui, set(commands)) or classify_response(cli, set(commands)):
            raise ContractError("oracle pair carries a hidden state")
        for side in (gui, cli):
            pair_problem = _oracle_response_error(side, commands)
            if pair_problem is not None:
                raise ContractError(f"oracle pair: {pair_problem}")
        if _project(gui) != _project(cli):
            raise ContractError("oracle pair semantic projection diverges")
        if gui.get("surface") != "gui" or cli.get("surface") != "cli":
            raise ContractError("oracle pair surfaces are swapped")
        if gui["presentation"]["rendered"] == cli["presentation"]["rendered"]:
            raise ContractError("oracle pair does not exercise presentation")
        expected_command = REQUIRED_ORACLE_PAIR_COMMANDS.get(pair.get("pair_id"))
        if (
            expected_command is None
            or gui.get("command") != expected_command
            or cli.get("command") != expected_command
        ):
            raise ContractError(f"oracle pair command drifted for {pair.get('pair_id')}")
        pair_ids.append(pair.get("pair_id"))
    if tuple(pair_ids) != REQUIRED_ORACLE_PAIRS:
        raise ContractError("oracle pair ids drifted")
    for command in kinds["commands"].get("commands", []):
        _require_schema(command_validator, command, "oracle command")
        arguments = command.get("arguments") if isinstance(command.get("arguments"), dict) else {}
        if command["command"] in INTERVENTION_NAMES:
            if command.get("confirmation") != "explicit" or arguments.get("scope") != "operator_session":
                raise ContractError("intervention command skips confirmation or session scope")
        elif command.get("confirmation") != "not_required":
            raise ContractError("read command requires a confirmation it does not need")
    if {item["command"] for item in kinds["commands"].get("commands", [])} != set(commands):
        raise ContractError("oracle commands do not cover the catalog")
    for event in kinds["events"].get("events", []):
        _require_schema(event_validator, event, "oracle event")
        if event.get("visibility") == "hidden":
            raise ContractError("oracle event hides state")
        event_problem = _event_error(event)
        if event_problem is not None:
            raise ContractError(f"oracle event: {event_problem}")
        match_problem = _event_response_error(event, by_request.get(event.get("request_id")))
        if match_problem is not None:
            raise ContractError(f"oracle event: {match_problem}")
    seen_types = {
        event.get("event_type") for event in kinds["events"].get("events", []) if isinstance(event, dict)
    }
    if seen_types != set(EVENT_COMMANDS):
        raise ContractError("oracle events do not cover the event types")
    for error in kinds["errors"].get("errors", []):
        _require_schema(error_validator, error, "oracle error catalog")
        if error.get("code") != error.get("status_code"):
            raise ContractError("oracle error code does not match its status")
    if {item["code"] for item in kinds["errors"].get("errors", [])} != set(EXIT_CODES) - {"ok"}:
        raise ContractError("oracle errors do not cover the failure statuses")


def _repaired_status_text_body(body: dict[str, Any]) -> dict[str, Any]:
    """Restore only the rendered status on the contradictory-status control."""
    repaired = dict(body)
    presentation = body.get("presentation")
    status = body.get("status_code")
    if not isinstance(presentation, dict) or not isinstance(status, str):
        return repaired
    repaired["presentation"] = {**presentation, "text_status": status, "rendered": f"status={status}"}
    return repaired


def _repaired_stale_body(body: dict[str, Any]) -> dict[str, Any]:
    """Restore only watch_only on the stale negative control."""
    repaired = dict(body)
    result = body.get("result")
    if not isinstance(result, dict):
        return repaired
    repaired["result"] = {**result, "watch_only": True}
    return repaired


def _repaired_command_body(control: str, body: dict[str, Any]) -> dict[str, Any]:
    """Restore only the safeguard named by a command negative control."""
    repaired = dict(body)
    if control == "unconfirmed_intervention":
        repaired["confirmation"] = "explicit"
        return repaired
    arguments = body.get("arguments")
    if not isinstance(arguments, dict):
        # Leave a malformed value alone so the repaired body still fails.
        return repaired
    repaired["arguments"] = {**arguments, "scope": "operator_session"}
    return repaired


def _assign(doc: dict[str, Any], path: str, value: Any) -> bool:
    """Set or remove a dotted field. None removes the leaf."""
    cursor: Any = doc
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(cursor, dict):
            return False
        cursor = cursor.get(part)
    if not isinstance(cursor, dict) or not parts[-1]:
        return False
    if value is None:
        cursor.pop(parts[-1], None)
    else:
        cursor[parts[-1]] = value
    return True


def _check_privileged(
    document: dict[str, Any],
    validator: Draft202012Validator,
    allowed: set[str],
    baseline: dict[str, Any] | None,
) -> str:
    """Each privileged safeguard must fail alone, and its repair must pass."""
    control = "privileged_order_bypass"
    safeguards = document.get("safeguards")
    if not isinstance(safeguards, list) or not isinstance(baseline, dict):
        return _control_line(control, "WRONG_FAILURE")
    if (
        document.get("baseline_request_id") != PRIVILEGED_BASELINE_ID
        or baseline.get("request_id") != PRIVILEGED_BASELINE_ID
    ):
        return _control_line(control, "WRONG_FAILURE")
    by_name: dict[str, dict[str, Any]] = {}
    for item in safeguards:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return _control_line(control, "WRONG_FAILURE")
        if item["name"] in by_name:
            return _control_line(control, "WRONG_FAILURE")
        by_name[item["name"]] = item
    if set(by_name) != set(REQUIRED_PRIVILEGED):
        return _control_line(control, "WRONG_FAILURE")
    for name, (expected, pinned_field, pinned_value, pinned_repair) in REQUIRED_PRIVILEGED.items():
        item = by_name[name]
        field = item.get("field")
        if (
            field != pinned_field
            or item.get("value") != pinned_value
            or type(item.get("value")) is not type(pinned_value)
            or item.get("repair") != pinned_repair
            or type(item.get("repair")) is not type(pinned_repair)
        ):
            return _control_line(control, "WRONG_FAILURE")
        broken = json.loads(json.dumps(baseline))
        repaired = json.loads(json.dumps(baseline))
        if not _assign(broken, field, item.get("value")) or not _assign(repaired, field, item.get("repair")):
            return _control_line(control, "WRONG_FAILURE")
        schema_error = _schema_error(validator, broken)
        classified = classify_response(broken, allowed)
        if schema_error is None or classified != expected:
            detail = schema_error or f"{name} classified={classified}"
            return _control_line(control, "WRONG_FAILURE", detail)
        repaired_error = _schema_error(validator, repaired)
        repaired_class = classify_response(repaired, allowed)
        if repaired_error is not None or repaired_class is not None:
            detail = repaired_error or f"{name} repaired classified={repaired_class}"
            return _control_line(control, "WRONG_FAILURE", detail)
    return _control_line(control, "FAIL as designed")


def _require_schema(validator: Draft202012Validator, instance: Any, label: str) -> None:
    error = _schema_error(validator, instance)
    if error is not None:
        raise ContractError(f"{label} schema: {error}")


def _check_negative(
    control: str,
    document: dict[str, Any],
    validator: Draft202012Validator,
    command_validator: Draft202012Validator,
    allowed: set[str],
    baseline: dict[str, Any] | None = None,
) -> str:
    if not isinstance(document, dict) or document.get("control") != control:
        return _control_line(control, "WRONG_FAILURE")
    if control == "privileged_order_bypass":
        return _check_privileged(document, validator, allowed, baseline)
    if control in COMMAND_SCHEMA_CONTROLS:
        body = document.get("body")
        schema_error = None if not isinstance(body, dict) else _schema_error(command_validator, body)
        if schema_error is None:
            return _control_line(control, "UNEXPECTED_ACCEPT")
        if not isinstance(body, dict):
            return _control_line(control, "WRONG_FAILURE")
        repaired_error = _schema_error(command_validator, _repaired_command_body(control, body))
        if repaired_error is not None:
            return _control_line(control, "WRONG_FAILURE", repaired_error)
        return _control_line(control, "FAIL as designed")
    if control == "gui_cli_semantic_divergence":
        gui, cli = document.get("gui"), document.get("cli")
        gui_error = _schema_error(validator, gui)
        cli_error = _schema_error(validator, cli)
        schema_ok = gui_error is None and cli_error is None
        classified = None
        if isinstance(gui, dict) and isinstance(cli, dict):
            left = classify_response(gui, allowed)
            right = classify_response(cli, allowed)
            classified = left or right
            if classified is None and _project(gui) != _project(cli):
                classified = "gui_cli_semantic_divergence"
        if schema_ok and classified == control:
            return _control_line(control, "FAIL as designed")
        detail = gui_error or cli_error or f"classified={classified}"
        outcome = "UNEXPECTED_ACCEPT" if classified is None and schema_ok else "WRONG_FAILURE"
        return _control_line(control, outcome, detail)
    if control == "contradictory_status_text":
        body = document.get("body")
        schema_error = None if not isinstance(body, dict) else _schema_error(validator, body)
        classified = classify_response(body, allowed) if isinstance(body, dict) else None
        if schema_error is None or classified != control or not isinstance(body, dict):
            if classified is None and schema_error is None:
                return _control_line(control, "UNEXPECTED_ACCEPT")
            detail = schema_error or f"classified={classified}"
            return _control_line(control, "WRONG_FAILURE", detail)
        repaired_error = _schema_error(validator, _repaired_status_text_body(body))
        if repaired_error is not None:
            return _control_line(control, "WRONG_FAILURE", repaired_error)
        return _control_line(control, "FAIL as designed")
    if control == "stale_not_watch_only":
        body = document.get("body")
        schema_error = None if not isinstance(body, dict) else _schema_error(validator, body)
        classified = classify_response(body, allowed) if isinstance(body, dict) else None
        if schema_error is None or classified != control or not isinstance(body, dict):
            if classified is None and schema_error is None:
                return _control_line(control, "UNEXPECTED_ACCEPT")
            detail = schema_error or f"classified={classified}"
            return _control_line(control, "WRONG_FAILURE", detail)
        repaired_error = _schema_error(validator, _repaired_stale_body(body))
        if repaired_error is not None:
            return _control_line(control, "WRONG_FAILURE", repaired_error)
        return _control_line(control, "FAIL as designed")
    body = document.get("body")
    schema_error = None if not isinstance(body, dict) else _schema_error(validator, body)
    classified = classify_response(body, allowed) if isinstance(body, dict) else None
    schema_matches = schema_error is None if control in SCHEMA_VALID_CONTROLS else schema_error is not None
    if schema_matches and classified == control:
        return _control_line(control, "FAIL as designed")
    if classified is None and schema_error is None:
        return _control_line(control, "UNEXPECTED_ACCEPT")
    detail = schema_error or f"classified={classified}"
    return _control_line(control, "WRONG_FAILURE", detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    code, stdout, stderr = evaluate(args.root)
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
