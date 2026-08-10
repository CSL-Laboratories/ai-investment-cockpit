"""BUG-031: ``timeout_s`` must bound the entire spawned process tree."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import time

import pytest

from agent_runner.transport import SubprocessTransport

CHILD_SLEEP_SECONDS = 25
TIMEOUT_SECONDS = 2.0
ALLOWED_OVERSHOOT_SECONDS = 10.0


def _grandchild_launcher(tmp_path):
    child = tmp_path / "grandchild.py"
    child.write_text(
        textwrap.dedent(
            f"""
            import time
            time.sleep({CHILD_SLEEP_SECONDS})
            """
        ),
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        textwrap.dedent(
            f"""
            import subprocess, sys, time
            subprocess.Popen([sys.executable, {str(child)!r}])
            time.sleep({CHILD_SLEEP_SECONDS})
            """
        ),
        encoding="utf-8",
    )
    return [sys.executable, str(parent)]


def test_timeout_kills_the_whole_tree_and_returns_promptly(tmp_path):
    argv = _grandchild_launcher(tmp_path)

    started = time.monotonic()
    result = SubprocessTransport().run(
        argv,
        input_text="",
        timeout_s=TIMEOUT_SECONDS,
        cwd=None,
    )
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert result.returncode == 124
    assert elapsed < TIMEOUT_SECONDS + ALLOWED_OVERSHOOT_SECONDS, (
        f"timeout_s={TIMEOUT_SECONDS} did not bound the call: it took {elapsed:.1f}s; "
        "a surviving descendant held the captured pipes open"
    )


def test_transport_normal_path_still_delivers_stdin_and_stdout():
    result = SubprocessTransport().run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        input_text="brew",
        timeout_s=60,
        cwd=None,
    )
    assert (result.returncode, result.stdout.strip(), result.timed_out) == (0, "BREW", False)


def test_transport_still_honours_cwd(tmp_path):
    result = SubprocessTransport().run(
        [sys.executable, "-c", "import os,sys; sys.stdout.write(os.getcwd())"],
        input_text="",
        timeout_s=60,
        cwd=str(tmp_path),
    )
    assert os.path.realpath(result.stdout.strip()) == os.path.realpath(str(tmp_path))


# ---- salvaged from rw01: the defect, the controls, and the observability ------------------


def test_the_bare_subprocess_run_is_what_failed(tmp_path):
    """Documents the DEFECT itself, so a future 'simplification' back to ``subprocess.run``
    fails loudly rather than silently re-introducing an unbounded agent call. This is the
    highest-value test in the file: every other one here would still pass after that
    regression."""
    argv = _grandchild_launcher(tmp_path)

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(argv, input="", capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    # The overshoot lives in the CLEANUP subprocess.run does after raising, so it is measured
    # around the whole block, not around the raise.
    elapsed = time.monotonic() - started
    assert elapsed >= TIMEOUT_SECONDS, (
        "the child must actually outlive the timeout for this to test anything"
    )


def test_nonzero_exit_and_stderr_survive_the_rewrite():
    """The run -> Popen rewrite must not quietly drop either channel."""
    result = SubprocessTransport().run(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        input_text="", timeout_s=60, cwd=None)
    assert result.returncode == 3
    assert "boom" in result.stderr
    assert result.timed_out is False


def test_a_failed_spawn_is_logged_with_its_stderr(caplog):
    """The first live failure was a 44ms non-zero exit whose cause is now permanently
    unrecoverable: the ``AgentJob`` row records THAT a job failed, and nothing recorded why. A
    bounded stderr tail in the local log is the difference between a diagnosis and a forensics
    session."""
    with caplog.at_level("WARNING", logger="agent_runner.transport"):
        SubprocessTransport().run(
            [sys.executable, "-c", "import sys; sys.stderr.write('vendor said no'); sys.exit(2)"],
            input_text="", timeout_s=60, cwd=None)
    assert any("vendor said no" in r.getMessage() for r in caplog.records)


def test_a_clean_run_is_not_logged(caplog):
    """Silence on success — the log must stay readable enough that a warning MEANS something."""
    with caplog.at_level("WARNING", logger="agent_runner.transport"):
        SubprocessTransport().run([sys.executable, "-c", "pass"], input_text="",
                                  timeout_s=60, cwd=None)
    assert not [r for r in caplog.records if r.name == "agent_runner.transport"]


def test_the_logged_stderr_tail_is_bounded(caplog):
    """A CLI can echo its prompt back on a parse error, and the prompt carries untrusted feed
    text. The tail must not become a way to paste a whole packet into the log."""
    from agent_runner.transport import _STDERR_TAIL

    with caplog.at_level("WARNING", logger="agent_runner.transport"):
        SubprocessTransport().run(
            [sys.executable, "-c",
             "import sys; sys.stderr.write('x' * 50_000); sys.exit(1)"],
            input_text="", timeout_s=60, cwd=None)
    logged = [r.getMessage() for r in caplog.records if r.name == "agent_runner.transport"]
    assert logged
    # The longest RUN of x's, not the total count: the message itself contains the executable
    # name ("python.exe"), whose stray 'x' would otherwise put the total one over the bound.
    longest = max((max((len(run) for run in re.findall(r"x+", m)), default=0)) for m in logged)
    assert longest <= _STDERR_TAIL, f"stderr tail was {longest} chars, bound is {_STDERR_TAIL}"
