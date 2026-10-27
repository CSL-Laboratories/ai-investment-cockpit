"""``python -m cockpit_spine`` : the CLI surface of the operator service.

The CLI keeps no state of its own. The log file is the hash-chained transcript,
one JSON record per line; each invocation verifies it, replays it into a
session, applies one command through ``OperatorService`` and writes the chain
back. A broken chain is refused (exit 3) and never repaired.

Exit codes: 0 done (also --help), 2 the command was refused (the refusal is
audited), 3 the log or the arguments are unusable, or the transcript lock was
not acquired before its deadline (nothing was written).

The proposal fetch and the lock each have a deadline. A fetch that misses its
deadline is recorded as a refusal fail-closed to ``watch_only``; the late result
is not applied. The lock deadline is longer than one fetch deadline, so a stop
waiting behind a hung propose still acquires the lock after that propose
fail-closes, and a lock that never comes free does not wait forever.

Stop does not need the projection registry. Sources that cite csl-research are
read from ``--vault-root`` or, when that option is omitted, from
``CSL_RESEARCH_CHECKOUT``. The CLI never fetches. When neither is set, every
command except stop exits 3 with the loader's configuration message (the same
sentence ``regen_projections`` prints). Stop still writes its record and
withholds the context figures.
"""

from __future__ import annotations

import argparse
import errno
import json
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from cockpit_spine import views
from cockpit_spine.audit import AuditError
from cockpit_spine.fake_source import FakeProposalSource
from cockpit_spine.projections import ProjectionError, Registry, load_registry
from cockpit_spine.service import API_VERSION, OperatorService
from cockpit_spine.session import FETCH_DEADLINE_SECONDS, OperatorSession, utc_now
from cockpit_spine.sources import ProposalSource

try:  # POSIX
    import fcntl
except ImportError:  # native Windows, part of the DEC-09 matrix
    fcntl = None
    import msvcrt

EXIT_OK, EXIT_REFUSED, EXIT_UNUSABLE = 0, 2, 3
# Outlasts one fetch deadline so stop can land after a hung propose fail-closes.
LOCK_DEADLINE_SECONDS = FETCH_DEADLINE_SECONDS * 3
SourceFactory = Callable[[], ProposalSource]
# macOS publishes EDEADLK and not EDEADLOCK. Reading the missing name at import
# crashes every command, including --help.
_LOCK_BUSY = frozenset({
    errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLOCK", errno.EDEADLK),
})


class LockTimeout(Exception):
    """The transcript lock was still held when its deadline passed."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cockpit_spine", description=__doc__)
    parser.add_argument("--log", required=True, type=Path, help="transcript file")
    parser.add_argument("--json", action="store_true", help="print the response as JSON")
    parser.add_argument(
        "--vault-root", type=Path, default=None,
        help="csl-research checkout (default: CSL_RESEARCH_CHECKOUT)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    opened = commands.add_parser("open")
    opened.add_argument("--session-id", required=True)
    opened.add_argument("--mode", required=True)
    commands.add_parser("status")
    commands.add_parser("audit")
    propose = commands.add_parser("propose")
    propose.add_argument("--request-id", required=True)
    propose.add_argument("--prompt", default="")
    approve = commands.add_parser("approve")
    approve.add_argument("--proposal-id", required=True)
    approve.add_argument("--operator", required=True)
    approve.add_argument("--confirm-digest", required=True)
    veto = commands.add_parser("veto")
    veto.add_argument("--proposal-id", required=True)
    veto.add_argument("--operator", required=True)
    veto.add_argument("--reason", required=True)
    stop = commands.add_parser("stop")
    stop.add_argument("--operator", required=True)
    stop.add_argument("--reason", required=True)
    return parser


def read_log(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except (OSError, ValueError):
        raise AuditError("transcript unreadable") from None


def _acquire(handle) -> None:
    """Take the lock once, or raise if another run holds it right now."""

    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


def _busy(error: OSError) -> bool:
    return isinstance(error, BlockingIOError) or error.errno in _LOCK_BUSY


def _lock(path: Path, *, deadline_seconds: float = LOCK_DEADLINE_SECONDS):
    """Exclusive lock held across read, command and write. Released when closed.

    ``fcntl.flock`` on POSIX; on Windows ``msvcrt.locking`` on the lock file's
    first byte. Both retry only until ``deadline_seconds``. A lock that never
    comes free raises ``LockTimeout`` instead of waiting out the operator.
    """

    handle = path.with_name(path.name + ".lock").open("a+")
    deadline = time.monotonic() + max(0.0, deadline_seconds)
    try:
        while True:
            try:
                _acquire(handle)
                return handle
            except OSError as error:
                if not _busy(error):
                    raise
            if time.monotonic() >= deadline:
                raise LockTimeout
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.05, remaining))
    except BaseException:
        handle.close()
        raise


def _unlock(handle) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def write_log(path: Path, records: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _projection_failure(error: ProjectionError) -> str:
    """Exit-3 text. A missing checkout names the same fix as regen_projections."""

    text = str(error)
    if text.startswith("csl-research checkout"):
        return f"unusable: {text}\n"
    return "unusable: ProjectionError\n"


def _registry_for(
    command: str, vault_root: Path | None, registry_path: Path | None,
) -> Registry:
    """Verified registry. Stop still runs when the registry cannot be loaded.

    ``vault_root`` wins over ``CSL_RESEARCH_CHECKOUT``. When both are absent the
    loader raises, and only stop continues. An empty registry verifies nothing,
    so the view withholds its figures instead of refusing the stop.
    ``registry_path`` defaults to the packaged registry. Tests pass a registry
    they built for a temporary vault.
    """

    try:
        if registry_path is None:
            loaded = load_registry(vault_root=vault_root)
        else:
            loaded = load_registry(registry_path, vault_root=vault_root)
    except ProjectionError:
        if command != "stop":
            raise
        return Registry({}, "")
    return loaded


def _args_for(namespace: argparse.Namespace) -> dict[str, str]:
    mapping = {
        "propose": ("request_id", "prompt"),
        "approve": ("proposal_id", "operator", "confirm_digest"),
        "veto": ("proposal_id", "operator", "reason"),
        "stop": ("operator", "reason"),
    }
    return {key: getattr(namespace, key) for key in mapping.get(namespace.command, ())}


def main(
    argv: list[str],
    *,
    source_factory: SourceFactory = FakeProposalSource,
    out: TextIO | None = None,
    clock: Callable[[], str] = utc_now,
    fetch_deadline_seconds: float = FETCH_DEADLINE_SECONDS,
    lock_deadline_seconds: float = LOCK_DEADLINE_SECONDS,
    registry_path: Path | None = None,
) -> int:
    out = out or sys.stdout
    try:
        namespace = build_parser().parse_args(argv)
    except SystemExit as stop:
        return EXIT_OK if stop.code in (0, None) else EXIT_UNUSABLE
    lock = None
    try:
        lock = _lock(namespace.log, deadline_seconds=lock_deadline_seconds)
        registry = _registry_for(namespace.command, namespace.vault_root, registry_path)
        source = source_factory()
        if namespace.command == "open":
            if namespace.log.exists():
                raise AuditError("transcript already exists")
            session = OperatorSession.open(
                session_id=namespace.session_id, mode=namespace.mode, source=source, clock=clock,
                fetch_deadline_seconds=fetch_deadline_seconds)
            request: dict[str, Any] = {"api": API_VERSION, "command": "status", "args": {}}
        else:
            session = OperatorSession.replay(
                read_log(namespace.log), source=source, clock=clock,
                fetch_deadline_seconds=fetch_deadline_seconds)
            request = {"api": API_VERSION, "command": namespace.command,
                       "args": _args_for(namespace)}
        response = OperatorService(session, registry).handle(request)
        if namespace.command != "audit":
            write_log(namespace.log, session.records())
    except LockTimeout:
        out.write("unusable: lock_timeout\n")
        return EXIT_UNUSABLE
    except ProjectionError as error:
        out.write(_projection_failure(error))
        return EXIT_UNUSABLE
    except (AuditError, ValueError) as error:
        out.write(f"unusable: {type(error).__name__}\n")
        return EXIT_UNUSABLE
    except OSError:
        out.write("unusable: OSError\n")
        return EXIT_UNUSABLE
    finally:
        if lock is not None:
            _unlock(lock)
    if namespace.json:
        out.write(json.dumps(response, sort_keys=True) + "\n")
    else:
        lines = [views.Line(**item) for item in response["lines"]]
        out.write(f"{'ok' if response['ok'] else 'refused'}: {response['code']}\n")
        out.write(views.render_text(lines))
    return EXIT_OK if response["ok"] else EXIT_REFUSED
