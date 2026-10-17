"""Provider-free checks for the CSL-220 O8 phase A contract artifacts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = "tools/check_o8_phase_a.py"
EXPECTED_NEGATIVE_LINES = (
    "O8 negative control failed as designed: omit-io: omits I/O",
    "O8 negative control failed as designed: omit-helpers: omits helper processes",
    "O8 negative control failed as designed: omit-variance: omits variance",
    "O8 negative control failed as designed: candidate-missing-floors: candidate missing floors",
    "O8 negative control failed as designed: floor-enforced-by-benchmark: candidate missing floors",
    "O8 negative controls: 5 failed as designed",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, "-X", "utf8", "-B", VALIDATOR, *args),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_o8_phase_a_fixture_stays_researched_and_negative_controls_fail(capsys) -> None:
    positive = _run()
    assert positive.returncode == 0, positive.stderr
    assert "maturity=researched" in positive.stdout
    assert "completion_gate_status=pending_outcome_artifact" in positive.stdout
    assert "per_pr_benchmark_gating=false" in positive.stdout
    assert "new_ci_cadence=false" in positive.stdout
    assert "PASS" not in positive.stdout
    assert "contract_frozen" not in positive.stdout
    assert "qualification" not in positive.stdout

    negative = _run("--negative-controls")
    assert negative.returncode == 0, negative.stderr
    for line in EXPECTED_NEGATIVE_LINES:
        assert line in negative.stdout
    # Pytest captures the subprocess. Write the designed failures to the CI log.
    with capsys.disabled():
        sys.stdout.write(negative.stdout)
        if not negative.stdout.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()


def test_o8_phase_a_rejects_a_promoted_maturity(tmp_path: Path) -> None:
    packet = json.loads((ROOT / "docs/replacement/outcomes/O8/fixtures/phase-a-packet.json").read_text(encoding="utf-8"))
    packet["maturity"] = "contract_frozen"
    candidate = tmp_path / "packet.json"
    candidate.write_text(json.dumps(packet), encoding="utf-8")
    result = _run("--manifest", str(candidate), "--kind", "packet")
    assert result.returncode == 1
    assert "maturity is researched only" in result.stderr
    assert "fixture accepted" not in result.stdout


def test_o8_contract_doc_keeps_researched_maturity_and_lucas_decisions() -> None:
    text = (ROOT / "docs/replacement/outcomes/O8/PHASE-A.md").read_text(encoding="utf-8")
    assert "Maturity claimed: `researched`." in text
    assert "## Unresolved decisions (owner: Lucas)" in text
    assert "## Research (Parallel)" in text
    for url in (
        "https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig",
        "https://pyperf.readthedocs.io/en/stable/run_benchmark.html",
        "https://pyperf.readthedocs.io/en/latest/analyze.html",
        "https://github.com/psf/pyperf",
        "https://enersys.co.th/en/insights/strangler-fig-migration-python-to-rust-2026",
    ):
        assert url in text


def test_o8_validator_does_not_import_network_or_credentials() -> None:
    source = (ROOT / "tools" / "check_o8_phase_a.py").read_text(encoding="utf-8")
    imported = "\n".join(
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    )
    for banned in ("keyring", "httpx", "urllib", "socket", "discord", "alpaca", "boto3"):
        assert banned not in imported
