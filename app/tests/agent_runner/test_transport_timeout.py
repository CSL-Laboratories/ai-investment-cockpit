"""BUG-031: ``timeout_s`` must bound the entire spawned process tree."""
from __future__ import annotations

import os
import sys
import textwrap
import time

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
