"""CSL-216 O4 candidate packet. Provider-free. No network and no credentials.

The negative controls are supposed to fail validation. This test passes only
when each frozen control fails for its designed reason, and it prints those
failures so the CI log shows them.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
O4 = ROOT / "docs" / "replacement" / "outcomes" / "O4"
COMMAND = ("python", "-X", "utf8", "-B", "docs/replacement/outcomes/O4/validate.py")
ACCEPTED = {
    "docs/replacement/SUCCESSOR-PRODUCT.md":
        "6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e",
    "docs/replacement/ISSUE-CONTRACT.md":
        "f1fb50dab4b166ca476698fc3974d6a1c2003b16df1d89ff8930060ad04b5094",
    "docs/replacement/MONTH-PROGRAM.md":
        "f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001",
    "docs/replacement/SCOPE-AUDIT.md":
        "d23dcc769e743d199ae4142f0e20ad5feebfe310c71d9f57fdd9d647c230db8c",
}
FAILURES = (
    "O4 negative control FAIL overlapping_populations:",
    "O4 negative control FAIL dropped_costs:",
    "O4 negative control FAIL dropped_empty_outcome:",
    "O4 negative control FAIL parameter_variants_as_distinct_strategies:",
    "O4 negative control FAIL decision_owner_role:",
    "O4 negative control FAIL leakage_controls_satisfied:",
    "O4 negative control FAIL variant_configuration:",
    "O4 negative control FAIL unbound_population:",
    "O4 negative control FAIL metric_registry:",
    "O4 negative control FAIL measured_costs_on_unrun_experiment:",
    "O4 negative control FAIL promotion_stage_authorized:",
    "O4 negative control FAIL promotion_gates:",
)
CHECKED = (
    "O4 candidate packet checked at maturity researched; "
    "qualify_count=0; promotion_authorized=false"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_o4_negative_controls_fail_as_designed(capsys) -> None:
    completed = subprocess.run(
        (sys.executable, *COMMAND[1:]),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    with capsys.disabled():
        sys.stdout.write(completed.stdout)
        sys.stdout.flush()
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert len(lines) == 13
    for line, prefix in zip(lines, FAILURES):
        assert line.startswith(prefix)
    assert lines[12] == CHECKED
    assert "PASS" not in completed.stdout
    assert "contract_frozen" not in completed.stdout
    source = (O4 / "validate.py").read_text(encoding="utf-8")
    for banned in ("import socket", "import urllib", "import httpx", "import requests"):
        assert banned not in source


def test_o4_protocol_keeps_research_and_owner_decisions() -> None:
    protocol = (O4 / "PROTOCOL.md").read_text(encoding="utf-8")
    assert "## Research (Parallel)" in protocol
    assert "## Unresolved decisions (owner: roles)" in protocol
    assert "search_a81ed0bcb8b97e64d4f958ef59dde38a" in protocol
    assert "search_73f6ee6002538aa601e083ab7169dee4" in protocol
    for url in (
        "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551",
        "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253",
        "https://doi.org/10.1016/j.knosys.2024.112477",
        "https://github.com/eslazarev/purged-cross-validation",
    ):
        assert url in protocol
    for decision in ("Spend", "Credentials", "Tax/legal", "Architecture", "Release", "Live", "Thresholds"):
        assert f"| {decision} |" in protocol
    assert "**Maturity claimed:** `researched`." in protocol
    packet = json.loads((O4 / "fixtures" / "candidate-packet.json").read_text(encoding="utf-8"))
    assert packet["maturity"] == "researched"
    assert packet["qualification_claimed"] is False
    assert packet["contract_frozen_claimed"] is False
    assert packet["empirical_pass_claimed"] is False
    assert [item["status"] for item in packet["unresolved_decisions"]] == ["undecided"] * 7
    owners = {item["id"]: item["owner"] for item in packet["unresolved_decisions"]}
    assert owners["release"] == "release owner"
    assert owners["spend"] == owners["credentials"] == "reserved authority"
    assert set(owners.values()) == {"owner", "release owner", "reserved authority"}
    registry_ids = [item["id"] for item in packet["metric_registry"]["measures"]]
    assert "net_yield" in registry_ids and "capital_use" in registry_ids
    for variant in packet["strategy_contract"]["parameter_variants"]:
        assert variant["configuration"]["legs"]
        assert len(variant["configuration_sha256"]) == 64


def test_o4_leaves_accepted_csl212_digests_unchanged() -> None:
    program = json.loads((ROOT / "docs" / "replacement" / "program.json").read_text(encoding="utf-8"))
    recorded = {item["path"]: item["sha256"] for item in program["contracts"]}
    assert recorded == ACCEPTED
    for path, digest in ACCEPTED.items():
        assert _sha256(ROOT / path) == digest
    outcome = next(item for item in program["outcomes"] if item["key"] == "O4")
    assert outcome["evidence"]["claimed"] == "researched"
    assert outcome["completion_gate_status"] == "pending_outcome_artifact"
    assert program["authority"]["live_authority_granted"] is False
