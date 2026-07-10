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
