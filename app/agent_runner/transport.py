"""WP1b — subprocess transport seam.

The runner calls a transport's ``run(argv, input_text, timeout_s, cwd) -> ProcResult``.
The default runs the real subprocess; tests inject a fake with the same signature, so
G1b is hermetic (no real ``claude``/``codex`` process).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SubprocessTransport:
    def run(self, argv: list[str], input_text: str, timeout_s: float, cwd: str | None) -> ProcResult:
        try:
            p = subprocess.run(
                argv, input=input_text, capture_output=True, text=True,
                timeout=timeout_s, cwd=cwd,
            )
            return ProcResult(p.returncode, p.stdout or "", p.stderr or "", False)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            return ProcResult(124, out, err + "\n[timeout]", True)
