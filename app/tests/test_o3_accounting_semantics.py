"""CSL-215 O3 candidate validator. Provider-free: local files only, no network."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import warnings
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "docs" / "replacement" / "outcomes" / "O3" / "validate_candidate.py"
NEGATIVE_IDS = (
    "neg_double_counted_collateral_after_restart",
    "neg_voided_market_unreconciled",
    "neg_partial_fill_divergence",
    "neg_duplicate_fill_divergence",
    "neg_late_fill_divergence",
)


def test_o3_negative_controls_fail_as_designed() -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    for fixture_id in NEGATIVE_IDS:
        needle = f"O3 NEGATIVE CONTROL FAILED AS DESIGNED: {fixture_id} "
        assert needle in result.stdout
    assert "O3 candidate maturity remains researched" in result.stdout
    assert "contract_frozen" not in result.stdout
    for line in result.stdout.splitlines():
        if line.startswith("O3 NEGATIVE CONTROL FAILED AS DESIGNED:"):
            warnings.warn(line, UserWarning, stacklevel=1)


def test_o3_registry_maturity_stays_researched() -> None:
    program = json.loads((ROOT / "docs" / "replacement" / "program.json").read_text(encoding="utf-8"))
    outcome = next(item for item in program["outcomes"] if item["key"] == "O3")
    assert outcome["linear_issue"] == "CSL-215"
    assert outcome["evidence"]["claimed"] == "researched"
    assert "contract_frozen" in outcome["evidence"]["not_claimed"]
    assert outcome["completion_gate_status"] == "pending_outcome_artifact"
    assert outcome["discretion_class"] == "human_gated"


def test_accepted_csl212_contract_digests_unchanged() -> None:
    program = json.loads((ROOT / "docs" / "replacement" / "program.json").read_text(encoding="utf-8"))
    for record in program["contracts"]:
        digest = hashlib.sha256((ROOT / record["path"]).read_bytes()).hexdigest()
        assert digest == record["sha256"]
