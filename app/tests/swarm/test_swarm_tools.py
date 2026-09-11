"""Deterministic tests for Cloud swarm spawn/intake helpers. No Devin spawn."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPAWN_PATH = ROOT / "docs/research/swarm-tools/spawn_devin_leaf.py"
VALIDATE_PATH = ROOT / "docs/research/swarm-tools/validate_filings.py"
FIXTURES = ROOT / "docs/research/swarm-tools/fixtures"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


spawn = _load("spawn_devin_leaf", SPAWN_PATH)
validate = _load("validate_filings", VALIDATE_PATH)


def test_frozen_argv_is_accept_edits_without_sandbox(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("brief\n", encoding="utf-8")
    export = tmp_path / "export" / "leaf.atif.json"
    argv = spawn.frozen_argv(brief=brief, export=export, model="swe-2-medium")
    assert argv[0] == "devin"
    assert "--sandbox" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "accept-edits"
    assert argv[argv.index("--model") + 1] == "swe-2-medium"
    assert "--prompt-file" in argv
    assert "-p" in argv
    assert "autonomous" not in argv
    assert "dangerous" not in argv
    assert "smart" not in argv
    assert spawn.forbidden_token(argv) is None


def test_dry_run_does_not_require_devin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVIN_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("DEVIN_SANDBOX", raising=False)
    brief = tmp_path / "brief.md"
    brief.write_text("brief\n", encoding="utf-8")
    export = tmp_path / "export" / "leaf.atif.json"
    rc = spawn.main(
        [
            "--brief",
            str(brief),
            "--export",
            str(export),
            "--dry-run",
        ]
    )
    assert rc == 0


def test_refuses_unknown_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVIN_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("DEVIN_SANDBOX", raising=False)
    brief = tmp_path / "brief.md"
    brief.write_text("brief\n", encoding="utf-8")
    rc = spawn.main(
        [
            "--brief",
            str(brief),
            "--export",
            str(tmp_path / "out.json"),
            "--model",
            "nope",
            "--dry-run",
        ]
    )
    assert rc == 2


def test_refuses_widened_permission_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEVIN_PERMISSION_MODE", "dangerous")
    monkeypatch.delenv("DEVIN_SANDBOX", raising=False)
    brief = tmp_path / "brief.md"
    brief.write_text("brief\n", encoding="utf-8")
    rc = spawn.main(
        [
            "--brief",
            str(brief),
            "--export",
            str(tmp_path / "out.json"),
            "--dry-run",
        ]
    )
    assert rc == 2


def test_refuses_sandbox_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVIN_PERMISSION_MODE", raising=False)
    monkeypatch.setenv("DEVIN_SANDBOX", "1")
    brief = tmp_path / "brief.md"
    brief.write_text("brief\n", encoding="utf-8")
    rc = spawn.main(
        [
            "--brief",
            str(brief),
            "--export",
            str(tmp_path / "out.json"),
            "--dry-run",
        ]
    )
    assert rc == 2


def test_validate_filings_accepts_fixtures(tmp_path: Path) -> None:
    rc = validate.main(
        [
            "--run-dir",
            str(tmp_path / "run"),
            "--leaf",
            str(FIXTURES / "valid-leaf.json"),
            "--review",
            str(FIXTURES / "valid-review.json"),
            "--synthesis",
            str(FIXTURES / "valid-synthesis.json"),
        ]
    )
    assert rc == 0
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["empirical_pass_claimed"] is False
    assert len(manifest["filings"]) == 3

def test_validate_filings_rejects_pass_claimed_synthesis(tmp_path: Path) -> None:
    bad = tmp_path / "bad-synthesis.json"
    payload = json.loads((FIXTURES / "valid-synthesis.json").read_text(encoding="utf-8"))
    payload["empirical_pass_claimed"] = True
    bad.write_text(json.dumps(payload), encoding="utf-8")
    rc = validate.main(
        [
            "--run-dir",
            str(tmp_path / "run"),
            "--synthesis",
            str(bad),
        ]
    )
    assert rc == 1
