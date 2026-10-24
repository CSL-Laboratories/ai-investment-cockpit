#!/usr/bin/python3 -I
"""Launcher child-env and fd-2 capture policy (CSL-450 D10 E3-2 / E1 channel S).

D10's production launcher is a statically linked native executable (no shebang,
no Python) that forks, ``dup2``s the sink onto the child's fd 2, and
``fexecve``s the hashed CLI. This module is the reviewed policy that binary
must implement:

- Refuse (do not exec) if any U-15 name appears in the parent environment
  (``SRT_DEBUG``, ``DEBUG_CLAUDE_AGENT_SDK``, ``ACTIONS_STEP_DEBUG``), even
  empty or ``false``. Also refuse a truthy ``RUNNER_DEBUG``.
- Drop those names and do not forward child stderr to the parent/SDK.
- Force ``SRT_DEBUG=1`` only in the child environment after the refuse check.
- Child stdout is copied byte-exactly to the parent; fd 2 is the capture sink.

E4 stays parked: this policy does not bind first-Bash or treat ``first_bash``
as §1.7 clause 3.
"""
from __future__ import annotations

from typing import Mapping, MutableMapping, Optional, Tuple

TRUTHY = frozenset({"1", "true", "yes", "on"})
# U-15: refuse if any of these three *appear* in the parent env. SRT_DEBUG is
# still forced in the child after this check passes (name absent).
U15_NAMES = ("SRT_DEBUG", "DEBUG_CLAUDE_AGENT_SDK", "ACTIONS_STEP_DEBUG")
REFUSE_NAMES = U15_NAMES + ("RUNNER_DEBUG",)
DROP_ALWAYS = REFUSE_NAMES + (
    "SRT_DEBUG",
    "DEBUG",
    "NODE_OPTIONS",
    "BASH_ENV",
    "ENV",
    "LD_PRELOAD",
    "LD_AUDIT",
    "GITHUB_ENV",
    "GITHUB_PATH",
    "GITHUB_OUTPUT",
    "GITHUB_STATE",
    "GITHUB_STEP_SUMMARY",
)
FORCED_CHILD = {
    "SRT_DEBUG": "1",
    "DISABLE_UPDATES": "1",
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TERM": "dumb",
}
KEEP_IF_PRESENT = (
    "TZ",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
    "CLAUDE_CODE_ENTRYPOINT",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "RUNNER_TEMP",
    "RUNNER_OS",
    "CI",
    "GITHUB_ACTIONS",
)
# Enumerated CLAUDE_CODE_* only (N5). Anything else is dropped.
KEEP_CLAUDE_CODE = frozenset(
    {
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
        "CLAUDE_CODE_ENTRYPOINT",
    }
)

CLAUDE_PATH = "/opt/csl-claude/2.1.306/claude"
CAPTURE_PATH = "/var/lib/csl-relay/capture"


def _is_truthy(value: Optional[str]) -> bool:
    if value is None or value == "":
        return False
    return value.strip().lower() in TRUTHY


def refused_debug(env: Mapping[str, str]) -> Optional[str]:
    """Return the refused name, or None if the launcher may continue.

    U-15 names refuse on presence (any value). ``RUNNER_DEBUG`` refuses if truthy.
    """
    for name in U15_NAMES:
        if name in env:
            return name
    if _is_truthy(env.get("RUNNER_DEBUG")):
        return "RUNNER_DEBUG"
    return None


def child_env(received: Mapping[str, str], *, home: str, user: str) -> MutableMapping[str, str]:
    """Build the child environment from an explicit allowlist. Nothing inherited by default."""
    refused = refused_debug(received)
    if refused:
        raise RuntimeError(refused)
    if received.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB") != "1":
        raise RuntimeError("scrub")
    out: MutableMapping[str, str] = {}
    out.update(FORCED_CHILD)
    out["HOME"] = home
    out["USER"] = user
    out["LOGNAME"] = user
    for name in KEEP_IF_PRESENT:
        if name in received and name not in DROP_ALWAYS:
            out[name] = received[name]
    for name, value in received.items():
        if name.startswith("GITHUB_") and name not in DROP_ALWAYS:
            out.setdefault(name, value)
    # Drop every other CLAUDE_CODE_* name, documented or not (N5).
    for name in list(out):
        if name.startswith("CLAUDE_CODE_") and name not in KEEP_CLAUDE_CODE:
            del out[name]
            continue
        if name in DROP_ALWAYS and name != "SRT_DEBUG":
            del out[name]
    out["SRT_DEBUG"] = "1"
    out["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
    return out


def parent_must_not_forward_stderr() -> bool:
    """SDK ``options.stderr`` and ``DEBUG_CLAUDE_AGENT_SDK`` stay off (channel S)."""
    return True


def fd_map() -> Tuple[int, int, int]:
    """Child stdio: fd 0 inherited, fd 1 stdout pipe to parent, fd 2 capture sink."""
    return (0, 1, 2)


def main(argv: Tuple[str, ...] | list[str]) -> int:
    """fexecve stand-in: refuse U-15, child-only SRT_DEBUG, fd 2 = FIFO."""
    import os
    import sys

    refused = refused_debug(os.environ)
    if refused:
        sys.stderr.write("launcher refused\n")
        return 1
    env = child_env(os.environ, home="/home/runner", user="runner")
    fifo = os.environ.get("CSL_RELAY_FIFO", "/var/lib/csl-relay/stderr.fifo")
    capture = os.environ.get("CSL_RELAY_CAPTURE", CAPTURE_PATH)
    cap = 1 * 1024 * 1024
    os.makedirs(os.path.dirname(capture), exist_ok=True)
    if not os.path.exists(fifo):
        os.mkfifo(fifo, 0o600)
        os.chmod(fifo, 0o600)
    reader = os.fork()
    if reader == 0:
        read_fd = os.open(fifo, os.O_RDONLY)
        written = 0
        with open(capture, "wb") as out:
            while True:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                if written >= cap:
                    continue
                piece = chunk[: cap - written]
                out.write(piece)
                written += len(piece)
        os.close(read_fd)
        os._exit(0)
    write_fd = os.open(fifo, os.O_WRONLY)
    pid = os.fork()
    if pid == 0:
        os.dup2(write_fd, 2)
        if write_fd != 2:
            os.close(write_fd)
        os.execve(CLAUDE_PATH, [CLAUDE_PATH, *list(argv[1:])], dict(env))
    os.close(write_fd)
    os.waitpid(pid, 0)
    os.waitpid(reader, 0)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv))
