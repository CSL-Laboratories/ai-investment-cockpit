"""CSL-214 candidate O2 contract. Provider-free. No network, credentials, or live venue.

Exit 0 of the candidate command is not an empirical PASS. The warnings below are the
designed negative-control failures and must appear in CI output.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
O2 = ROOT / "docs" / "replacement" / "outcomes" / "O2"
VALIDATOR = O2 / "validate_o2.py"


class O2NegativeControlWarning(UserWarning):
    """A frozen negative control failed as designed."""


def _load_validator():
    spec = importlib.util.spec_from_file_location("o2_validate", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_o2_candidate_replays_and_negative_controls_fail_as_designed() -> None:
    module = _load_validator()
    lines = module.validate_candidate(O2)
    assert lines[0] == "O2 candidate corpus: ok"
    assert lines[1] == "O2 maturity: researched"
    failures = [line for line in lines if line.startswith("O2 NEGATIVE CONTROL FAIL AS DESIGNED:")]
    assert failures == [
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: future_data_leak codes=future_data_leak",
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: raw_not_reconstructable codes=raw_not_reconstructable",
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: lost_resolution_semantics codes=lost_resolution_semantics",
        "O2 NEGATIVE CONTROL FAIL AS DESIGNED: lost_corporate_action_semantics codes=lost_corporate_action_semantics",
    ]
    for line in failures:
        warnings.warn(line, O2NegativeControlWarning, stacklevel=1)

    completed = subprocess.run(
        (sys.executable, "-X", "utf8", "-B", str(VALIDATOR)),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for line in failures:
        assert line in completed.stdout
    assert "contract_frozen" in completed.stdout
    assert "Not claimed" in completed.stdout


def test_o2_validator_does_not_import_providers() -> None:
    text = VALIDATOR.read_text(encoding="utf-8")
    for banned in ("urllib", "httpx", "requests", "socket", "websockets", "boto3", "keyring"):
        assert f"import {banned}" not in text
        assert f"from {banned}" not in text


def test_accepted_csl212_documents_and_o2_maturity_stay_researched() -> None:
    program = json.loads((ROOT / "docs" / "replacement" / "program.json").read_text(encoding="utf-8"))
    receipt = program["authority"]["acceptance_receipt"]["accepted_contracts"]
    for path, digest in receipt.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
    outcome = next(item for item in program["outcomes"] if item["key"] == "O2")
    assert outcome["linear_issue"] == "CSL-214"
    assert outcome["evidence"]["claimed"] == "researched"
    assert "contract_frozen" in outcome["evidence"]["not_claimed"]
    assert outcome["completion_gate_status"] == "pending_outcome_artifact"
