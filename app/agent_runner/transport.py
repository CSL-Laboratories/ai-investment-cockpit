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
from collections.abc import Mapping
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

# CSL-371: the agent CLI's environment is an ALLOWLIST, not the parent's environment minus a
# denylist. ``os.environ.copy()`` handed every secret in the operator's shell (GITHUB_TOKEN,
# OPENAI_API_KEY, a bot token, cloud creds, whatever a developer exported) to a third-party CLI,
# where it can reach telemetry, logs or a tool the CLI was granted. Both supported runtimes
# authenticate from their own on-disk login stores under the user profile (~/.claude,
# ~/.claude.json, ~/.codex/auth.json; see docs/setup/low-priv-agent-account.md), so neither needs
# a credential in env. A new variable reaches the child only by being named here.
#
# Matching follows the platform. Windows environment names are case-insensitive, so there a name
# is compared upper-cased. POSIX names are case-sensitive, so there a name must match exactly:
# ``home`` or ``path`` is a different variable from ``HOME`` or ``PATH`` and is dropped (Codex,
# #240). The conventional lower-case proxy spellings are named in _POSIX_LOWERCASE_NAMES.
_CHILD_ENV_ALLOWLIST = frozenset({
    # Process launch and executable lookup.
    "PATH", "PATHEXT", "COMSPEC", "SHELL",
    # Windows system locations. Python and Node both fail in odd ways without SYSTEMROOT.
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    # Profile and per-user state. The CLIs find their login stores through these.
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
    "USER", "USERNAME", "LOGNAME", "USERDOMAIN",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    # Where each runtime keeps its config, when the operator relocated it.
    "CLAUDE_CONFIG_DIR", "CODEX_HOME",
    # Scratch space.
    "TEMP", "TMP", "TMPDIR",
    # Locale, time zone and terminal. The LC_* names are the exact POSIX locale categories plus
    # glibc's extra ones, not an LC_ prefix: a prefix let any LC_-named variable through,
    # including a secret such as LC_INTERNAL_BEARER (Devin, #240).
    "LANG", "LANGUAGE", "TZ", "TERM", "NO_COLOR",
    "LC_ALL", "LC_COLLATE", "LC_CTYPE", "LC_MESSAGES", "LC_MONETARY", "LC_NUMERIC", "LC_TIME",
    "LC_ADDRESS", "LC_IDENTIFICATION", "LC_MEASUREMENT", "LC_NAME", "LC_PAPER", "LC_TELEPHONE",
    # Network reachability behind a proxy or a corporate CA. Values are passed only when they
    # carry no userinfo (see _proxy_has_credentials).
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
})
_PROXY_NAMES = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"})
# POSIX tools conventionally read lower-case proxy names, so these exact spellings are allowed too.
_POSIX_LOWERCASE_NAMES = frozenset({"http_proxy", "https_proxy", "all_proxy", "no_proxy"})
_CASE_INSENSITIVE_ENV = os.name == "nt"

# Belt and braces: a name that LOOKS like a secret never reaches the child, even if a future edit
# adds it to the allowlist above. JEV_API_KEY is named explicitly as well (the original strip).
# Substring markers, matched anywhere in the upper-cased name. CREDENTIAL also covers CREDENTIALS,
# AUTH covers OAUTH/AUTHORIZATION, SESSION covers SESSIONID, PRIVATE covers PRIVATE_KEY.
_SECRET_NAME_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "PASSPHRASE", "CREDENTIAL",
                        "API_KEY", "APIKEY", "AUTH", "BEARER", "COOKIE", "SESSION", "PRIVATE",
                        "SIGNING", "JWT")
_SECRET_NAME_SUFFIXES = ("_KEY", "_PAT", "_PWD", "_PASS", "_DSN")
_SECRET_NAME_PREFIXES = ("GH_", "GITHUB_", "JEV_", "AWS_", "AZURE_", "GOOGLE_", "GCP_")


def _looks_secret(upper_name: str) -> bool:
    return (
        upper_name == "JEV_API_KEY"
        or upper_name.endswith(_SECRET_NAME_SUFFIXES)
        or upper_name.startswith(_SECRET_NAME_PREFIXES)
        or any(marker in upper_name for marker in _SECRET_NAME_MARKERS)
    )


def _proxy_has_credentials(value: str) -> bool:
    """``http://user:pass@proxy:8080`` carries a credential in the URL. Drop it rather than parse
    it: a proxy that needs auth must be configured for the CLI by the operator directly."""
    head = value.split("://", 1)[-1].split("/", 1)[0]
    return "@" in head


def build_child_env(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment an agent CLI child is spawned with: allowlisted names only.

    ``parent`` defaults to ``os.environ``. Names match case-insensitively on Windows and exactly
    on POSIX. Original key spelling is preserved, so a lower-case POSIX ``https_proxy`` stays
    lower-case for the child.
    """
    source = os.environ if parent is None else parent
    env: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if _CASE_INSENSITIVE_ENV:
            if upper not in _CHILD_ENV_ALLOWLIST:
                continue
        elif name not in _CHILD_ENV_ALLOWLIST and name not in _POSIX_LOWERCASE_NAMES:
            continue
        if _looks_secret(upper):
            continue
        if upper in _PROXY_NAMES and _proxy_has_credentials(value):
            continue
        env[name] = value
    return env


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
        # CSL-371: an explicit allowlist, not the parent environment minus a denylist. The child
        # must not inherit a Jev bearer or any other secret left in the parent environment; the
        # cockpit's own credentials live in keyring (Inv 4), not in this env.
        child_env = build_child_env()
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


def _launch_accepts_on_spawn(launch) -> bool:
    """True when ``launch(..., on_spawn=...)`` will not raise ``TypeError``.

    A named ``on_spawn`` parameter accepts the keyword. ``**kwargs`` does too
    only when a ``functools.wraps`` target names it. A bare ``**kwargs``
    forwarder has no such proof and keeps the five-argument call, as does a
    five-argument wrapper.
    """
    seen: set[int] = set()
    current = launch
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        try:
            params = inspect.signature(current, follow_wrapped=False).parameters
        except (TypeError, ValueError):
            return False
        named = params.get("on_spawn")
        if named is not None:
            return named.kind is not inspect.Parameter.POSITIONAL_ONLY
        if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return False
        current = getattr(current, "__wrapped__", None)
    return False


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
        # the spawn. Launchers that cannot accept ``on_spawn`` keep the
        # five-argument call; ``run_agent`` still marks after a successful return.
        launch = self._launch
        if _launch_accepts_on_spawn(launch):
            return launch(
                self.account, argv, input_text, timeout_s, cwd,
                on_spawn=mark_agent_dispatched,
            )
        return launch(self.account, argv, input_text, timeout_s, cwd)
