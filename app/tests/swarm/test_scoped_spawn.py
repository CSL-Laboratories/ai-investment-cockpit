"""RW-33 offline previews and filing admission. No model job is dispatched."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scoped = _load("scoped", ROOT / "docs/research/swarm-tools/spawn_scoped_leaf.py")
intake = _load("intake", ROOT / "docs/research/swarm-tools/validate_filings.py")
FIXTURES = ROOT / "docs/research/swarm-tools/fixtures"


@pytest.fixture
def preview(tmp_path, monkeypatch):
    briefs = tmp_path / "briefs"
    briefs.mkdir()
    runs = tmp_path / "runs"
    monkeypatch.setattr(scoped, "BRIEFS_DIR", briefs)
    monkeypatch.setattr(scoped, "RUNS_DIR", runs)
    brief = briefs / "brief.md"
    brief.write_text("Illustration, not a qualified dispatch.", encoding="utf-8")
    export = runs / "attempt-1" / "filing.json"
    args = ["--brief", str(brief), "--export", str(export), "--model", "requested-model"]
    return brief, export, args


@pytest.mark.parametrize("runtime", ["cursor", "codex", "opencode"])
def test_launch_refused_before_io_even_with_cli_available(runtime, preview, monkeypatch):
    brief, export, args = preview
    export.parent.mkdir(parents=True)
    export.write_bytes(b"prior attempt")
    monkeypatch.setenv("OPENCODE_AUTO", "1")
    monkeypatch.setenv("CURSOR_INHERIT_BULK", "1")

    def unexpected(*args, **kwargs):
        pytest.fail("A disabled route attempted child dispatch or brief I/O")

    monkeypatch.setattr(subprocess, "run", unexpected)
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    monkeypatch.setattr(Path, "open", unexpected)
    assert scoped.main(["--runtime", runtime, *args]) == 2
    monkeypatch.undo()
    assert export.read_bytes() == b"prior attempt"
    assert brief.exists()


@pytest.mark.parametrize("runtime", ["cursor", "codex", "opencode"])
def test_preview_is_honest_and_has_no_output_side_effect(runtime, preview, capsys):
    brief, export, args = preview
    assert scoped.main(["--runtime", runtime, *args, "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["requested_model"] == "requested-model"
    assert result["model_available"] is None
    assert result["dispatch_enabled"] is False
    assert result["brief_sha256"] == hashlib.sha256(brief.read_bytes()).hexdigest()
    assert not export.parent.exists()


@pytest.mark.parametrize("flag", ["--auto", "--resume", "--continue", "--sandbox",
                                 "--force", "--trust", "--approve-mcps", "--api-key=secret-marker",
                                 "--dry"])
def test_unknown_options_refused_without_echoing_values(flag, preview, capsys):
    _, export, args = preview
    with pytest.raises(SystemExit) as exc:
        scoped.main(["--runtime", "cursor", *args, flag])
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "secret-marker" not in output.err + output.out
    assert not export.parent.exists()


@pytest.mark.parametrize("case", ["existing", "outside", "empty", "oversized", "missing", "runtime"])
def test_invalid_preview_preserves_files(case, preview):
    brief, export, args = preview
    runtime = "cursor"
    if case == "existing":
        export.parent.mkdir(parents=True)
        export.write_bytes(b"original")
    elif case == "outside":
        args[args.index("--export") + 1] = str(brief.parent.parent / "outside.json")
    elif case == "empty":
        brief.write_bytes(b"")
    elif case == "oversized":
        brief.write_bytes(b"x" * (scoped.MAX_BRIEF_BYTES + 1))
    elif case == "missing":
        brief.unlink()
    elif case == "runtime":
        runtime = "devin"
    assert scoped.main(["--runtime", runtime, *args, "--dry-run"]) == 2
    if case == "existing":
        assert export.read_bytes() == b"original"


def test_resolved_brief_escape_refused(preview):
    brief, _, args = preview
    outside = brief.parent.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = brief.parent / "escape.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    args[args.index("--brief") + 1] = str(link)
    assert scoped.main(["--runtime", "cursor", *args, "--dry-run"]) == 2


def packet(tmp_path, name, **updates):
    payload = json.loads((FIXTURES / f"valid-{name}.json").read_text())
    payload.update(updates)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def intake_args(tmp_path, synthesis_updates=None, leaf_updates=None, review_updates=None):
    leaf = packet(tmp_path, "leaf", **(leaf_updates or {}))
    review = packet(tmp_path, "review", **(review_updates or {}))
    synthesis = packet(tmp_path, "synthesis", **(synthesis_updates or {}))
    return ["--run-dir", str(tmp_path / "run"), "--leaf", str(leaf),
            "--review", str(review), "--synthesis", str(synthesis)]


@pytest.mark.parametrize("role", ["muse-second-gate", "openrouter-second-gate", "hive-second-gate"])
def test_all_synthesis_roles_validate_with_attempt_and_distinct_family(role, tmp_path):
    assert intake.main(intake_args(tmp_path, {"role": role})) == 0
    manifest = json.loads((tmp_path / "run/manifest.json").read_text())
    assert len(manifest["filings"]) == 3
    assert not manifest["empirical_pass_claimed"]


@pytest.mark.parametrize("kind", ["leaf", "review", "synthesis"])
def test_empirical_pass_quarantined_for_every_role(kind, tmp_path, capsys):
    args = intake_args(tmp_path)
    packet(tmp_path, kind, empirical_pass_claimed=True,
           **({"summary": "secret-marker"} if kind == "synthesis" else {}))
    assert intake.main(args) == 1
    assert not (tmp_path / "run/manifest.json").exists()
    assert "secret-marker" not in capsys.readouterr().out


@pytest.mark.parametrize("update", [
    {"route_family": "fixture-producer"},
    {"source_families": ["unrelated-family"]},
    {"leaf_ids": ["unrelated-leaf"]},
    {"attempt": 0},
])
def test_unbound_or_same_family_synthesis_quarantined(update, tmp_path):
    assert intake.main(intake_args(tmp_path, update)) == 1
    assert not (tmp_path / "run/manifest.json").exists()


def test_manifest_cannot_be_overwritten(tmp_path):
    args = intake_args(tmp_path)
    assert intake.main(args) == 0
    before = (tmp_path / "run/manifest.json").read_bytes()
    assert intake.main(args) == 1
    assert (tmp_path / "run/manifest.json").read_bytes() == before


@pytest.mark.parametrize("content", [
    b'{"job_id":"one","job_id":"two"}',
    b'{"type":"event"}\n{"type":"final"}',
    b"x" * (intake.MAX_FILING_BYTES + 1),
], ids=["duplicate-key", "event-stream", "oversized"])
def test_transport_or_duplicate_or_oversize_is_not_a_filing(content, tmp_path):
    path = tmp_path / "invalid.json"
    path.write_bytes(content)
    assert intake.main(["--run-dir", str(tmp_path / "run"), "--leaf", str(path)]) == 1
    assert not (tmp_path / "run/manifest.json").exists()


def test_duplicate_attempt_and_empty_intake_refused(tmp_path):
    leaf = packet(tmp_path, "leaf")
    args = ["--run-dir", str(tmp_path / "run")]
    assert intake.main(args) == 1
    assert intake.main([*args, "--leaf", str(leaf), "--leaf", str(leaf)]) == 1
    assert not (tmp_path / "run/manifest.json").exists()
