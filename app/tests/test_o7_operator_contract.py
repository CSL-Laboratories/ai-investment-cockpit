"""Provider-free checks for the CSL-219 O7 candidate.

The exact candidate command is ``python -X utf8 -B tools/check_o7_operator_contract.py``.
Negative controls must fail as designed. This module does not open a socket, read
credentials, or call a provider. It does not claim qualification.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from tests.o7_contract_report import O7_NEGATIVE_LINES

ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = ROOT / "tools" / "check_o7_operator_contract.py"
SPEC = importlib.util.spec_from_file_location("o7_operator_contract", CHECKER_PATH)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

EXACT_COMMAND = (
    sys.executable,
    "-X",
    "utf8",
    "-B",
    "tools/check_o7_operator_contract.py",
)
EXPECTED_LINES = (
    "NEGATIVE CONTROL gui_cli_semantic_divergence: FAIL as designed (semantic_projection_mismatch)",
    "NEGATIVE CONTROL hidden_refusal: FAIL as designed (refusal_not_visible)",
    "NEGATIVE CONTROL hidden_uncertainty: FAIL as designed (uncertainty_not_visible)",
    "NEGATIVE CONTROL hidden_stale_data: FAIL as designed (stale_not_visible)",
    "NEGATIVE CONTROL hidden_unreconciled: FAIL as designed (unreconciled_not_visible)",
    "NEGATIVE CONTROL privileged_order_bypass: FAIL as designed (surface_order_path)",
    "NEGATIVE CONTROL color_only_signal: FAIL as designed (color_only_signal)",
    "NEGATIVE CONTROL success_exit_on_refusal: FAIL as designed (exit_code_hides_refusal)",
    "NEGATIVE CONTROL contradictory_status_text: FAIL as designed (status_text_mismatch)",
    "NEGATIVE CONTROL unconfirmed_intervention: FAIL as designed (intervention_not_explicit)",
    "NEGATIVE CONTROL unscoped_intervention: FAIL as designed (intervention_not_scoped)",
    "NEGATIVE CONTROL stale_not_watch_only: FAIL as designed (stale_not_watch_only)",
)
ACCEPTED = {
    "docs/replacement/SUCCESSOR-PRODUCT.md": "6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e",
    "docs/replacement/ISSUE-CONTRACT.md": "f1fb50dab4b166ca476698fc3974d6a1c2003b16df1d89ff8930060ad04b5094",
    "docs/replacement/MONTH-PROGRAM.md": "f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001",
    "docs/replacement/SCOPE-AUDIT.md": "d23dcc769e743d199ae4142f0e20ad5feebfe310c71d9f57fdd9d647c230db8c",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _retarget(root: Path, relative: str) -> None:
    manifest_path = root / "fixtures" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = _sha256(root / relative)
    for item in manifest["files"]:
        if item["path"] == relative:
            item["sha256"] = digest
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def test_exact_candidate_command_shows_negative_controls_failing(pytestconfig) -> None:
    completed = subprocess.run(
        EXACT_COMMAND,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.startswith("NEGATIVE CONTROL ")]
    pytestconfig.stash[O7_NEGATIVE_LINES] = lines
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert lines == list(EXPECTED_LINES)
    assert "qualification_claimed=false" in completed.stdout
    assert "qualification_claimed=true" not in completed.stdout
    assert "contract_frozen_claimed=false" in completed.stdout
    assert "contract_frozen_claimed=true" not in completed.stdout
    assert "PASS" not in completed.stdout
    for path, digest in ACCEPTED.items():
        assert _sha256(ROOT / path) == digest
    program = json.loads((ROOT / "docs" / "replacement" / "program.json").read_text(encoding="utf-8"))
    outcome = next(item for item in program["outcomes"] if item["key"] == "O7")
    assert outcome["evidence"]["claimed"] == "researched"
    assert outcome["completion_gate_status"] == "pending_outcome_artifact"


def test_checker_does_not_open_a_socket(monkeypatch) -> None:
    def blocked(*_args, **_kwargs):
        raise AssertionError("O7 checker opened a socket")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    code, stdout, stderr = CHECKER.evaluate(ROOT / "docs" / "replacement" / "outcomes" / "O7")
    assert code == 0, stderr
    assert "NEGATIVE CONTROL privileged_order_bypass: FAIL as designed" in stdout


def test_equal_projections_are_not_counted_as_divergence(tmp_path: Path) -> None:
    root = tmp_path / "O7"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", root)
    relative = "fixtures/negative/gui-cli-divergence.json"
    document = json.loads((root / relative).read_text(encoding="utf-8"))
    document["cli"]["result"]["positions"][0]["quantity"] = document["gui"]["result"]["positions"][0]["quantity"]
    (root / relative).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _retarget(root, relative)
    code, stdout, stderr = CHECKER.evaluate(root)
    assert code == 1
    assert "NEGATIVE CONTROL gui_cli_semantic_divergence: UNEXPECTED_ACCEPT" in stdout
    assert "negative control did not fail as designed" in stderr


def test_promotion_claim_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "O7"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", root)
    gates_path = root / "gates.json"
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gates["claims"]["contract_frozen"] = True
    gates_path.write_text(json.dumps(gates, indent=2) + "\n", encoding="utf-8")
    code, stdout, stderr = CHECKER.evaluate(root)
    assert code == 1
    assert stdout == ""
    assert "maturity promotion" in stderr


def _candidate_documents() -> tuple[dict, dict, object]:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    commands = {row["name"] for row in catalog["commands"]}
    schema = json.loads((root / "schemas" / "operator-response.schema.json").read_text(encoding="utf-8"))
    validator = CHECKER._validator(schema)
    responses = json.loads((root / "fixtures" / "oracle" / "responses.json").read_text(encoding="utf-8"))
    by_request = {item["request_id"]: item for item in responses["responses"]}
    return by_request, commands, validator


def test_portfolio_shapes_and_compound_status_are_visible() -> None:
    by_request, commands, validator = _candidate_documents()
    for request_id in ("req_positions_empty", "req_positions_signed", "req_decisions_refused_and_stale"):
        document = by_request[request_id]
        assert CHECKER._schema_error(validator, document) is None
        assert CHECKER.classify_response(document, commands) is None
    signed = by_request["req_positions_signed"]["result"]["positions"]
    assert [item["quantity"] for item in signed] == ["100.5", "-2"]
    assert by_request["req_positions_empty"]["result"]["positions"] == []
    compound = by_request["req_decisions_refused_and_stale"]
    assert compound["status_code"] == "stale_or_unreconciled"
    assert compound["visibility"]["refusal"] == "visible"
    assert {item["code"] for item in compound["errors"]} == {"stale_or_unreconciled", "refused"}


def test_hidden_result_states_and_empty_results_fail() -> None:
    by_request, commands, validator = _candidate_documents()
    decisions = copy.deepcopy(by_request["req_decisions"])
    decisions["result"]["refusals"] = []
    decisions["status_code"] = "ok"
    decisions["visibility"]["refusal"] = "absent_not_hidden"
    decisions["errors"] = []
    decisions["presentation"]["exit_code"] = 0
    decisions["presentation"]["text_status"] = "ok"
    decisions["presentation"]["rendered"] = "status=ok canonical decisions"
    assert CHECKER.classify_response(decisions, commands) == "hidden_refusal"

    health = copy.deepcopy(by_request["req_health_stale"])
    health["result"]["stale"] = False
    health["status_code"] = "ok"
    health["visibility"]["stale_data"] = "absent_not_hidden"
    health["errors"] = []
    health["presentation"]["exit_code"] = 0
    health["presentation"]["text_status"] = "ok"
    health["presentation"]["rendered"] = "status=ok canonical health"
    assert CHECKER.classify_response(health, commands) == "hidden_stale_data"

    positions = copy.deepcopy(by_request["req_positions"])
    positions["result"]["collateral"]["state"] = "unknown"
    assert CHECKER.classify_response(positions, commands) == "hidden_uncertainty"

    contradicted = copy.deepcopy(by_request["req_decisions"])
    contradicted["presentation"]["text_status"] = "ok"
    contradicted["presentation"]["rendered"] = "status=ok canonical decisions"
    assert CHECKER.classify_response(contradicted, commands) == "contradictory_status_text"

    empty = copy.deepcopy(by_request["req_positions"])
    empty["result"] = {}
    assert CHECKER._schema_error(validator, empty) is not None


def test_intervention_commands_require_explicit_session_scope() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    schema = json.loads((root / "schemas" / "operator-command.schema.json").read_text(encoding="utf-8"))
    validator = CHECKER._validator(schema)
    commands = json.loads((root / "fixtures" / "oracle" / "commands.json").read_text(encoding="utf-8"))
    stop = next(item for item in commands["commands"] if item["command"] == "operator.interventions.request_stop")
    assert CHECKER._schema_error(validator, stop) is None
    unconfirmed = copy.deepcopy(stop)
    unconfirmed["confirmation"] = "not_required"
    assert CHECKER._schema_error(validator, unconfirmed) is not None
    unscoped = copy.deepcopy(stop)
    unscoped["arguments"] = {}
    assert CHECKER._schema_error(validator, unscoped) is not None


def test_unavailable_and_stale_health_fail_closed() -> None:
    by_request, commands, validator = _candidate_documents()
    unavailable = by_request["req_health_unavailable"]
    assert CHECKER._schema_error(validator, unavailable) is None
    assert CHECKER.classify_response(unavailable, commands) is None
    assert unavailable["presentation"]["exit_code"] == 4
    hidden = copy.deepcopy(unavailable)
    hidden["status_code"] = "ok"
    hidden["errors"] = []
    hidden["presentation"]["exit_code"] = 0
    hidden["presentation"]["text_status"] = "ok"
    hidden["presentation"]["rendered"] = "status=ok canonical health"
    assert CHECKER.classify_response(hidden, commands) == "hidden_unavailable"
    stale = copy.deepcopy(by_request["req_health_stale"])
    stale["result"]["watch_only"] = False
    assert CHECKER.classify_response(stale, commands) == "stale_not_watch_only"
    positions = copy.deepcopy(by_request["req_positions"])
    positions["result"]["stale"] = True
    positions["result"]["watch_only"] = False
    positions["status_code"] = "stale_or_unreconciled"
    positions["visibility"]["stale_data"] = "visible"
    positions["errors"] = [
        {
            "code": "stale_or_unreconciled",
            "status_code": "stale_or_unreconciled",
            "message": "Positions are stale.",
            "recovery": "Refresh the positions read.",
            "color_only": False,
        }
    ]
    positions["presentation"]["exit_code"] = 3
    positions["presentation"]["text_status"] = "stale_or_unreconciled"
    positions["presentation"]["rendered"] = "status=stale_or_unreconciled positions"
    assert CHECKER.classify_response(positions, commands) == "stale_not_watch_only"
    assert CHECKER._schema_error(validator, positions) is not None
    positions["result"]["watch_only"] = True
    assert CHECKER.classify_response(positions, commands) is None
    assert CHECKER._schema_error(validator, positions) is None


def test_healthy_dependencies_cannot_claim_an_outage() -> None:
    by_request, commands, _validator = _candidate_documents()
    healthy = copy.deepcopy(by_request["req_health"])
    healthy["status_code"] = "unavailable"
    healthy["result"]["watch_only"] = True
    healthy["errors"] = [
        {
            "code": "unavailable",
            "status_code": "unavailable",
            "message": "Unavailable claimed without an unread dependency.",
            "recovery": "Read the dependency status again.",
            "color_only": False,
        }
    ]
    healthy["presentation"]["exit_code"] = 4
    healthy["presentation"]["text_status"] = "unavailable"
    healthy["presentation"]["rendered"] = "status=unavailable canonical health"
    assert CHECKER.classify_response(healthy, commands) == "false_unavailable"


def test_authority_effect_and_event_type_follow_the_command() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    by_request, commands, response_validator = _candidate_documents()
    read = copy.deepcopy(by_request["req_positions"])
    read["authority_effect"] = "intervention_request"
    assert CHECKER._schema_error(response_validator, read) is not None
    assert CHECKER.classify_response(read, commands) == "authority_effect_mismatch"
    event_schema = json.loads((root / "schemas" / "operator-event.schema.json").read_text(encoding="utf-8"))
    event_validator = CHECKER._validator(event_schema)
    events = json.loads((root / "fixtures" / "oracle" / "events.json").read_text(encoding="utf-8"))
    for event in events["events"]:
        assert CHECKER._schema_error(event_validator, event) is None
        assert CHECKER._event_error(event) is None
    mismatched = copy.deepcopy(events["events"][0])
    mismatched["event_type"] = "intervention_recorded"
    assert CHECKER._schema_error(event_validator, mismatched) is not None
    assert CHECKER._event_error(mismatched) == "event type does not match the command"


def test_error_codes_and_intervention_names_match() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    by_request, commands, response_validator = _candidate_documents()
    stop = copy.deepcopy(by_request["req_stop"])
    stop["result"]["intervention"] = "revocation"
    assert CHECKER.classify_response(stop, commands) == "intervention_mismatch"
    assert CHECKER._schema_error(response_validator, stop) is not None
    error_schema = json.loads((root / "schemas" / "operator-error.schema.json").read_text(encoding="utf-8"))
    error_validator = CHECKER._validator(error_schema)
    errors = json.loads((root / "fixtures" / "oracle" / "errors.json").read_text(encoding="utf-8"))
    for error in errors["errors"]:
        assert error["code"] == error["status_code"]
        assert CHECKER._schema_error(error_validator, error) is None
    mismatched = copy.deepcopy(errors["errors"][0])
    mismatched["status_code"] = "unavailable" if mismatched["code"] != "unavailable" else "refused"
    assert CHECKER._schema_error(error_validator, mismatched) is not None


def test_reads_do_not_carry_intervention_fields() -> None:
    by_request, commands, response_validator = _candidate_documents()
    positions = copy.deepcopy(by_request["req_positions"])
    assert positions["authority_effect"] == "none"
    positions["result"]["intervention"] = "stop"
    positions["result"]["recorded"] = True
    assert CHECKER.classify_response(positions, commands) == "read_claims_intervention"
    assert CHECKER._schema_error(response_validator, positions) is not None
    recorded_only = copy.deepcopy(by_request["req_positions"])
    recorded_only["result"]["survives_restart"] = True
    assert CHECKER.classify_response(recorded_only, commands) == "read_claims_intervention"
    assert CHECKER._schema_error(response_validator, recorded_only) is not None


def test_recorded_stop_persists_and_ansi_is_absent_from_every_string() -> None:
    by_request, commands, response_validator = _candidate_documents()
    stop = copy.deepcopy(by_request["req_stop"])
    stop["result"]["survives_restart"] = False
    assert CHECKER.classify_response(stop, commands) == "recorded_intervention_not_persistent"
    assert CHECKER._schema_error(response_validator, stop) is not None
    for request_id in ("req_stop", "req_reconcile", "req_revoke"):
        unrecorded = copy.deepcopy(by_request[request_id])
        assert unrecorded["status_code"] == "ok"
        unrecorded["result"]["recorded"] = False
        unrecorded["result"]["survives_restart"] = False
        assert CHECKER.classify_response(unrecorded, commands) == "unrecorded_intervention_success"
        assert CHECKER._schema_error(response_validator, unrecorded) is not None
    tainted = copy.deepcopy(by_request["req_positions"])
    tainted["errors"] = [
        {
            "code": "usage",
            "status_code": "usage",
            "message": "status\x1b[31mred",
            "recovery": "Read the text status.",
            "color_only": False,
        }
    ]
    tainted["presentation"]["ansi_in_machine_output"] = False
    assert "\x1b" not in tainted["presentation"]["rendered"]
    assert CHECKER.classify_response(tainted, commands) == "color_only_signal"
    assert CHECKER._schema_error(response_validator, tainted) is not None


def test_profile_class_agrees_and_intervention_controls_fail_on_their_own_field(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    by_request, commands, response_validator = _candidate_documents()
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    catalog_commands = {row["name"]: row for row in catalog["commands"]}
    profiles = copy.deepcopy(by_request["req_profiles"])
    profiles["result"]["active_profile_class"] = "C"
    assert profiles["profile"]["profile_class"] == "A"
    assert CHECKER.classify_response(profiles, commands) == "profile_class_mismatch"
    assert CHECKER._schema_error(response_validator, profiles) is not None
    assert CHECKER._oracle_response_error(profiles, catalog_commands) is not None
    command_schema = json.loads((root / "schemas" / "operator-command.schema.json").read_text(encoding="utf-8"))
    command_validator = CHECKER._validator(command_schema)
    for name in ("unconfirmed-intervention.json", "unscoped-intervention.json"):
        document = json.loads((root / "fixtures" / "negative" / name).read_text(encoding="utf-8"))
        body = document["body"]
        assert CHECKER._schema_error(command_validator, body) is not None
        assert CHECKER._schema_error(command_validator, CHECKER._repaired_command_body(document["control"], body)) is None
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    fixture = copied / "fixtures" / "negative" / "unconfirmed-intervention.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    document["body"]["api_version"] = "o7.operator.v1"
    fixture.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "fixtures/negative/unconfirmed-intervention.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert "NEGATIVE CONTROL unconfirmed_intervention: WRONG_FAILURE" in stdout
    assert stderr.startswith("o7-operator-contract: FAIL:")
    unscoped = copied / "fixtures" / "negative" / "unscoped-intervention.json"
    unscoped_document = json.loads(unscoped.read_text(encoding="utf-8"))
    unscoped_document["body"]["arguments"] = []
    unscoped.write_text(json.dumps(unscoped_document, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "fixtures/negative/unscoped-intervention.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert "NEGATIVE CONTROL unscoped_intervention: WRONG_FAILURE" in stdout


def test_stale_control_fails_only_when_watch_only_is_false(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    response_schema = json.loads((root / "schemas" / "operator-response.schema.json").read_text(encoding="utf-8"))
    response_validator = CHECKER._validator(response_schema)
    document = json.loads((root / "fixtures" / "negative" / "stale-not-watch-only.json").read_text(encoding="utf-8"))
    body = document["body"]
    assert CHECKER._schema_error(response_validator, body) is not None
    assert CHECKER._schema_error(response_validator, CHECKER._repaired_stale_body(body)) is None
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    fixture = copied / "fixtures" / "negative" / "stale-not-watch-only.json"
    tampered = json.loads(fixture.read_text(encoding="utf-8"))
    tampered["body"]["api_version"] = "o7.operator.v1"
    fixture.write_text(json.dumps(tampered, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "fixtures/negative/stale-not-watch-only.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert "NEGATIVE CONTROL stale_not_watch_only: WRONG_FAILURE" in stdout
    assert stderr.startswith("o7-operator-contract: FAIL:")


def test_contradictory_status_fails_only_on_the_rendered_status(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    response_schema = json.loads((root / "schemas" / "operator-response.schema.json").read_text(encoding="utf-8"))
    response_validator = CHECKER._validator(response_schema)
    document = json.loads((root / "fixtures" / "negative" / "contradictory-status-text.json").read_text(encoding="utf-8"))
    body = document["body"]
    assert CHECKER._schema_error(response_validator, body) is not None
    assert CHECKER._schema_error(response_validator, CHECKER._repaired_status_text_body(body)) is None
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    fixture = copied / "fixtures" / "negative" / "contradictory-status-text.json"
    tampered = json.loads(fixture.read_text(encoding="utf-8"))
    tampered["body"]["api_version"] = "o7.operator.v1"
    fixture.write_text(json.dumps(tampered, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "fixtures/negative/contradictory-status-text.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert "NEGATIVE CONTROL contradictory_status_text: WRONG_FAILURE" in stdout
    assert stderr.startswith("o7-operator-contract: FAIL:")


def test_decision_ids_are_unique() -> None:
    by_request, commands, _validator = _candidate_documents()
    conflict = copy.deepcopy(by_request["req_decisions"])
    assert CHECKER.classify_response(conflict, commands) is None
    for item in conflict["result"]["decisions"]:
        item["decision_id"] = "dec_refused"
    assert CHECKER.classify_response(conflict, commands) == "duplicate_decision_id"


def test_validate_response_rejects_duplicate_decision_ids_the_schema_accepts() -> None:
    by_request, commands, validator = _candidate_documents()
    conflict = copy.deepcopy(by_request["req_decisions"])
    assert CHECKER.validate_response(validator, conflict, commands) is None
    for item in conflict["result"]["decisions"]:
        item["decision_id"] = "dec_refused"
    assert CHECKER._schema_error(validator, conflict) is None
    assert CHECKER.validate_response(validator, conflict, commands) == "duplicate_decision_id"


def test_one_refusal_reason_per_decision() -> None:
    by_request, commands, validator = _candidate_documents()
    conflict = copy.deepcopy(by_request["req_decisions"])
    assert CHECKER.validate_response(validator, conflict, commands) is None
    conflict["result"]["refusals"].append(
        {
            "decision_id": "dec_refused",
            "reason_code": "other_reason",
            "visible": True,
        }
    )
    assert CHECKER._schema_error(validator, conflict) is None
    assert CHECKER.classify_response(conflict, commands) == "conflicting_refusal_reason"
    assert CHECKER.validate_response(validator, conflict, commands) == "conflicting_refusal_reason"


def test_response_required_fields_stay_pinned(tmp_path: Path) -> None:
    widened = tmp_path / "unversioned"
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    shutil.copytree(root, widened)
    schema_path = widened / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["required"] = [name for name in schema["required"] if name != "api_version"]
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(widened, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(widened)
    assert code == 1
    assert stdout == ""
    assert "response schema required fields drifted" in stderr


def test_refusal_cannot_name_a_recorded_decision() -> None:
    by_request, commands, validator = _candidate_documents()
    conflict = copy.deepcopy(by_request["req_decisions"])
    assert CHECKER.classify_response(conflict, commands) is None
    recorded = next(item for item in conflict["result"]["decisions"] if item["outcome"] == "recorded")
    conflict["result"]["refusals"].append(
        {
            "decision_id": recorded["decision_id"],
            "reason_code": recorded["reason_code"],
            "visible": True,
        }
    )
    assert CHECKER._schema_error(validator, conflict) is None
    assert CHECKER.classify_response(conflict, commands) == "refusal_on_recorded_decision"
    assert CHECKER.validate_response(validator, conflict, commands) == "refusal_on_recorded_decision"


def _drop_catalog_command(root: Path, name: str) -> None:
    """Remove one command from every place the checker compares to the catalog."""
    catalog_path = root / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["commands"] = [row for row in catalog["commands"] if row["name"] != name]
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    for relative in (
        "schemas/operator-command.schema.json",
        "schemas/operator-event.schema.json",
    ):
        path = root / relative
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema["properties"]["command"]["enum"] = [
            item for item in schema["properties"]["command"]["enum"] if item != name
        ]
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    response_path = root / "schemas" / "operator-response.schema.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    for clause in response["allOf"]:
        enum = clause.get("else", {}).get("properties", {}).get("command", {}).get("enum")
        if isinstance(enum, list) and name in enum:
            clause["else"]["properties"]["command"]["enum"] = [item for item in enum if item != name]
    response_path.write_text(json.dumps(response, indent=2) + "\n", encoding="utf-8")
    parity_path = root / "parity-matrix.json"
    parity = json.loads(parity_path.read_text(encoding="utf-8"))
    parity["rows"] = [row for row in parity["rows"] if row["command"] != name]
    parity_path.write_text(json.dumps(parity, indent=2) + "\n", encoding="utf-8")
    for relative, key in (
        ("fixtures/oracle/responses.json", "responses"),
        ("fixtures/oracle/commands.json", "commands"),
    ):
        path = root / relative
        document = json.loads(path.read_text(encoding="utf-8"))
        document[key] = [item for item in document[key] if item["command"] != name]
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    for relative in (
        "catalog.json",
        "schemas/operator-command.schema.json",
        "schemas/operator-event.schema.json",
        "schemas/operator-response.schema.json",
        "parity-matrix.json",
        "fixtures/oracle/responses.json",
        "fixtures/oracle/commands.json",
    ):
        _retarget(root, relative)


def test_catalog_journey_and_result_fields_are_pinned(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    swapped = tmp_path / "swapped"
    shutil.copytree(root, swapped)
    catalog_path = swapped / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    for row in catalog["commands"]:
        if row["name"] == "operator.positions.inspect":
            row["journey"] = "audit"
        elif row["name"] == "operator.audit.export":
            row["journey"] = "positions_collateral"
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    parity_path = swapped / "parity-matrix.json"
    parity = json.loads(parity_path.read_text(encoding="utf-8"))
    for row in parity["rows"]:
        if row["command"] == "operator.positions.inspect":
            row["journey"] = "audit"
        elif row["command"] == "operator.audit.export":
            row["journey"] = "positions_collateral"
    parity_path.write_text(json.dumps(parity, indent=2) + "\n", encoding="utf-8")
    _retarget(swapped, "catalog.json")
    _retarget(swapped, "parity-matrix.json")
    code, stdout, stderr = CHECKER.evaluate(swapped)
    assert code == 1
    assert stdout == ""
    assert "catalog journey drifted for operator.audit.export" in stderr
    trimmed = tmp_path / "trimmed"
    shutil.copytree(root, trimmed)
    catalog_path = trimmed / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    for row in catalog["commands"]:
        if row["name"] == "operator.evidence.inspect":
            row["required_result_fields"] = ["raw_separated_from_summary", "provenance"]
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    _retarget(trimmed, "catalog.json")
    code, stdout, stderr = CHECKER.evaluate(trimmed)
    assert code == 1
    assert stdout == ""
    assert "catalog result fields drifted for operator.evidence.inspect" in stderr
    schema_only = tmp_path / "schema"
    shutil.copytree(root, schema_only)
    schema_path = schema_only / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    for clause in schema["allOf"]:
        command = clause.get("if", {}).get("properties", {}).get("command", {}).get("const")
        then = clause.get("then")
        required = (
            then.get("properties", {}).get("result", {}).get("required") if isinstance(then, dict) else None
        )
        if command == "operator.evidence.inspect" and isinstance(required, list):
            clause["then"]["properties"]["result"]["required"] = ["raw_separated_from_summary", "provenance"]
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(schema_only, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(schema_only)
    assert code == 1
    assert stdout == ""
    assert "schema result fields drifted for operator.evidence.inspect" in stderr
    ok_only = tmp_path / "ok-only"
    shutil.copytree(root, ok_only)
    schema_path = ok_only / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    kept = []
    saw_ok = False
    for clause in schema["allOf"]:
        properties = clause.get("if", {}).get("properties", {})
        command = properties.get("command", {}).get("const") if isinstance(properties, dict) else None
        status = properties.get("status_code", {}).get("const") if isinstance(properties, dict) else None
        if command == "operator.evidence.inspect" and status in CHECKER.RESULT_STATUSES:
            if status == "ok" and not saw_ok:
                kept.append(clause)
                saw_ok = True
            continue
        kept.append(clause)
    assert saw_ok
    schema["allOf"] = kept
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(ok_only, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(ok_only)
    assert code == 1
    assert stdout == ""
    assert "schema result fields drifted for operator.evidence.inspect" in stderr
    unreachable = tmp_path / "unreachable"
    shutil.copytree(root, unreachable)
    schema_path = unreachable / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    patched = False
    for clause in schema["allOf"]:
        properties = clause.get("if", {}).get("properties", {})
        command = properties.get("command", {}).get("const") if isinstance(properties, dict) else None
        status = properties.get("status_code", {}).get("const") if isinstance(properties, dict) else None
        if command == "operator.evidence.inspect" and status == "refused":
            clause["if"]["required"] = ["command", "status_code", "missing_field"]
            patched = True
    assert patched
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(unreachable, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(unreachable)
    assert code == 1
    assert stdout == ""
    assert "schema result clause cannot apply for operator.evidence.inspect" in stderr
    impossible = tmp_path / "impossible-then"
    shutil.copytree(root, impossible)
    schema_path = impossible / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    patched = False
    for clause in schema["allOf"]:
        properties = clause.get("if", {}).get("properties", {})
        command = properties.get("command", {}).get("const") if isinstance(properties, dict) else None
        status = properties.get("status_code", {}).get("const") if isinstance(properties, dict) else None
        if command == "operator.evidence.inspect" and status == "refused":
            clause["then"]["required"] = ["missing_field"]
            patched = True
    assert patched
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(impossible, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(impossible)
    assert code == 1
    assert stdout == ""
    assert "schema result consequent cannot apply for operator.evidence.inspect" in stderr
    bounded = tmp_path / "max-properties"
    shutil.copytree(root, bounded)
    schema_path = bounded / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    patched = False
    for clause in schema["allOf"]:
        properties = clause.get("if", {}).get("properties", {})
        command = properties.get("command", {}).get("const") if isinstance(properties, dict) else None
        status = properties.get("status_code", {}).get("const") if isinstance(properties, dict) else None
        if command == "operator.evidence.inspect" and status == "refused":
            clause["then"]["properties"]["result"]["maxProperties"] = 0
            patched = True
    assert patched
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(bounded, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(bounded)
    assert code == 1
    assert stdout == ""
    assert "schema result consequent cannot apply for operator.evidence.inspect" in stderr
    contradicted = tmp_path / "halt"
    shutil.copytree(root, contradicted)
    schema_path = contradicted / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    patched = False
    for clause in schema["allOf"]:
        properties = clause.get("if", {}).get("properties", {})
        command = properties.get("command", {}).get("const") if isinstance(properties, dict) else None
        status = properties.get("status_code", {}).get("const") if isinstance(properties, dict) else None
        if command == "operator.interventions.request_stop" and status == "unavailable":
            clause["then"]["properties"]["result"]["properties"]["intervention"] = {"const": "halt"}
            patched = True
    assert patched
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(contradicted, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(contradicted)
    assert code == 1
    assert stdout == ""
    assert "schema result consequent cannot apply for operator.interventions.request_stop" in stderr


def test_direct_authorization_stays_false(tmp_path: Path) -> None:
    by_request, commands, _validator = _candidate_documents()
    profiles = copy.deepcopy(by_request["req_profiles"])
    assert profiles["result"]["bounded_direct_authorized"] is False
    assert CHECKER.classify_response(profiles, commands) is None
    profiles["result"]["bounded_direct_authorized"] = True
    assert CHECKER.classify_response(profiles, commands) == "direct_authorization"
    profiles["result"]["bounded_direct_authorized"] = False
    profiles["result"]["local_control_is_account_wide"] = True
    assert CHECKER.classify_response(profiles, commands) == "account_wide_local_control"
    exported = copy.deepcopy(by_request["req_audit"])
    exported["result"]["secrets_included"] = True
    assert CHECKER.classify_response(exported, commands) == "secrets_included"
    widened = tmp_path / "direct-authorization"
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    shutil.copytree(root, widened)
    schema_path = widened / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["result"]["properties"]["bounded_direct_authorized"] = {"type": "boolean"}
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(widened, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(widened)
    assert code == 1
    assert stdout == ""
    assert "bounded_direct_authorized is not limited to false" in stderr


def test_command_schema_rejects_authority_fields(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    for key, message in (
        ("arguments", "command schema accepts an authority field"),
        ("root", "command schema accepts an authority field"),
    ):
        widened = tmp_path / key
        shutil.copytree(root, widened)
        schema_path = widened / "schemas" / "operator-command.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if key == "arguments":
            schema["properties"]["arguments"]["additionalProperties"] = True
        else:
            schema["additionalProperties"] = True
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        _retarget(widened, "schemas/operator-command.schema.json")
        code, stdout, stderr = CHECKER.evaluate(widened)
        assert code == 1
        assert stdout == ""
        assert message in stderr


def test_positive_safety_consts_stay_true(tmp_path: Path) -> None:
    by_request, commands, _validator = _candidate_documents()
    evidence = copy.deepcopy(by_request["req_evidence"])
    assert evidence["result"]["raw_separated_from_summary"] is True
    assert CHECKER.classify_response(evidence, commands) is None
    evidence["result"]["raw_separated_from_summary"] = False
    assert CHECKER.classify_response(evidence, commands) == "raw_not_separated"
    profiles = copy.deepcopy(by_request["req_profiles"])
    profiles["result"]["surfaces_share_one_profile"] = False
    assert CHECKER.classify_response(profiles, commands) == "split_profile"
    stop = copy.deepcopy(by_request["req_stop"])
    stop["result"]["restart_requires_reconciliation"] = False
    assert CHECKER.classify_response(stop, commands) == "restart_without_reconciliation"
    widened = tmp_path / "raw-combined"
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    shutil.copytree(root, widened)
    schema_path = widened / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["result"]["properties"]["raw_separated_from_summary"] = {"type": "boolean"}
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(widened, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(widened)
    assert code == 1
    assert stdout == ""
    assert "raw_separated_from_summary is not limited to true" in stderr


def test_result_schema_rejects_unmodeled_authority(tmp_path: Path) -> None:
    widened = tmp_path / "open-result"
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    shutil.copytree(root, widened)
    schema_path = widened / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["result"]["additionalProperties"] = True
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(widened, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(widened)
    assert code == 1
    assert stdout == ""
    assert "result schema accepts an authority field" in stderr


def test_profile_event_and_confirmation_stay_closed(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    cases = (
        (
            "profile",
            "schemas/operator-response.schema.json",
            ("properties", "profile", "additionalProperties"),
            True,
            "profile schema accepts an authority field",
        ),
        (
            "confirmation",
            "schemas/operator-command.schema.json",
            ("required",),
            None,
            "command schema accepts an authority field",
        ),
        (
            "event",
            "schemas/operator-event.schema.json",
            ("additionalProperties",),
            True,
            "event schema accepts an authority field",
        ),
    )
    for name, relative, path, value, message in cases:
        widened = tmp_path / name
        shutil.copytree(root, widened)
        schema_path = widened / relative
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if name == "confirmation":
            schema["required"] = [item for item in schema["required"] if item != "confirmation"]
        else:
            cursor = schema
            for key in path[:-1]:
                cursor = cursor[key]
            cursor[path[-1]] = value
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        _retarget(widened, relative)
        code, stdout, stderr = CHECKER.evaluate(widened)
        assert code == 1, name
        assert stdout == ""
        assert message in stderr, name


def test_embedded_error_and_api_version_stay_pinned(tmp_path: Path) -> None:
    by_request, commands, validator = _candidate_documents()
    decisions = copy.deepcopy(by_request["req_decisions"])
    assert CHECKER.validate_response(validator, decisions, commands) is None
    decisions["api_version"] = "o7.operator.v1"
    assert CHECKER.validate_response(validator, decisions, commands) is not None
    decisions["api_version"] = CHECKER.API_VERSION
    decisions["errors"][0] = {**decisions["errors"][0], "live_authorized": True}
    assert CHECKER._schema_error(validator, decisions) is not None
    assert CHECKER.validate_response(validator, decisions, commands) is not None
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    cases = (
        (
            "open-error",
            ("properties", "errors", "items", "additionalProperties"),
            True,
            "response error schema accepts an authority field",
        ),
        (
            "named-error-authority",
            ("properties", "errors", "items", "properties", "live_authorized"),
            {"const": True},
            "response error schema accepts an authority field",
        ),
        (
            "free-version",
            ("properties", "api_version"),
            {"type": "string"},
            "response api_version is not o7.operator.v0",
        ),
    )
    for name, path, value, message in cases:
        widened = tmp_path / name
        shutil.copytree(root, widened)
        schema_path = widened / "schemas" / "operator-response.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        cursor = schema
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        _retarget(widened, "schemas/operator-response.schema.json")
        code, stdout, stderr = CHECKER.evaluate(widened)
        assert code == 1, name
        assert stdout == ""
        assert message in stderr, name


def test_presentation_and_wire_versions_stay_pinned(tmp_path: Path) -> None:
    by_request, commands, validator = _candidate_documents()
    positions = copy.deepcopy(by_request["req_positions"])
    assert CHECKER.validate_response(validator, positions, commands) is None
    positions["presentation"] = {**positions["presentation"], "live_authorized": True}
    assert CHECKER._schema_error(validator, positions) is not None
    assert CHECKER.validate_response(validator, positions, commands) is not None
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    command_schema = json.loads((root / "schemas" / "operator-command.schema.json").read_text(encoding="utf-8"))
    command_validator = CHECKER._validator(command_schema)
    read = CHECKER._intervention_probe(
        "operator.positions.inspect", confirmation="not_required", arguments={}
    )
    assert CHECKER._schema_error(command_validator, read) is None
    assert CHECKER._schema_error(command_validator, {**read, "api_version": "o7.operator.v1"}) is not None
    event_schema = json.loads((root / "schemas" / "operator-event.schema.json").read_text(encoding="utf-8"))
    event_validator = CHECKER._validator(event_schema)
    event = CHECKER._event_probe(
        "intervention_recorded", "operator.interventions.request_stop", "ok", "visible"
    )
    assert CHECKER._schema_error(event_validator, event) is None
    assert CHECKER._schema_error(event_validator, {**event, "api_version": "o7.operator.v1"}) is not None
    cases = (
        (
            "open-presentation",
            "schemas/operator-response.schema.json",
            ("properties", "presentation", "additionalProperties"),
            True,
            "presentation schema accepts an authority field",
        ),
        (
            "named-presentation",
            "schemas/operator-response.schema.json",
            ("properties", "presentation", "properties", "live_authorized"),
            {"const": True},
            "presentation schema accepts an authority field",
        ),
        (
            "command-version",
            "schemas/operator-command.schema.json",
            ("properties", "api_version"),
            {"type": "string"},
            "command api_version is not o7.operator.v0",
        ),
        (
            "event-version",
            "schemas/operator-event.schema.json",
            ("properties", "api_version"),
            {"type": "string"},
            "event api_version is not o7.operator.v0",
        ),
    )
    for name, relative, path, value, message in cases:
        widened = tmp_path / name
        shutil.copytree(root, widened)
        schema_path = widened / relative
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        cursor = schema
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        _retarget(widened, relative)
        code, stdout, stderr = CHECKER.evaluate(widened)
        assert code == 1, name
        assert stdout == ""
        assert message in stderr, name


def test_visibility_stays_closed(tmp_path: Path) -> None:
    by_request, commands, validator = _candidate_documents()
    positions = copy.deepcopy(by_request["req_positions"])
    assert CHECKER.validate_response(validator, positions, commands) is None
    positions["visibility"] = {**positions["visibility"], "live_authorized": True}
    assert CHECKER._schema_error(validator, positions) is not None
    assert CHECKER.validate_response(validator, positions, commands) is not None
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    cases = (
        (
            "open-visibility",
            ("properties", "visibility", "additionalProperties"),
            True,
        ),
        (
            "named-visibility",
            ("properties", "visibility", "properties", "live_authorized"),
            {"const": True},
        ),
    )
    for name, path, value in cases:
        widened = tmp_path / name
        shutil.copytree(root, widened)
        schema_path = widened / "schemas" / "operator-response.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        cursor = schema
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        _retarget(widened, "schemas/operator-response.schema.json")
        code, stdout, stderr = CHECKER.evaluate(widened)
        assert code == 1, name
        assert stdout == ""
        assert "visibility schema accepts an authority field" in stderr, name


def test_unanchored_result_pattern_admits_a_containing_literal() -> None:
    schema = {
        "properties": {
            "result": {
                "properties": {"source_id": {"type": "string", "pattern": "source"}},
            }
        },
        "allOf": [
            {
                "if": {
                    "required": ["command", "status_code"],
                    "properties": {
                        "command": {"const": "operator.evidence.inspect"},
                        "status_code": {"const": "refused"},
                    },
                },
                "then": {
                    "properties": {
                        "result": {
                            "required": ["source_id"],
                            "properties": {"source_id": {"const": "fixture_source_1"}},
                        }
                    }
                },
            }
        ],
    }
    fields = CHECKER._schema_command_result_fields(schema)
    assert fields["operator.evidence.inspect"]["refused"] == ["source_id"]
    schema["allOf"][0]["then"]["properties"]["result"]["properties"]["source_id"] = {"const": "fixture"}
    try:
        CHECKER._schema_command_result_fields(schema)
    except CHECKER.ContractError as exc:
        assert "schema result consequent cannot apply" in str(exc)
    else:
        raise AssertionError("a literal outside the base pattern was accepted")


def test_schema_annotations_do_not_block_result_clauses(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    annotated = tmp_path / "annotated"
    shutil.copytree(root, annotated)
    schema_path = annotated / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    for clause in schema["allOf"]:
        properties = clause.get("if", {}).get("properties", {}) if isinstance(clause.get("if"), dict) else {}
        status = properties.get("status_code")
        if (
            isinstance(properties.get("command"), dict)
            and properties["command"].get("const") == "operator.evidence.inspect"
            and isinstance(status, dict)
            and status.get("const") == "refused"
        ):
            clause["if"]["description"] = "Evidence refusal"
            properties["command"]["$comment"] = "annotation only"
            status["title"] = "refused"
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    fields = CHECKER._schema_command_result_fields(schema)
    assert "refused" in fields["operator.evidence.inspect"]


def test_reconciled_result_cannot_carry_unknown_orders() -> None:
    by_request, commands, validator = _candidate_documents()
    conflict = copy.deepcopy(by_request["req_recovery"])
    assert conflict["result"]["reconciliation"] == "reconciled"
    assert conflict["result"]["unknown_orders"] == 0
    assert CHECKER.classify_response(conflict, commands) is None
    conflict["result"]["unknown_orders"] = 1
    conflict["visibility"]["reconciliation"] = "unreconciled_visible"
    conflict["status_code"] = "stale_or_unreconciled"
    conflict["result"]["restart_allowed"] = False
    conflict["errors"] = [
        {
            "code": "stale_or_unreconciled",
            "status_code": "stale_or_unreconciled",
            "message": "Unknown orders remain.",
            "recovery": "Reconcile before restart.",
            "color_only": False,
        }
    ]
    conflict["presentation"]["exit_code"] = 3
    conflict["presentation"]["text_status"] = "stale_or_unreconciled"
    conflict["presentation"]["rendered"] = "status=stale_or_unreconciled recovery"
    assert CHECKER.classify_response(conflict, commands) == "reconciled_with_unknown_orders"
    assert CHECKER._schema_error(validator, conflict) is not None


def test_each_intervention_command_requires_confirmation(tmp_path: Path) -> None:
    copied = tmp_path / "O7"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", copied)
    schema_path = copied / "schemas" / "operator-command.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["allOf"][0]["then"]["properties"].pop("confirmation")
    schema["allOf"].append(
        {
            "if": {
                "required": ["command"],
                "properties": {"command": {"const": "operator.interventions.request_stop"}},
            },
            "then": {"properties": {"confirmation": {"const": "explicit"}}},
        }
    )
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "schemas/operator-command.schema.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "unconfirmed intervention schema accepts operator.interventions.request_reconciliation" in stderr
    widened = tmp_path / "account-scope"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", widened)
    schema_path = widened / "schemas" / "operator-command.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["arguments"]["properties"]["scope"]["enum"] = ["operator_session", "account"]
    schema["allOf"][0]["then"]["properties"]["arguments"]["properties"]["scope"] = {
        "enum": ["operator_session", "account"]
    }
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(widened, "schemas/operator-command.schema.json")
    code, stdout, stderr = CHECKER.evaluate(widened)
    assert code == 1
    assert stdout == ""
    assert "intervention scope is not limited to operator_session" in stderr


def test_catalog_command_set_is_pinned(tmp_path: Path) -> None:
    copied = tmp_path / "O7"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", copied)
    _drop_catalog_command(copied, "operator.interventions.request_reconciliation")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "catalog command set drifted" in stderr


def test_event_schema_rejects_an_unconstrained_intervention(tmp_path: Path) -> None:
    copied = tmp_path / "O7"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", copied)
    schema_path = copied / "schemas" / "operator-event.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["allOf"] = [
        clause
        for clause in schema["allOf"]
        if clause.get("if", {}).get("properties", {}).get("event_type", {}).get("const") != "intervention_recorded"
    ]
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "schemas/operator-event.schema.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "event schema does not constrain intervention_recorded" in stderr
    blocked = tmp_path / "blocked-status"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", blocked)
    schema_path = blocked / "schemas" / "operator-event.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["allOf"].append(
        {
            "if": {
                "required": ["event_type", "status_code"],
                "properties": {
                    "event_type": {"const": "uncertainty_visible"},
                    "status_code": {"const": "refused"},
                },
            },
            "then": False,
        }
    )
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(blocked, "schemas/operator-event.schema.json")
    code, stdout, stderr = CHECKER.evaluate(blocked)
    assert code == 1
    assert stdout == ""
    assert "event schema rejects uncertainty_visible refused" in stderr


def test_refused_intervention_cannot_be_recorded(tmp_path: Path) -> None:
    by_request, commands, validator = _candidate_documents()
    for request_id in ("req_stop", "req_reconcile", "req_revoke"):
        refused = copy.deepcopy(by_request[request_id])
        refused["status_code"] = "refused"
        assert refused["result"]["recorded"] is True
        assert CHECKER.classify_response(refused, commands) == "recorded_intervention_refused"
        assert CHECKER._schema_error(validator, refused) is not None
    copied = tmp_path / "O7"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", copied)
    schema_path = copied / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    kept = []
    for clause in schema["allOf"]:
        properties = clause.get("if", {}).get("properties", {}) if isinstance(clause.get("if"), dict) else {}
        status = properties.get("status_code") if isinstance(properties, dict) else None
        command = properties.get("command") if isinstance(properties, dict) else None
        status_const = status.get("const") if isinstance(status, dict) else None
        command_const = command.get("const") if isinstance(command, dict) else None
        if status_const == "refused" and command_const is None and isinstance(command, dict):
            continue
        kept.append(clause)
    assert len(kept) == len(schema["allOf"]) - 1
    schema["allOf"] = kept
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "refused intervention can still be recorded" in stderr
    unreachable = tmp_path / "unreachable-refusal"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", unreachable)
    schema_path = unreachable / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    patched = False
    for clause in schema["allOf"]:
        condition = clause.get("if")
        properties = condition.get("properties") if isinstance(condition, dict) else None
        command = properties.get("command") if isinstance(properties, dict) else None
        status = properties.get("status_code") if isinstance(properties, dict) else None
        if (
            isinstance(command, dict)
            and "enum" in command
            and isinstance(status, dict)
            and status.get("const") == "refused"
        ):
            condition["maxProperties"] = 0
            condition["required"] = ["command", "status_code", "absent_field"]
            patched = True
    assert patched
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(unreachable, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(unreachable)
    assert code == 1
    assert stdout == ""
    assert "refused intervention clause cannot apply" in stderr


def test_error_schema_binds_every_code_to_its_status(tmp_path: Path) -> None:
    copied = tmp_path / "O7"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", copied)
    schema_path = copied / "schemas" / "operator-error.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["allOf"] = [
        clause
        for clause in schema["allOf"]
        if clause.get("if", {}).get("properties", {}).get("code", {}).get("const") != "usage"
    ]
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "schemas/operator-error.schema.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "error schema does not bind usage" in stderr


def test_uncertainty_visible_event_keeps_compound_status() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    event_schema = json.loads((root / "schemas" / "operator-event.schema.json").read_text(encoding="utf-8"))
    event_validator = CHECKER._validator(event_schema)
    events = json.loads((root / "fixtures" / "oracle" / "events.json").read_text(encoding="utf-8"))
    base = next(event for event in events["events"] if event["event_type"] == "uncertainty_visible")
    for status in ("ok", "refused", "stale_or_unreconciled", "unavailable"):
        event = copy.deepcopy(base)
        event["status_code"] = status
        assert CHECKER._schema_error(event_validator, event) is None
        assert CHECKER._event_error(event) is None
    usage = copy.deepcopy(base)
    usage["status_code"] = "usage"
    assert CHECKER._schema_error(event_validator, usage) is not None
    assert CHECKER._event_error(usage) == "event status does not match the event type"


def test_unresolved_decision_ids_are_pinned(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    fixture = copied / "unresolved-decisions.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    document["decisions"] = [item for item in document["decisions"] if item["id"] != "tui-authorization"]
    fixture.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "unresolved-decisions.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "unresolved decision ids drifted" in stderr
    duplicated = tmp_path / "duplicate"
    shutil.copytree(root, duplicated)
    duplicate_fixture = duplicated / "unresolved-decisions.json"
    duplicate = json.loads(duplicate_fixture.read_text(encoding="utf-8"))
    duplicate["decisions"].append(copy.deepcopy(duplicate["decisions"][0]))
    duplicate_fixture.write_text(json.dumps(duplicate, indent=2) + "\n", encoding="utf-8")
    _retarget(duplicated, "unresolved-decisions.json")
    code, stdout, stderr = CHECKER.evaluate(duplicated)
    assert code == 1
    assert stdout == ""
    assert "unresolved decision ids drifted" in stderr


def test_rendered_status_is_a_delimited_field() -> None:
    by_request, commands, validator = _candidate_documents()
    refused = copy.deepcopy(by_request["req_decisions"])
    assert refused["status_code"] == "refused"
    refused["presentation"]["text_status"] = "refused"
    refused["presentation"]["rendered"] = "status=not_refused"
    assert CHECKER.classify_response(refused, commands) == "contradictory_status_text"
    assert CHECKER._schema_error(validator, refused) is not None
    competing = copy.deepcopy(by_request["req_decisions"])
    competing["presentation"]["rendered"] = "status=refused status=ok"
    assert CHECKER.classify_response(competing, commands) == "contradictory_status_text"
    assert CHECKER._schema_error(validator, competing) is not None
    for extra in ("status=refused", "status=bogus", "status=not-refused"):
        repeated = copy.deepcopy(by_request["req_decisions"])
        repeated["presentation"]["rendered"] = f"status=refused {extra}"
        assert CHECKER.classify_response(repeated, commands) == "contradictory_status_text"
        assert CHECKER._schema_error(validator, repeated) is not None


def _usage_response(source: dict, command: str | None) -> dict:
    body = copy.deepcopy(source)
    if command is None:
        body.pop("command", None)
    else:
        body["command"] = command
    body["request_id"] = "req_usage_rejected"
    body["status_code"] = "usage"
    body["authority_effect"] = "none"
    body["result"] = {}
    body["errors"] = [
        {
            "code": "usage",
            "status_code": "usage",
            "message": "command was rejected",
            "recovery": "use a catalog command",
            "color_only": False,
        }
    ]
    body["presentation"] = {
        "exit_code": 1,
        "machine_readable": True,
        "ansi_in_machine_output": False,
        "color_only_signal": False,
        "text_status": "usage",
        "rendered": "status=usage rejected command",
    }
    return body


def test_usage_response_can_name_a_rejected_command() -> None:
    by_request, commands, validator = _candidate_documents()
    unknown = _usage_response(by_request["req_health"], "operator.orders.submit")
    missing = _usage_response(by_request["req_health"], None)
    assert CHECKER._schema_error(validator, unknown) is None
    assert CHECKER.classify_response(unknown, commands) is None
    assert CHECKER._schema_error(validator, missing) is None
    assert CHECKER.classify_response(missing, commands) is None
    invented = copy.deepcopy(by_request["req_health"])
    invented["command"] = "operator.orders.submit"
    assert CHECKER._schema_error(validator, invented) is not None
    assert CHECKER.classify_response(invented, commands) == "privileged_order_bypass"


def test_export_recorded_event_keeps_compound_status() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    event_schema = json.loads((root / "schemas" / "operator-event.schema.json").read_text(encoding="utf-8"))
    event_validator = CHECKER._validator(event_schema)
    events = json.loads((root / "fixtures" / "oracle" / "events.json").read_text(encoding="utf-8"))
    base = next(event for event in events["events"] if event["event_type"] == "export_recorded")
    for status in ("ok", "stale_or_unreconciled", "unavailable"):
        event = copy.deepcopy(base)
        event["status_code"] = status
        assert CHECKER._schema_error(event_validator, event) is None
        assert CHECKER._event_error(event) is None
    refused = copy.deepcopy(base)
    refused["status_code"] = "refused"
    assert CHECKER._schema_error(event_validator, refused) is not None
    assert CHECKER._event_error(refused) == "event status does not match the event type"


def test_evidence_viewed_event_keeps_compound_status() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    event_schema = json.loads((root / "schemas" / "operator-event.schema.json").read_text(encoding="utf-8"))
    event_validator = CHECKER._validator(event_schema)
    events = json.loads((root / "fixtures" / "oracle" / "events.json").read_text(encoding="utf-8"))
    base = next(event for event in events["events"] if event["event_type"] == "evidence_viewed")
    for status in ("ok", "stale_or_unreconciled", "unavailable"):
        event = copy.deepcopy(base)
        event["status_code"] = status
        assert CHECKER._schema_error(event_validator, event) is None
        assert CHECKER._event_error(event) is None
    refused = copy.deepcopy(base)
    refused["status_code"] = "refused"
    assert CHECKER._schema_error(event_validator, refused) is not None
    assert CHECKER._event_error(refused) == "event status does not match the event type"


def test_refusal_and_unreconciled_events_match_the_originating_command() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    event_schema = json.loads((root / "schemas" / "operator-event.schema.json").read_text(encoding="utf-8"))
    event_validator = CHECKER._validator(event_schema)
    events = json.loads((root / "fixtures" / "oracle" / "events.json").read_text(encoding="utf-8"))
    for event_type in ("refusal_visible", "unreconciled_visible"):
        base = next(event for event in events["events"] if event["event_type"] == event_type)
        positions = copy.deepcopy(base)
        positions["command"] = "operator.positions.inspect"
        positions["request_id"] = "req_positions"
        assert CHECKER._schema_error(event_validator, positions) is None
        assert CHECKER._event_error(positions) is None


def test_uncertainty_visible_event_matches_any_catalog_command() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    event_schema = json.loads((root / "schemas" / "operator-event.schema.json").read_text(encoding="utf-8"))
    event_validator = CHECKER._validator(event_schema)
    events = json.loads((root / "fixtures" / "oracle" / "events.json").read_text(encoding="utf-8"))
    base = next(event for event in events["events"] if event["event_type"] == "uncertainty_visible")
    positions = copy.deepcopy(base)
    positions["command"] = "operator.positions.inspect"
    positions["request_id"] = "req_positions"
    assert CHECKER._schema_error(event_validator, positions) is None
    assert CHECKER._event_error(positions) is None


def test_intervention_recorded_event_keeps_compound_status() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    event_schema = json.loads((root / "schemas" / "operator-event.schema.json").read_text(encoding="utf-8"))
    event_validator = CHECKER._validator(event_schema)
    events = json.loads((root / "fixtures" / "oracle" / "events.json").read_text(encoding="utf-8"))
    base = next(event for event in events["events"] if event["event_type"] == "intervention_recorded")
    for status in ("ok", "stale_or_unreconciled", "unavailable"):
        event = copy.deepcopy(base)
        event["status_code"] = status
        assert CHECKER._schema_error(event_validator, event) is None
        assert CHECKER._event_error(event) is None
    refused = copy.deepcopy(base)
    refused["status_code"] = "refused"
    assert CHECKER._schema_error(event_validator, refused) is not None
    assert CHECKER._event_error(refused) == "event status does not match the event type"


def test_parity_pair_ids_match_their_commands(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    responses = json.loads((copied / "fixtures" / "oracle" / "responses.json").read_text(encoding="utf-8"))
    health = next(doc for doc in responses["responses"] if doc["request_id"] == "req_health")
    fixture = copied / "fixtures" / "oracle" / "pairs.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    pair = next(item for item in document["pairs"] if item["pair_id"] == "positions.inspect.parity")
    gui = copy.deepcopy(health)
    cli = copy.deepcopy(health)
    gui["surface"] = "gui"
    cli["surface"] = "cli"
    gui["presentation"]["rendered"] = "status=ok gui health"
    cli["presentation"]["rendered"] = "status=ok cli health"
    pair["gui"] = gui
    pair["cli"] = cli
    fixture.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "fixtures/oracle/pairs.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "oracle pair command drifted for positions.inspect.parity" in stderr


def test_stale_visible_event_matches_any_catalog_command() -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    event_schema = json.loads((root / "schemas" / "operator-event.schema.json").read_text(encoding="utf-8"))
    event_validator = CHECKER._validator(event_schema)
    events = json.loads((root / "fixtures" / "oracle" / "events.json").read_text(encoding="utf-8"))
    stale = next(event for event in events["events"] if event["event_type"] == "stale_visible")
    positions = copy.deepcopy(stale)
    positions["command"] = "operator.positions.inspect"
    positions["request_id"] = "req_positions"
    assert CHECKER._schema_error(event_validator, positions) is None
    assert CHECKER._event_error(positions) is None


def test_privileged_path_fails_on_order_path_alone(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    schema_path = copied / "schemas" / "operator-response.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["order_path"] = {"enum": ["none", "surface_private"]}
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "schemas/operator-response.schema.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert "NEGATIVE CONTROL privileged_order_bypass: WRONG_FAILURE" in stdout
    assert stderr.startswith("o7-operator-contract: FAIL:")


def test_parity_rows_keep_their_journey_and_display_fields(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    fixture = copied / "parity-matrix.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    for row in document["rows"]:
        if row["command"] == "operator.interventions.request_stop":
            row["must_show"] = ["intervention"]
    fixture.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "parity-matrix.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "parity must_show drifted for operator.interventions.request_stop" in stderr
    swapped = tmp_path / "swapped"
    shutil.copytree(root, swapped)
    matrix = swapped / "parity-matrix.json"
    document = json.loads(matrix.read_text(encoding="utf-8"))
    for row in document["rows"]:
        if row["command"] == "operator.positions.inspect":
            row["journey"] = "audit"
        elif row["command"] == "operator.audit.export":
            row["journey"] = "positions_collateral"
    matrix.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _retarget(swapped, "parity-matrix.json")
    code, stdout, stderr = CHECKER.evaluate(swapped)
    assert code == 1
    assert stdout == ""
    assert "parity journey drifted for operator.positions.inspect" in stderr


def test_visible_uncertainty_requires_uncertainty_evidence() -> None:
    by_request, commands, response_validator = _candidate_documents()
    invented = copy.deepcopy(by_request["req_health"])
    invented["visibility"]["uncertainty"] = "visible"
    assert invented["status_code"] == "ok"
    assert "items" not in invented["result"]
    assert CHECKER.classify_response(invented, commands) == "hidden_uncertainty"
    assert CHECKER._schema_error(response_validator, invented) is not None


def test_parity_pairs_use_the_oracle_response_checks(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    fixture = copied / "fixtures" / "oracle" / "pairs.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    for pair in document["pairs"]:
        pair["gui"]["profile"]["profile_class"] = "C"
        pair["cli"]["profile"]["profile_class"] = "C"
    fixture.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "fixtures/oracle/pairs.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "illustrative oracle must stay on profile A" in stderr


def test_visible_refusal_requires_refusal_evidence() -> None:
    by_request, commands, response_validator = _candidate_documents()
    invented = copy.deepcopy(by_request["req_health"])
    invented["visibility"]["refusal"] = "visible"
    assert invented["status_code"] == "ok"
    assert invented["errors"] == []
    assert "refusals" not in invented["result"]
    assert CHECKER.classify_response(invented, commands) == "hidden_refusal"
    assert CHECKER._schema_error(response_validator, invented) is not None


def test_events_match_their_originating_response(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    fixture = copied / "fixtures" / "oracle" / "events.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    for event in document["events"]:
        if event["event_type"] == "refusal_visible":
            event["request_id"] = "req_health"
    fixture.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "fixtures/oracle/events.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "event command does not match the response" in stderr
    conditioned = tmp_path / "conditioned"
    shutil.copytree(root, conditioned)
    stale_fixture = conditioned / "fixtures" / "oracle" / "events.json"
    stale = json.loads(stale_fixture.read_text(encoding="utf-8"))
    for event in stale["events"]:
        if event["event_type"] == "stale_visible":
            event["request_id"] = "req_recovery_open"
            event["command"] = "operator.recovery.inspect"
    stale_fixture.write_text(json.dumps(stale, indent=2) + "\n", encoding="utf-8")
    _retarget(conditioned, "fixtures/oracle/events.json")
    code, stdout, stderr = CHECKER.evaluate(conditioned)
    assert code == 1
    assert stdout == ""
    assert "event condition does not match the response" in stderr


def test_oracle_events_cover_every_event_type(tmp_path: Path) -> None:
    root = ROOT / "docs" / "replacement" / "outcomes" / "O7"
    copied = tmp_path / "O7"
    shutil.copytree(root, copied)
    fixture = copied / "fixtures" / "oracle" / "events.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    document["events"] = [
        event
        for event in document["events"]
        if event["event_type"] not in {"intervention_recorded", "export_recorded", "unreconciled_visible"}
    ]
    assert len(document["events"]) >= 4
    fixture.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _retarget(copied, "fixtures/oracle/events.json")
    code, stdout, stderr = CHECKER.evaluate(copied)
    assert code == 1
    assert stdout == ""
    assert "oracle events do not cover the event types" in stderr


def test_visible_stale_requires_stale_evidence() -> None:
    by_request, commands, response_validator = _candidate_documents()
    invented = copy.deepcopy(by_request["req_health"])
    invented["visibility"]["stale_data"] = "visible"
    assert invented["result"]["stale"] is False
    assert invented["result"]["dependencies"][0]["status"] == "ok"
    assert invented["status_code"] == "ok"
    assert invented["result"]["watch_only"] is False
    assert CHECKER.classify_response(invented, commands) == "hidden_stale_data"
    assert CHECKER._schema_error(response_validator, invented) is not None


def test_unreconciled_visibility_matches_the_result() -> None:
    by_request, commands, response_validator = _candidate_documents()
    invented = copy.deepcopy(by_request["req_recovery"])
    invented["visibility"]["reconciliation"] = "unreconciled_visible"
    assert invented["result"]["reconciliation"] == "reconciled"
    assert invented["result"]["unknown_orders"] == 0
    assert invented["result"]["restart_allowed"] is True
    assert CHECKER.classify_response(invented, commands) == "hidden_unreconciled"
    assert CHECKER._schema_error(response_validator, invented) is not None
    hidden = copy.deepcopy(by_request["req_recovery_open"])
    hidden["visibility"]["reconciliation"] = "reconciled"
    hidden["status_code"] = "ok"
    hidden["errors"] = []
    hidden["result"]["restart_allowed"] = True
    hidden["presentation"]["exit_code"] = 0
    hidden["presentation"]["text_status"] = "ok"
    hidden["presentation"]["rendered"] = "status=ok reconciled"
    assert CHECKER.classify_response(hidden, commands) == "hidden_unreconciled"


def test_missing_catalog_field_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "O7"
    shutil.copytree(ROOT / "docs" / "replacement" / "outcomes" / "O7", root)
    catalog_path = root / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["commands"][0].pop("required_result_fields")
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    _retarget(root, "catalog.json")
    code, stdout, stderr = CHECKER.evaluate(root)
    assert code == 1
    assert stdout == ""
    assert stderr.startswith("o7-operator-contract: FAIL:")


def test_checker_source_has_no_network_client() -> None:
    text = CHECKER_PATH.read_text(encoding="utf-8")
    for banned in ("import socket", "import httpx", "import urllib", "import requests", "import subprocess"):
        assert banned not in text
