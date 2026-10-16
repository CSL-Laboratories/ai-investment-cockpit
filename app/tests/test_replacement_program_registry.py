from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_replacement_program_registry() -> None:
    commands = (
        (sys.executable, "tools/check_replacement_program.py"),
        (sys.executable, "-m", "unittest", "-q", "tools.test_replacement_program"),
    )
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)


def test_registry_cli_rejects_changed_outcome_with_unchanged_receipt(tmp_path: Path) -> None:
    program = json.loads((ROOT / "docs/replacement/program.json").read_text(encoding="utf-8"))
    program["outcomes"][0]["intended_outcome"] = "Approve live execution immediately."
    candidate = tmp_path / "program.json"
    candidate.write_text(json.dumps(program), encoding="utf-8")
    result = subprocess.run(
        (sys.executable, "tools/check_replacement_program.py", "--program", str(candidate)),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "outcome integrity baseline changed" in result.stderr


@pytest.mark.parametrize("container", [None, "research_evidence"])
def test_registry_cli_rejects_enclosing_authority_claims(tmp_path: Path, container: str | None) -> None:
    program = json.loads((ROOT / "docs/replacement/program.json").read_text(encoding="utf-8"))
    target = program if container is None else program[container]
    target["live_authority_granted"] = True
    candidate = tmp_path / "program.json"
    candidate.write_text(json.dumps(program), encoding="utf-8")
    result = subprocess.run(
        (sys.executable, "tools/check_replacement_program.py", "--program", str(candidate)),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "fields changed" in result.stderr
    assert "replacement-program: PASS" not in result.stdout
