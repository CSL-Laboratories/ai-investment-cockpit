"""``python -m cockpit_spine`` : the CLI surface of the operator service.

The CLI keeps no state of its own. The log file is the hash-chained transcript,
one JSON record per line; each invocation verifies it, replays it into a
session, applies one command through ``OperatorService`` and writes the chain
back. A broken chain is refused (exit 3) and never repaired.

Exit codes: 0 done (also --help), 2 the command was refused (the refusal is
audited), 3 the log or the arguments are unusable.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from cockpit_spine import views
from cockpit_spine.audit import AuditError
from cockpit_spine.fake_source import FakeProposalSource
from cockpit_spine.projections import ProjectionError, load_registry
from cockpit_spine.service import API_VERSION, OperatorService
from cockpit_spine.session import OperatorSession, utc_now
from cockpit_spine.sources import ProposalSource

EXIT_OK, EXIT_REFUSED, EXIT_UNUSABLE = 0, 2, 3
SourceFactory = Callable[[], ProposalSource]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cockpit_spine", description=__doc__)
    parser.add_argument("--log", required=True, type=Path, help="transcript file")
    parser.add_argument("--json", action="store_true", help="print the response as JSON")
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


def write_log(path: Path, records: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


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
) -> int:
    out = out or sys.stdout
    try:
        namespace = build_parser().parse_args(argv)
    except SystemExit as stop:
        return EXIT_OK if stop.code in (0, None) else EXIT_UNUSABLE
    try:
        registry = load_registry()
        source = source_factory()
        if namespace.command == "open":
            if namespace.log.exists():
                raise AuditError("transcript already exists")
            session = OperatorSession.open(
                session_id=namespace.session_id, mode=namespace.mode, source=source, clock=clock)
            request: dict[str, Any] = {"api": API_VERSION, "command": "status", "args": {}}
        else:
            session = OperatorSession.replay(read_log(namespace.log), source=source, clock=clock)
            request = {"api": API_VERSION, "command": namespace.command,
                       "args": _args_for(namespace)}
    except (AuditError, ProjectionError, ValueError) as error:
        out.write(f"unusable: {type(error).__name__}\n")
        return EXIT_UNUSABLE
    response = OperatorService(session, registry).handle(request)
    if namespace.command != "audit":
        write_log(namespace.log, session.records())
    if namespace.json:
        out.write(json.dumps(response, sort_keys=True) + "\n")
    else:
        lines = [views.Line(**item) for item in response["lines"]]
        out.write(f"{'ok' if response['ok'] else 'refused'}: {response['code']}\n")
        out.write(views.render_text(lines))
    return EXIT_OK if response["ok"] else EXIT_REFUSED
