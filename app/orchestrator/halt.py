"""WP5 — the out-of-band HALT sentinel (SEC-04).

A durable filesystem flag that the future WP7 ``OrderGateway`` checks SYNCHRONOUSLY before
every broker submit, and that the mode ladder consults before arming an auto mode. Kept in
its OWN module (no orchestrator-internal imports) so both ``killswitch.py`` and
``mode_ladder.py`` can use it without a circular import.

Path precedence: an explicit ``COCKPIT_HALT_PATH`` env override (operator-configurable, the
test-isolation seam, AND the divergence-proof PRODUCTION PIN) -> an
ACCOUNT-INDEPENDENT-BY-CONSTRUCTION, machine-wide default (Windows hardcoded
``C:/ProgramData`` / POSIX hardcoded ``/var/tmp``). The default is DELIBERATELY NOT per-user
app data AND DELIBERATELY NOT read from ANY env var: the out-of-band ``--set`` CLI runs under
the OPERATOR account while the gateway reads under the NSSM SERVICE account, so a per-user
``LOCALAPPDATA`` / ``XDG_STATE_HOME`` fallback would resolve to two DIFFERENT files (R2-S1
divergence) — and even ``%ProgramData%`` is a per-account-MUTABLE env var (an
``HKCU\\Environment`` shadow, or a ``CreateProcessWithLogonW`` service env block lacking
``CreateEnvironmentBlock``) that lets the two processes resolve DIFFERENT bases (R5-S2). The
default is therefore a FIXED LITERAL, identical across accounts BY CONSTRUCTION. PRODUCTION
SHOULD still pin ``COCKPIT_HALT_PATH`` to ONE absolute path shared by BOTH the service account
AND the operator's interactive ``--set`` process (defense-in-depth; the NSSM service + the
hotkey/file-drop trigger agree on the sentinel location). Provisioning the low-priv NSSM
account + launcher env-block is W15/OPS-06, not implemented here.

OUT-OF-BAND TRIGGER (SEC-04, W12 SEAM 5) — the sentinel is settable from a SEPARATE process::

    python -m orchestrator.halt --set [--reason R] [--path P]
    python -m orchestrator.halt --clear [--path P]

Because it runs in its own process it sets the flag INDEPENDENT of a wedged event loop: even
if the orchestrator's asyncio loop is deadlocked, the operator (or a watchdog) drops the
sentinel and the gateway — which reads it SYNCHRONOUSLY before every submit and treats an
unreadable flag as SET (fail closed) — refuses the very next broker call. This is the last
line that does not depend on any in-process code path still being responsive.

HARD STOP / NSSM SERVICE NOTE: when the cockpit runs as a Windows service under NSSM, an
``nssm stop <service>`` (or the Services console Stop) tears the whole process down — which
also drops the live MCP/broker connection at the OS level, the ultimate fail-closed. The
out-of-band ``--set`` above is the SOFTER of the two: it disarms the order path while leaving
the process up (so the audit chain keeps its watermark and the operator can inspect state).
Reach for ``nssm stop`` only when even a wedged process must be killed; prefer ``--set`` so
the shutdown stays auditable.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def default_halt_path() -> Path:
    """The configured HALT sentinel path. ``COCKPIT_HALT_PATH`` wins if set (SEC-04: the sentinel
    path is deliberately configurable AND is the divergence-proof production pin — see the module
    docstring). Otherwise fall back to an ACCOUNT-INDEPENDENT-BY-CONSTRUCTION, machine-wide location
    so the out-of-band ``--set`` process and the gateway CONVERGE no matter which OS account each
    runs under (R2-S1 / R5-S2).

    The default is a FIXED HARDCODED LITERAL, deliberately NOT derived from ANY environment variable
    — not per-user ``LOCALAPPDATA`` / ``XDG_STATE_HOME`` (which diverged between the operator's
    interactive ``--set`` and the NSSM service), and NOT ``%PROGRAMDATA%`` either. ``PROGRAMDATA``
    reads as a per-account-MUTABLE env var: an ``HKCU\\Environment`` shadow, or a
    ``CreateProcessWithLogonW`` service env block that lacks ``CreateEnvironmentBlock``, lets the
    operator's ``--set`` process and the NSSM-service gateway resolve DIFFERENT bases while
    ``COCKPIT_HALT_PATH`` is unset — the kill would write where the gateway never looks (R5-S2:
    reproduced end-to-end, broker submits after the kill). Hardcoding the literal makes the two
    processes resolve the SAME sentinel BY CONSTRUCTION, independent of the OS account's env.

    Production SHOULD still pin ``COCKPIT_HALT_PATH`` (defense-in-depth); provisioning the low-priv
    NSSM account + the launcher env-block is W15/OPS-06, not implemented here."""
    explicit = os.environ.get("COCKPIT_HALT_PATH")
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            # R6-S FAIL CLOSED: a RELATIVE pin resolves CWD-relative, so the NSSM-service gateway
            # (CWD C:/Windows/System32) and the operator's --set process (operator CWD) would
            # read/write DIFFERENT files -> the kill fails OPEN. compose()'s
            # ensure_halt_dir(default_halt_path()) triggers this at BOOT, failing closed on the
            # misconfiguration rather than letting a submit-path silently resolve a diverging sentinel.
            raise ValueError(
                "COCKPIT_HALT_PATH must be an ABSOLUTE path; a relative sentinel diverges by CWD "
                "between the gateway and the out-of-band --set process (SEC-04, R6-S)."
            )
        return p
    if os.name == "nt":
        # HARDCODED literal — NOT os.environ['PROGRAMDATA']. ProgramData is fixed at C:/ProgramData
        # on every supported Windows install; reading the env var would reintroduce the per-account
        # divergence (R5-S2). Construction-guaranteed identical for operator + service.
        base = "C:/ProgramData"
    else:
        base = "/var/tmp"
    return Path(base) / "ai-investment-cockpit" / "HALT"


def _fsync_directory(directory: Path) -> None:
    """Flush the directory entry that makes the sentinel name durable.

    The file fsync is mandatory. Opening a directory can fail on Windows, so
    that OSError is best-effort and does not undo a durable file write.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(os.fspath(directory), flags)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            return
    finally:
        os.close(fd)


def set_halt(path: Path | str, reason: str = "killswitch") -> Path:
    """Write the HALT sentinel and fsync the file and its directory.

    A crash after a plain write can drop the sentinel. The file fsync and the
    directory fsync make the name and the bytes durable. If this write fails
    and the audit block write failed too, ``admit_integrity_failure`` does not
    survive a restart and the sentinel is absent. A required safety session
    cannot record a clean close while that denial is set, so the next start
    sees an unclosed session and stays in startup hold. The process restarts
    closed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{datetime.now(timezone.utc).isoformat()} {reason}\n".encode("utf-8")
    fd = os.open(os.fspath(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        written = os.write(fd, payload)
        if written != len(payload):
            raise OSError("HALT sentinel write was short")
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)
    return path


def halt_is_set(path: Path | str) -> bool:
    """Whether the HALT sentinel is present — probed FAIL CLOSED at the PRIMITIVE so EVERY caller
    (gateway gate (a) + the pre-broker re-check AND the mode-ladder arm refusal) inherits the
    policy (R2-S2). A RAW ``os.stat`` probe, NOT ``Path.exists()``: modern ``Path.exists()``
    SWALLOWS a whole class of errnos (ENOTDIR / EBADF / WSAENOTREADY / ERROR_NOT_READY /
    ERROR_INVALID_NAME / ERROR_CANT_RESOLVE_FILENAME) and returns ``False`` WITHOUT raising, so a
    genuinely present-but-unreadable sentinel (a not-yet-ready network path, an ENOTDIR path
    component) would read as NOT SET and let an order slip past the gateway.

    CLASS-CLOSING FAIL-CLOSED INVARIANT (R3-S3): ``FileNotFoundError`` is treated as DEFINITIVELY
    absent ONLY on the TRUE not-found signal. On the deploy platform (Windows) ``os.stat`` COLLAPSES
    a whole family of DISTINCT Win32 errors onto ``FileNotFoundError`` with ``errno == 2``:
    ERROR_FILE_NOT_FOUND(2) but ALSO ERROR_PATH_NOT_FOUND(3), ERROR_NOT_READY(21),
    ERROR_BAD_NETPATH(53), a missing subst/removable drive, an ENOTDIR path component, etc. So a
    genuinely-SET sentinel on a network / removable / subst mount that transiently drops would raise
    ``FileNotFoundError`` and — under a blanket ``return False`` — read as ABSENT, flipping the
    gateway from REFUSE to SUBMIT. Only ``winerror == 2`` (ERROR_FILE_NOT_FOUND: the FINAL component
    is absent but the rest of the path is valid) is a true not-found; EVERY OTHER ``winerror``
    (3 / 21 / 53 / …) FAILS CLOSED (reads as SET). On POSIX a bare ``FileNotFoundError`` is the
    genuine ENOENT and is absent; the other errnos raise distinct ``OSError`` subclasses caught
    below. A NUL byte in the path makes ``os.stat`` raise ``ValueError`` (NOT an ``OSError``) — that
    too fails closed. Here: present -> True; DEFINITIVELY absent (true not-found) -> False; ANY OTHER
    fault (unreadable / unresolvable / dropped-mount / NUL byte) -> True (treat as SET — fail
    closed). ``ensure_halt_dir`` on the happy path guarantees a genuinely-absent sentinel yields
    ``winerror 2`` (parent present, final component absent), not ``winerror 3``."""
    try:
        os.stat(os.fspath(path))
    except FileNotFoundError as e:
        # True not-found ONLY: POSIX ENOENT, or Windows winerror 2. Every other Windows winerror
        # (a dropped/not-ready/bad-netpath mount collapsed onto errno 2) fails CLOSED (SET).
        # Inspect the exception rather than ``os.name`` so the Windows error family can be
        # simulated faithfully in cross-platform safety tests. Native POSIX ENOENT has no
        # ``winerror`` attribute and remains a definitive absence.
        winerror = getattr(e, "winerror", None)
        return winerror is not None and winerror != 2
    except ValueError:
        # A NUL byte in the path -> os.stat raises ValueError, which neither the FileNotFoundError
        # nor the OSError branch would catch. Fail closed.
        return True
    except OSError:
        return True
    return True


def ensure_halt_dir(path: Path | str) -> Path:
    """Idempotently create the sentinel's PARENT directory (``parents=True, exist_ok=True``) so the
    ``halt_is_set`` winerror-2 fail-closed invariant does NOT trip on the happy path. With the parent
    present, a genuinely-absent sentinel yields ``winerror 2`` (final component absent => not-halted);
    a DROPPED mount still yields ``winerror 3 / 21 / 53`` => fail closed. Called once by
    ``main.compose()``. Does NOT create or touch the sentinel FILE itself (byte-neutral to
    ``set_halt``/``clear_halt``)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.parent


def clear_halt(path: Path | str) -> None:
    Path(path).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Out-of-band CLI (SEC-04, W12 SEAM 5): set/clear the HALT sentinel from a SEPARATE
    process, independent of a wedged event loop. ``--path`` (else ``COCKPIT_HALT_PATH`` /
    default) selects the sentinel; the gateway reads it synchronously before every submit.

    Delegates to the byte-unchanged ``set_halt``/``clear_halt`` primitives — this adds an
    invocation surface only, no new sentinel semantics. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.halt",
        description="Out-of-band SEC-04 kill switch: set or clear the HALT sentinel.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--set", dest="do_set", action="store_true",
                       help="set the HALT sentinel (disarms the order path — fail closed)")
    group.add_argument("--clear", dest="do_clear", action="store_true",
                       help="clear the HALT sentinel (operator re-arm gate)")
    parser.add_argument("--reason", default="out-of-band killswitch",
                        help="reason recorded in the sentinel (with --set)")
    parser.add_argument("--path", default=None,
                        help="explicit sentinel path (else COCKPIT_HALT_PATH / default)")
    args = parser.parse_args(argv)

    if args.path is not None:
        path = Path(args.path)
        if not path.is_absolute():
            # R6-S: a relative --path makes this OOB process write a CWD-relative sentinel the
            # gateway (different CWD) never reads -> fail OPEN. Reject it (same guard as the
            # COCKPIT_HALT_PATH resolution).
            parser.error("--path must be an ABSOLUTE path (a relative sentinel diverges by CWD)")
    else:
        path = default_halt_path()
    if args.do_set:
        written = set_halt(path, reason=args.reason)
        print(f"HALT set: {written}", file=sys.stderr)
    else:
        clear_halt(path)
        print(f"HALT cleared: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
