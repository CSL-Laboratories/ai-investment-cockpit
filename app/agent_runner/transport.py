"""WP1b — subprocess transport seam.

The runner calls a transport's ``run(argv, input_text, timeout_s, cwd) -> ProcResult``.
The default runs the real subprocess; tests inject a fake with the same signature, so
G1b is hermetic (no real ``claude``/``codex`` process).
"""
from __future__ import annotations

import inspect
import logging
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass

from agent_runner.result import mark_agent_dispatched

log = logging.getLogger(__name__)

# Bound on the stderr excerpt carried into a log line. See _log_failure.
_STDERR_TAIL = 400

REAP_GRACE_SECONDS = 5.0
KILL_GRACE_SECONDS = 10.0

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


def _kill_tree(proc: subprocess.Popen) -> None:
    """Best-effort termination of a child and every descendant it spawned."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=KILL_GRACE_SECONDS,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception as exc:  # noqa: BLE001 - timeout cleanup must remain fail-closed
        # Still best-effort, but no longer INVISIBLE. A taskkill that fails leaves a live
        # descendant holding the sandbox, and a bare `pass` made that indistinguishable from
        # a clean kill.
        log.warning("could not kill the agent process tree (pid=%s): %s: %s",
                    proc.pid, type(exc).__name__, exc)
    try:
        proc.kill()
    except Exception as exc:  # noqa: BLE001 - an already-exited child is harmless
        log.debug("agent process already gone before kill (pid=%s): %s", proc.pid, exc)


def _log_failure(argv: list[str], result: ProcResult) -> None:
    """Name a failed agent spawn in the log. Silent on success.

    The AgentJob row records THAT a job failed; only this says why. The first live failure was
    a 44ms non-zero exit whose cause is permanently unrecoverable, because nothing anywhere
    captured the child's stderr.

    The tail is BOUNDED at 400 bytes deliberately: a CLI can echo its prompt back on a parse
    error, and the prompt carries untrusted feed text. This is a local log line, never an
    operator-facing surface, and it must not become a channel for pasting a whole packet
    somewhere it will be read as trustworthy.
    """
    if not result.timed_out and result.returncode == 0:
        return
    tail = (result.stderr or "").strip()[-_STDERR_TAIL:]
    log.warning("agent subprocess %s: rc=%s timed_out=%s stderr_tail=%r",
                os.path.basename(argv[0]) if argv else "?",
                result.returncode, result.timed_out, tail)


class SubprocessTransport:
    def run(self, argv: list[str], input_text: str, timeout_s: float, cwd: str | None) -> ProcResult:
        argv = resolve_executable(argv)
        # The child must not inherit a Jev bearer left in the parent environment.
        # The credential itself lives in keyring (Inv 4), not in this env.
        child_env = os.environ.copy()
        child_env.pop("JEV_API_KEY", None)
        popen_kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "cwd": cwd,
            "env": child_env,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, **popen_kwargs)
        mark_agent_dispatched()
        try:
            out, err = proc.communicate(input_text, timeout=timeout_s)
            result = ProcResult(proc.returncode, out or "", err or "", False)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=REAP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                # The pipes are abandoned rather than waited on forever: the tree is already
                # killed, and blocking here would turn a bounded timeout into an unbounded one.
                log.warning("agent pipes did not drain within %ss after the kill; "
                            "abandoning them", REAP_GRACE_SECONDS)
                out, err = "", ""
            result = ProcResult(124, out, err + "\n[timeout]", True)
        _log_failure(argv, result)
        return result


def _logon_launch(  # pragma: no cover - Windows/logon
    account, argv, input_text, timeout_s, cwd, on_spawn=None,
):
    """Production spawn-under-account launcher. Launches ``argv`` with the low-priv ``account``'s
    token via ``CreateProcessWithLogonW`` (credential provisioned per
    docs/setup/low-priv-agent-account.md), pipes stdio, enforces the timeout, and returns a
    ``ProcResult``. Not built on this host — raising here is the fail-closed posture: SEC-05 is
    ACTIVE only once the launcher + credential are provisioned (a W17 live-enablement precondition),
    exactly as the SEC-11 witness activates only once the Discord bot is provisioned.

    ``on_spawn`` is the deep-budget dispatch mark. This stub raises before any child exists and
    must not call it. A real launcher calls ``on_spawn`` exactly once, after the child exists and
    before a later timeout or pipe error. Calling it earlier keeps an unused unit; skipping it
    after spawn lets a retry exceed the daily cap.
    """
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
        # Mark only once a launcher reports that the child exists. Passing the
        # callback before launch would keep a unit when the launcher rejects
        # the spawn. Launchers that do not accept ``on_spawn`` keep the
        # five-argument call; ``run_agent`` still marks after a successful return.
        launch = self._launch
        try:
            params = inspect.signature(launch).parameters
        except (TypeError, ValueError):
            params = {}
        on_spawn = params.get("on_spawn")
        if on_spawn is not None and on_spawn.kind is not inspect.Parameter.POSITIONAL_ONLY:
            return launch(
                self.account, argv, input_text, timeout_s, cwd,
                on_spawn=mark_agent_dispatched,
            )
        return launch(self.account, argv, input_text, timeout_s, cwd)
