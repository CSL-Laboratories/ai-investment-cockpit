"""Regression tests for the Claude Code cloud-session hook (.claude/hooks/session-start.sh).

Hermetic: each test runs the real hook in a temporary project with stubbed git, python3,
uv, rustup and cargo. The project's ``.venv/bin/python`` is a wrapper around this test's
own interpreter, which already has the CI dependency set, and ``PIP_NO_INDEX=1`` keeps any
install attempt offline, so nothing is downloaded or changed. Linux-only: the hook targets
the cloud VM (bash, ``sha256sum``, a POSIX venv layout).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "session-start.sh"
STAMP = Path(".venv") / ".cloud-deps.sha256"

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bash") or not shutil.which("sha256sum"),
    reason="cloud-session hook is Linux-only",
)

PY = sys.executable
GOOD = f'#!/bin/sh\nexec "{PY}" "$@"\n'
NO_PIP = f'#!/bin/sh\ncase "$*" in *"-m pip"*) exit 1;; esac\nexec "{PY}" "$@"\n'
OLD_PY = f'#!/bin/sh\ncase "$*" in *version_info*) exit 1;; esac\nexec "{PY}" "$@"\n'
OK, FAIL = "#!/bin/sh\nexit 0\n", "#!/bin/sh\nexit 1\n"
# Stands in for `python3 -m venv .venv`: writes a working venv, plus the stamp if asked.
VENV_MAKER = (
    "#!/bin/sh\n"
    'if [ "$1" = -m ] && [ "$2" = venv ]; then\n'
    '  mkdir -p "$3/bin"\n'
    f"  printf '%s' '{GOOD}' > \"$3/bin/python\"\n"
    '  chmod +x "$3/bin/python"\n'
    '  [ -n "${STAMP_HASH:-}" ] && echo "$STAMP_HASH" > "$3/.cloud-deps.sha256"\n'
    "  exit 0\n"
    "fi\n"
    f'exec "{PY}" "$@"\n'
)


def _write(path: Path, text: str, mode: int = 0o755) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def _deps_hash(project: Path) -> str:
    data = (project / "app/pyproject.toml").read_bytes()
    data += (project / "app/benchmarks/requirements.txt").read_bytes()
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    for rel in ("app/pyproject.toml", "app/benchmarks/requirements.txt"):
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, proj / rel)
    (proj / "rust").mkdir()
    return proj


def _run(project: Path, stubs: dict[str, str], *, remote: bool = True, **env: str):
    bin_dir = project.parent / "stubs"
    for name, body in stubs.items():
        _write(bin_dir / name, body)
    full_env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CLAUDE_PROJECT_DIR": str(project),
        "CLAUDE_ENV_FILE": str(project.parent / "claude.env"),
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        **env,
    }
    full_env.pop("CLAUDE_CODE_REMOTE", None)
    if remote:
        full_env["CLAUDE_CODE_REMOTE"] = "true"
    result = subprocess.run(
        ["bash", str(HOOK)], cwd=project, env=full_env,
        capture_output=True, text=True, timeout=120,
    )
    return result.returncode, result.stdout


HEALTHY_TOOLS = {"git": OK, "python3": VENV_MAKER, "uv": FAIL, "rustup": OK, "cargo": OK}


def test_local_session_is_a_no_op(project):
    code, out = _run(project, {"git": FAIL}, remote=False)
    assert (code, out) == (0, "")
    assert not (project / ".venv").exists()


def test_every_step_failing_is_reported_not_hidden(project):
    tools = {name: FAIL for name in ("git", "python3", "uv", "rustup", "cargo")}
    code, out = _run(project, tools)
    assert code == 0, "the hook must exit 0 so its status line reaches Claude"
    assert "fetch=FAIL" in out and "venv=FAIL" in out and "deps=skipped" in out
    assert "origin/main unknown (stale)" in out
    assert "Setup incomplete" in out
    assert not (project / ".venv").exists()


def test_cached_healthy_venv_with_matching_stamp_is_ok(project):
    _write(project / ".venv/bin/python", GOOD)
    (project / STAMP).write_text(_deps_hash(project) + "\n")
    code, out = _run(project, HEALTHY_TOOLS)
    assert code == 0
    assert "venv=ok deps=ok rust=ok" in out
    assert "Setup incomplete" not in out
    env_file = (project.parent / "claude.env").read_text()
    assert f"{project}/.venv/bin" in env_file


@pytest.mark.parametrize("broken", [NO_PIP, OLD_PY], ids=["no-pip", "python-below-3.11"])
def test_unusable_cached_venv_is_rebuilt(project, broken):
    _write(project / ".venv/bin/python", broken)
    code, out = _run(project, HEALTHY_TOOLS, STAMP_HASH=_deps_hash(project))
    assert code == 0
    assert "venv=ok deps=ok" in out
    assert (project / ".venv/bin/python").read_text() == GOOD


def test_missing_dependency_fails_and_drops_the_stamp(project):
    pyproject = project / "app/pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace(
        "dependencies = [", 'dependencies = [\n  "csl-no-such-package-for-hook-test",', 1))
    _write(project / ".venv/bin/python", GOOD)
    (project / STAMP).write_text(_deps_hash(project) + "\n")
    code, out = _run(project, HEALTHY_TOOLS)
    assert code == 0
    assert "deps=FAIL" in out and "Setup incomplete" in out
    assert not (project / STAMP).exists(), "a failed check must drop the stamp"


def test_violated_version_constraint_fails(project):
    reqs = project / "app/benchmarks/requirements.txt"
    reqs.write_text(reqs.read_text() + "pytest<1\n")
    _write(project / ".venv/bin/python", GOOD)
    (project / STAMP).write_text(_deps_hash(project) + "\n")
    code, out = _run(project, HEALTHY_TOOLS)
    assert code == 0
    assert "deps=FAIL" in out
    assert not (project / STAMP).exists()
