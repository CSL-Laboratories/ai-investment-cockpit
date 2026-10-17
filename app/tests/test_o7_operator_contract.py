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
