"""Provider-free checks for the CSL-219 O7 candidate.

The exact candidate command is ``python -X utf8 -B tools/check_o7_operator_contract.py``.
Negative controls must fail as designed. This module does not open a socket, read
credentials, or call a provider. It does not claim qualification.
"""

from __future__ import annotations

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


def test_checker_source_has_no_network_client() -> None:
    text = CHECKER_PATH.read_text(encoding="utf-8")
    for banned in ("import socket", "import httpx", "import urllib", "import requests", "import subprocess"):
        assert banned not in text
