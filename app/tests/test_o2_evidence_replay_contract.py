"""CSL-214 candidate O2 contract. Provider-free. No network, credentials, or live venue.

Exit 0 of the candidate command is not an empirical PASS. The warnings below are the
designed negative-control failures and must appear in CI output.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

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


def test_swapped_event_identity_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    observations = json.loads((copy / "fixtures" / "observations.json").read_text(encoding="utf-8"))
    swapped = next(item for item in observations["observations"] if item["observation_id"] == "obs-ev-void")
    swapped["identity_id"] = "idn-ev-b1"
    (copy / "fixtures" / "observations.json").write_text(
        json.dumps(observations, indent=2) + "\n", encoding="utf-8"
    )
    manifest = json.loads((copy / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {
        rel: hashlib.sha256((copy / rel).read_bytes()).hexdigest() for rel in module.EXPECTED_FILES
    }
    (copy / "fixtures" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(module.ContractError, match="identity"):
        module.validate_candidate(copy)


def test_same_key_independent_splits_are_both_required_and_rejected() -> None:
    module = _load_validator()
    documents = {rel: json.loads((O2 / rel).read_text(encoding="utf-8")) for rel in module.SCHEMA_FOR}
    bundle = module.Bundle(documents)
    extra = json.loads(json.dumps(bundle.records["raw-eq-split"]))
    extra["raw_record_id"] = "raw-eq-split-other"
    extra["source_record_key"] = "split-other"
    extra["event_time"] = "2024-09-01T15:00:00Z"
    extra["accepted_at"] = "2024-09-01T15:00:00Z"
    extra["supersedes"] = None
    extra["payload"] = dict(extra["payload"])
    extra["payload"]["ratio_numerator"] = 4
    extra["payload"]["ex_date"] = "2024-09-03"
    extra["raw_sha256"] = module.payload_sha256(extra["payload"])
    bundle.records[extra["raw_record_id"]] = extra
    decision = module.parse_time("2024-09-02T00:00:00Z")
    required = {(item[1], item[4]) for item in module.required_corporate_actions(bundle, "idn-eq-v2", decision)}
    assert required == {(3, "raw-eq-split-corr"), (4, "raw-eq-split-other")}
    observation = {
        "market_family": "equity",
        "identity_id": "idn-eq-v2",
        "natural_key": "us-equity:listing:0000000001:common:corporate_action",
        "corporate_actions": [
            {
                "action_type": "split",
                "ratio_numerator": 4,
                "ratio_denominator": 1,
                "ex_date": "2024-09-03",
                "raw_record_id": "raw-eq-split-other",
            }
        ],
    }
    assert module.semantic_codes(bundle, observation, decision) == ["lost_corporate_action_semantics"]
    with pytest.raises(module.ContractError, match="independent actions"):
        module.validate_records(bundle)


def test_correction_without_its_raw_parent_is_rejected(tmp_path: Path) -> None:
    module = _load_validator()
    copy = tmp_path / "O2"
    shutil.copytree(O2, copy)
    observations = json.loads((copy / "fixtures" / "observations.json").read_text(encoding="utf-8"))
    amended = next(item for item in observations["observations"] if item["observation_id"] == "obs-revenue-jul")
    amended["raw_record_ids"] = ["raw-eq-10ka"]
    (copy / "fixtures" / "observations.json").write_text(
        json.dumps(observations, indent=2) + "\n", encoding="utf-8"
    )
    lineage = json.loads((copy / "fixtures" / "lineage.json").read_text(encoding="utf-8"))
    edge = next(item for item in lineage["edges"] if item["observation_id"] == "obs-revenue-jul")
    edge["input_raw_record_ids"] = ["raw-eq-10ka"]
    (copy / "fixtures" / "lineage.json").write_text(json.dumps(lineage, indent=2) + "\n", encoding="utf-8")
    manifest = json.loads((copy / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {
        rel: hashlib.sha256((copy / rel).read_bytes()).hexdigest() for rel in module.EXPECTED_FILES
    }
    (copy / "fixtures" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(module.ContractError, match="raw parent"):
        module.validate_candidate(copy)


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
