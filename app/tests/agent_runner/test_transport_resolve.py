"""RW-01 bring-up — the runtime CLI must actually be spawnable on the DEPLOY platform.

Found by running the real thing, not a fixture: ``subprocess.run(["claude", ...])`` raises
``FileNotFoundError`` on Windows even though ``claude`` is on PATH and works in the shell. npm
installs its CLIs as ``claude.CMD``, and ``CreateProcess`` does not append ``.CMD``/``.BAT``
when resolving a bare name — only a shell does. Every existing test injects a fake transport,
so nothing in the suite ever spawned a process and the whole suite stayed green while the
brew's first agent call was guaranteed to die on the deploy box.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agent_runner.transport import (
    ExecutableNotFound,
    SubprocessTransport,
    resolve_executable,
)


def test_resolves_a_bare_name_to_an_absolute_path(monkeypatch):
    """The fix in one line: a bare name becomes a real path, applying PATHEXT the way a shell
    does. On POSIX this is a no-op that returns the plain path."""
    # A project-local virtual environment need not be added to the user's global PATH. Make the
    # bare-name lookup precondition explicit so this test exercises ``resolve_executable`` rather
    # than relying on whatever Python installation happens to be globally discoverable.
    interpreter_dir = os.path.dirname(sys.executable)
    monkeypatch.setenv("PATH", interpreter_dir + os.pathsep + os.environ.get("PATH", ""))
    resolved = resolve_executable([sys.executable.rsplit(os.sep, 1)[-1].replace(".exe", ""),
                                   "--version"])
    assert os.path.isabs(resolved[0])
    assert resolved[1:] == ["--version"]


def test_missing_executable_names_the_tool_instead_of_winerror_2():
    """`[WinError 2] The system cannot find the file specified` names nothing, and reads like a
    missing data file. The operator needs to know it is the CLI that is absent."""
    with pytest.raises(ExecutableNotFound) as exc:
        resolve_executable(["definitely-not-a-real-cli-xyz", "-p"])
    assert "definitely-not-a-real-cli-xyz" in str(exc.value)
    assert "PATH" in str(exc.value)


def test_a_real_subprocess_actually_spawns_through_the_transport():
    """The end-to-end property the suite never had: a REAL process, through the REAL transport.

    Uses the running interpreter, so it needs no external tool and works on any platform.
    Without ``resolve_executable`` this is the exact call that failed on Windows.
    """
    result = SubprocessTransport().run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        input_text="brew", timeout_s=60, cwd=None)
    assert result.returncode == 0
    assert result.stdout.strip() == "BREW"
    assert result.timed_out is False


def test_empty_argv_is_passed_through_untouched():
    assert resolve_executable([]) == []


# ---------------------------------------------------------------------------------------
# the cmd-shim safety gate
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("hostile", ["AAPL&calc", "x|y", "a>b", 'q"r', "p^q"])
def test_cmd_shim_refuses_argv_carrying_cmd_metacharacters(tmp_path, monkeypatch, hostile):
    """A ``.cmd`` target is launched THROUGH cmd.exe, which re-parses the command line after
    Python's quoting — and Python does not escape cmd metacharacters (the BatBadBut class,
    CVE-2024-24576 in Rust's stdlib).

    Nothing untrusted reaches argv today (``build_argv`` emits config-derived values only; the
    prompt travels on STDIN), so this is defence in depth. It exists because that property is
    easy to lose silently — the day someone threads a symbol or a model-supplied session id
    into argv, this refuses instead of executing it.
    """
    shim = tmp_path / "faketool.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda _name: str(shim))

    with pytest.raises(ValueError, match="cmd metacharacter"):
        resolve_executable(["faketool", hostile])


def test_cmd_shim_accepts_ordinary_arguments(tmp_path, monkeypatch):
    """POSITIVE CONTROL: the gate must not block the argv the runner actually builds, or it
    would trade a spawn failure for a refusal."""
    shim = tmp_path / "faketool.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda _name: str(shim))

    argv = resolve_executable(["faketool", "-p", "--output-format", "json", "--model",
                               "claude-haiku-4-5-20251001", "--max-turns", "6",
                               "--allowedTools", "none"])
    assert argv[0] == str(shim)
    assert argv[1:3] == ["-p", "--output-format"]


def test_non_shim_targets_are_not_gated(tmp_path, monkeypatch):
    """The gate is about cmd.exe re-parsing, so it applies ONLY to .cmd/.bat. A real executable
    takes its argv directly and needs no such restriction."""
    exe = tmp_path / "faketool.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr("shutil.which", lambda _name: str(exe))
    assert resolve_executable(["faketool", "a&b"])[1] == "a&b"


# ---------------------------------------------------------------------------------------
# the deploy-platform smoke: is the CLI the cockpit needs actually spawnable HERE?
# ---------------------------------------------------------------------------------------
def test_claude_cli_is_spawnable_when_installed():
    """Skips where the CLI is absent (CI installs no runtime), but on a box that HAS it this is
    the check that would have caught the Windows spawn failure before a market morning."""
    import shutil

    if shutil.which("claude") is None:
        pytest.skip("claude CLI not installed on this host")
    result = SubprocessTransport().run(["claude", "--version"], input_text="",
                                       timeout_s=120, cwd=None)
    assert result.returncode == 0, f"claude is on PATH but did not spawn: {result.stderr!r}"
    assert "Claude" in result.stdout or result.stdout.strip()


def test_codex_cli_is_spawnable_when_installed():
    """Codex is a supported non-decision runtime and must be spawnable through the same
    Windows-aware transport as Claude. ``--version`` is local and does not spend a model turn."""
    import shutil

    if shutil.which("codex") is None:
        pytest.skip("codex CLI not installed on this host")
    result = SubprocessTransport().run(["codex", "--version"], input_text="",
                                       timeout_s=120, cwd=None)
    assert result.returncode == 0, f"codex is on PATH but did not spawn: {result.stderr!r}"
    assert "codex" in result.stdout.lower() or result.stdout.strip()


def test_subprocess_run_without_resolution_is_what_broke():
    """Documents the defect so a future 'simplification' back to a bare name fails loudly.

    On Windows a bare npm-shim name raises FileNotFoundError; on POSIX it resolves fine. Both
    outcomes are asserted rather than skipped, so the test states the platform difference that
    made this invisible in CI.
    """
    import shutil

    resolved = shutil.which("claude")
    if resolved is None:
        pytest.skip("claude CLI not installed on this host")
    if os.name != "nt":
        subprocess.run(["claude", "--version"], capture_output=True, timeout=120)
        return
    if not resolved.lower().endswith((".cmd", ".bat")):
        pytest.skip("claude resolves to a native executable, not a Windows cmd shim")
    with pytest.raises(FileNotFoundError):
        subprocess.run(["claude", "--version"], capture_output=True, timeout=120)
