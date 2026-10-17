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
    assert CHECKER.classify_response(stop, commands) == "recorded_stop_not_persistent"
    assert CHECKER._schema_error(response_validator, stop) is not None
    unrecorded = copy.deepcopy(by_request["req_stop"])
    unrecorded["result"]["recorded"] = False
    unrecorded["result"]["survives_restart"] = False
    assert CHECKER.classify_response(unrecorded, commands) is None
    assert CHECKER._schema_error(response_validator, unrecorded) is None
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
