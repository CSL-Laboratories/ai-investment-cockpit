from __future__ import annotations

import hashlib
import subprocess
import sys
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _run_checker(*extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, "tools/check_replacement_program.py", *extra),
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def test_replacement_program_registry() -> None:
    subprocess.run(
        (sys.executable, "-m", "unittest", "-q", "tools.test_replacement_program"),
        cwd=ROOT,
        check=True,
    )


def test_live_tree_issue_contract_digest_gate() -> None:
    issue = ROOT / "docs/replacement/ISSUE-CONTRACT.md"
    actual = hashlib.sha256(issue.read_bytes()).hexdigest()
    program = json.loads((ROOT / "docs/replacement/program.json").read_text(encoding="utf-8"))
    pinned = program["authority"]["acceptance_receipt"]["accepted_contracts"][
        "docs/replacement/ISSUE-CONTRACT.md"
    ]
    result = _run_checker()
    assert actual == pinned
    assert result.returncode == 0
    assert "replacement-program: PASS" in result.stdout


def test_registry_cli_rejects_changed_outcome_with_unchanged_receipt(tmp_path: Path) -> None:
    program = json.loads((ROOT / "docs/replacement/program.json").read_text(encoding="utf-8"))
    program["outcomes"][0]["intended_outcome"] = "Approve live execution immediately."
    candidate = tmp_path / "program.json"
    candidate.write_text(json.dumps(program), encoding="utf-8")
    result = _run_checker("--program", str(candidate))
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


@pytest.mark.parametrize("mutation", ["extra", "null", "list", "string", "path_type", "digest_type"])
def test_registry_cli_rejects_invalid_contract_records(tmp_path: Path, mutation: str) -> None:
    program = json.loads((ROOT / "docs/replacement/program.json").read_text(encoding="utf-8"))
    if mutation == "extra":
        program["contracts"][0]["live_authority_granted"] = True
    elif mutation in ("path_type", "digest_type"):
        field = "path" if mutation == "path_type" else "sha256"
        program["contracts"][0][field] = []
    else:
        program["contracts"][0] = {"null": None, "list": [], "string": "accepted"}[mutation]
    candidate = tmp_path / "program.json"
    candidate.write_text(json.dumps(program), encoding="utf-8")
    result = subprocess.run(
        (sys.executable, "tools/check_replacement_program.py", "--program", str(candidate)),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "replacement-program: FAIL: contract" in result.stderr
    assert "Traceback" not in result.stderr
    assert "replacement-program: PASS" not in result.stdout


@pytest.mark.parametrize("field", ["authority", "acceptance_receipt", "superseded_acceptance_receipt"])
@pytest.mark.parametrize("value", [None, True, 1, "accepted", [], [{}]])
def test_registry_cli_rejects_malformed_authority_objects(tmp_path: Path, field: str, value: object) -> None:
    program = json.loads((ROOT / "docs/replacement/program.json").read_text(encoding="utf-8"))
    target = program if field == "authority" else program["authority"]
    target[field] = value
    candidate = tmp_path / "program.json"
    candidate.write_text(json.dumps(program), encoding="utf-8")
    result = subprocess.run(
        (sys.executable, "tools/check_replacement_program.py", "--program", str(candidate)),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "replacement-program: FAIL:" in result.stderr
    assert "Traceback" not in result.stderr
    assert "replacement-program: PASS" not in result.stdout


@pytest.mark.parametrize("path", [
    ("authority", "acceptance_receipt", "not_granted"),
    ("authority", "superseded_acceptance_receipt", "not_granted"),
    ("research_evidence", "authority_compliance", "not_claimed"),
    ("research_evidence", "authority_compliance", "sha256"),
    *(("outcomes", index) for index in range(9)),
    ("schema_version",),
    ("manifest_gate",),
])
@pytest.mark.parametrize("value", [None, True, 1.0, "bad", [], {}, [{}]])
def test_registry_cli_normalizes_malformed_boundaries(
    tmp_path: Path, path: tuple, value: object
) -> None:
    program = json.loads((ROOT / "docs/replacement/program.json").read_text(encoding="utf-8"))
    target = program
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    candidate = tmp_path / "program.json"
    candidate.write_text(json.dumps(program), encoding="utf-8")
    result = _run_checker("--program", str(candidate))
    assert result.returncode == 1
    assert "replacement-program: FAIL:" in result.stderr
    assert "Traceback" not in result.stderr
    assert "replacement-program: PASS" not in result.stdout


@pytest.mark.parametrize("payload", [
    pytest.param(b"\xff", id="invalid-utf8"),
    pytest.param(b"{", id="invalid-json"),
    pytest.param(b'{"x":' + b"9" * 5000 + b"}", id="oversized-integer"),
    pytest.param(b"[" * 10000 + b"0" + b"]" * 10000, id="excessive-nesting"),
])
def test_registry_cli_normalizes_parser_failures(tmp_path: Path, payload: bytes) -> None:
    candidate = tmp_path / "program.json"
    candidate.write_bytes(payload)
    result = subprocess.run(
        (sys.executable, "tools/check_replacement_program.py", "--program", str(candidate)),
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONINTMAXSTRDIGITS": "4300"},
    )
    assert result.returncode == 1
    assert "replacement-program: FAIL:" in result.stderr
    assert "invalid program input:" in result.stderr or "program root must be an object" in result.stderr
    assert "Traceback" not in result.stderr
    assert "replacement-program: PASS" not in result.stdout


@pytest.mark.parametrize("field,value", [
    ("schema_version", "1"),
    ("live_authority_granted", "true"),
    ("live_authority_granted", "false"),
    ("not_granted", "[]"),
    ("intended_outcome", '"Live execution approved"'),
])
def test_registry_cli_rejects_duplicate_keys(tmp_path: Path, field: str, value: str) -> None:
    payload = (ROOT / "docs/replacement/program.json").read_text(encoding="utf-8")
    key = json.dumps(field) + ":"
    assert key in payload
    candidate = tmp_path / "program.json"
    candidate.write_text(payload.replace(key, f"{key} {value}, {key}", 1), encoding="utf-8")
    result = subprocess.run(
        (sys.executable, "tools/check_replacement_program.py", "--program", str(candidate)),
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "replacement-program: FAIL: invalid program input: duplicate object key:" in result.stderr
    assert "Traceback" not in result.stderr
    assert "replacement-program: PASS" not in result.stdout
