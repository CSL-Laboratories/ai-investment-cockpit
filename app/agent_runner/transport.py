"""WP1b — subprocess transport seam.

The runner calls a transport's ``run(argv, input_text, timeout_s, cwd) -> ProcResult``.
The default runs the real subprocess; tests inject a fake with the same signature, so
G1b is hermetic (no real ``claude``/``codex`` process).
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

# cmd.exe metacharacters. A ``.cmd``/``.bat`` target is launched THROUGH cmd.exe, which applies
# its own parsing to the command line AFTER Python's MSVCRT-style quoting — so an argv element
# carrying one of these could break out of its argument and become a command. Python's quoting
# does not escape them (this is the BatBadBut class of bug, CVE-2024-24576 in Rust's stdlib).
_CMD_METACHARACTERS = frozenset('&|<>^"')


class ExecutableNotFound(FileNotFoundError):
    """The runtime CLI is not on PATH. Named so the failure says WHICH tool is missing.

    A bare ``FileNotFoundError: [WinError 2] The system cannot find the file specified`` names
    nothing, and the operator's reasonable first guess is a missing data file, not a missing
    ``claude`` install.
    """


def resolve_executable(argv: list[str]) -> list[str]:
    """Resolve ``argv[0]`` to a real path before spawning. Windows correctness, not cosmetics.

    ``subprocess.run(["claude", ...])`` FAILS on Windows with ``FileNotFoundError`` even when
    ``claude`` is on PATH and works fine in the shell. npm installs its CLIs as ``claude.CMD``,
    and ``CreateProcess`` — unlike a shell — does not append ``.CMD``/``.BAT`` when resolving a
    bare name; it only tries ``.exe``. So the brew's first agent call died at the spawn on the
    deploy platform while every test passed, because tests inject a fake transport and never
    spawn anything. ``shutil.which`` applies PATHEXT the way the shell does, and is a no-op on
    POSIX (it returns the plain path).

    SAFETY GATE FOR ``.cmd``/``.bat`` TARGETS. Such a target runs THROUGH cmd.exe, which parses
    the command line again after Python has quoted it — and Python's quoting does not escape
    cmd metacharacters. Today nothing untrusted reaches argv (``runner.build_argv`` emits only
    config-derived values, and the prompt travels on STDIN), so this is defence in depth rather
    than a live hole. It is here because that property is easy to lose silently: the day someone
    threads a symbol, a filename or a model-supplied session id into argv, this refuses instead
    of executing it.
    """
    if not argv:
        return argv
    resolved = shutil.which(argv[0])
    if resolved is None:
        raise ExecutableNotFound(
            f"{argv[0]!r} is not on PATH. The cockpit spawns the runtime CLI as a subprocess; "
            f"install it and confirm `{argv[0]} --version` works in the SAME shell that runs "
            "the cockpit (a per-user npm install is not on a service account's PATH)."
        )
    if resolved.lower().endswith((".cmd", ".bat")):
        for arg in argv[1:]:
            bad = _CMD_METACHARACTERS.intersection(arg)
            if bad:
                raise ValueError(
                    f"refusing to spawn the cmd-shim {resolved!r} with an argument containing "
                    f"cmd metacharacter(s) {sorted(bad)!r}: a .cmd target is parsed by cmd.exe "
                    "after Python quotes it, so this could execute as a command. Pass the value "
                    "on stdin instead (that is how the prompt travels)."
                )
    return [resolved, *argv[1:]]


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SubprocessTransport:
    def run(self, argv: list[str], input_text: str, timeout_s: float, cwd: str | None) -> ProcResult:
        argv = resolve_executable(argv)
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


def _logon_launch(account, argv, input_text, timeout_s, cwd):  # pragma: no cover - Windows/logon
    """Production spawn-under-account launcher. Launches ``argv`` with the low-priv ``account``'s
    token via ``CreateProcessWithLogonW`` (credential provisioned per
    docs/setup/low-priv-agent-account.md), pipes stdio, enforces the timeout, and returns a
    ``ProcResult``. Not built on this host — raising here is the fail-closed posture: SEC-05 is
    ACTIVE only once the launcher + credential are provisioned (a W17 live-enablement precondition),
    exactly as the SEC-11 witness activates only once the Discord bot is provisioned."""
    raise NotImplementedError(
        "spawn-under-account launcher is not provisioned on this host; supply the low-priv account "
        "credential + a CreateProcessWithLogonW launcher (docs/setup/low-priv-agent-account.md)"
    )


class LowPrivSubprocessTransport:
    """SEC-05 (G11 round-1 closure) — spawn the child UNDER a separate low-privilege account so a
    decision-stage subprocess NEVER inherits the orchestrator's privileged token. The child's token
    IS the low-priv account's — the account whose ACLs ``account_guard`` verified — so an
    injected/compromised decision agent cannot read the bearer stores it was ACL-denied.

    The ``account`` attribute is the contract the runner checks: the runner refuses a decision job
    whose configured ``decision_agent_account`` does not match the transport's ``account`` (fail
    closed). The launch seam is injectable so the fail-closed wiring is testable without a second
    real Windows account; production defaults to ``CreateProcessWithLogonW`` (``_logon_launch``)."""

    def __init__(self, account: str, *, launch=None) -> None:
        if not account:
            raise ValueError("LowPrivSubprocessTransport requires a non-empty account")
        self.account = account
        self._launch = launch or _logon_launch

    def run(self, argv: list[str], input_text: str, timeout_s: float, cwd: str | None) -> ProcResult:
        return self._launch(self.account, argv, input_text, timeout_s, cwd)
