"""Check a recorded operator session without re-running it.

A reviewer who has no Recursion checkout can still prove the committed record
is whole. The chain verifies and replays under the session rules, and the step
transcript is bound to that chain: every step's head must be a chain hash, heads
may only move forward, and the chain records a step added (none for status and
audit, one or two for a command) must be exactly the event its command, arguments,
``ok`` and ``code`` claim. A refusal is replayed at the recorded timestamp, and
the ``command_refused`` data it writes must match the recorded event. The
response the operator got is rebuilt from the replayed session at that head,
using the same ``OperatorService`` rendering the CLI uses, and must match
exactly (``api``, ``ok``, ``code``, ``head``, ``withheld``, ``lines``, and no
extra keys). The steps must cover the whole
chain, every displayed number in
every step is a verified published-tier link, and approve, veto and stop each
happened. Malformed input is reported as a problem, never raised.
"""

from __future__ import annotations

import contextlib
import io
from typing import Any

from cockpit_spine import cli, views
from cockpit_spine.audit import AuditError, verify_chain
from cockpit_spine.projections import Registry
from cockpit_spine.service import OperatorService
from cockpit_spine.session import CommandResult, OperatorSession, _clean
from cockpit_spine.sources import ProposalSource
from successor.route_consumer import pin
from successor.route_consumer.records import prompt_digest

REQUIRED_CODES = ("approved", "vetoed", "stopped")
EXPECTED_KIND = "operator session transcript; not a model comparison; no CP7 result; no verdict"
EXPECTED_LABEL = "live route integration deferred; recorded on the fake driver; no verdict"
EXIT_OK, EXIT_REFUSED = 0, 2
ADAPTER_KIND = "recursion-fake-driver"


class _NoSource:
    adapter_kind = "none"

    def fetch(self, **_: Any) -> Any:
        raise AssertionError("a replay never contacts a source")


def _parsed(argv: list[str]) -> Any:
    """The arguments the CLI would receive for ``argv``, or ``None`` if it would not run.

    Parsing goes through the CLI's own parser, so a repeated flag means what it
    means to argparse (the last one wins) and an unknown flag is refused.
    """

    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return cli.build_parser().parse_args(["--log", "-", *argv])
    except SystemExit:
        return None


def _flag(argv: list[str], name: str) -> str | None:
    namespace = _parsed(argv)
    return None if namespace is None else getattr(namespace, name[2:].replace("-", "_"), None)


def _expected_code(reason: object) -> str:
    return views.code("result", reason).value


def _check_step(
    command: str, argv: list[str], response: dict[str, Any], segment: list[dict[str, Any]],
) -> list[str]:
    """What the chain says this command did, against what the step claims it did."""

    found: list[str] = []
    kinds = [r["kind"] for r in segment]
    data = [r["data"] for r in segment]
    ok, code = response.get("ok"), response.get("code")

    def claim(expected_ok: bool, expected_code: str, why: str) -> None:
        if ok is not expected_ok or code != expected_code:
            found.append(f"{why}: claims ok={ok!r} code={code!r}, chain says "
                         f"ok={expected_ok!r} code={expected_code!r}")

    def refused(actor: str) -> None:
        if data[0].get("command") != actor:
            found.append(f"{actor}: refusal record is for {data[0].get('command')!r}")
        claim(False, _expected_code(data[0].get("code")), f"{actor} refusal")

    def same(label: str, flag: str, recorded: object, index: int = 0) -> None:
        if _as_stored(flag, _flag(argv, flag)) != recorded:
            found.append(f"{command}: {label} differs from the chain record {index}")

    if command in ("status", "audit"):
        if kinds:
            found.append(f"{command}: wrote chain records {kinds}")
        claim(True, command, command)
    elif command == "open":
        if kinds != ["session_opened"]:
            found.append(f"open: chain shows {kinds}")
        else:
            same("session id", "--session-id", data[0].get("session_id"))
            same("mode", "--mode", data[0].get("mode"))
            claim(True, "status", "open")
    elif command == "propose":
        if kinds == ["command_refused"]:
            refused("propose")
        elif kinds in (["proposal_requested", "proposal_received"],
                       ["proposal_requested", "proposal_refused"]):
            same("request id", "--request-id", data[0].get("request_id"))
            prompt = _flag(argv, "--prompt")
            if prompt_digest("" if prompt is None else prompt) != data[0].get("prompt_digest"):
                found.append(f"{command}: prompt differs from the chain record")
            if data[1].get("request_id") != data[0].get("request_id"):
                found.append("propose: outcome answers a different request")
            if kinds[1] == "proposal_received":
                claim(True, "proposal_received", "propose")
            else:
                claim(False, _expected_code(data[1].get("reason")), "propose refusal")
        else:
            found.append(f"propose: chain shows {kinds}")
    elif command in ("approve", "veto", "stop"):
        accepted = {"approve": "proposal_approved", "veto": "proposal_vetoed",
                    "stop": "session_stopped"}[command]
        done = {"approve": "approved", "veto": "vetoed", "stop": "stopped"}[command]
        if kinds == ["command_refused"]:
            refused(command)
        elif kinds == [accepted]:
            same("operator", "--operator", data[0].get("operator"))
            if command != "stop":
                same("proposal id", "--proposal-id", data[0].get("proposal_id"))
            if command == "approve":
                same("confirm digest", "--confirm-digest", data[0].get("payload_digest"))
            else:
                same("reason", "--reason", data[0].get("reason"))
            claim(True, done, command)
        else:
            found.append(f"{command}: chain shows {kinds}")
    else:
        found.append(f"unknown command {command!r}")
    return found


def _as_stored(flag: str, supplied: str | None) -> object:
    """The value the session would store, so surrounding space is not damage."""

    if not isinstance(supplied, str):
        return supplied
    if flag in ("--operator", "--session-id"):
        return supplied.strip()
    if flag == "--reason":
        return _clean(supplied)
    if flag == "--request-id":
        return _clean(supplied, limit=64)
    return supplied


def _invoke(session: OperatorSession, command: str, argv: list[str]) -> CommandResult | None:
    """Re-run one command. ``None`` means the argv could not name its arguments."""

    def flag(name: str) -> str | None:
        return _flag(argv, name)

    if command == "propose":
        request_id = flag("--request-id")
        if request_id is None:
            return None
        prompt = flag("--prompt")
        return session.request_proposal(
            request_id=request_id, prompt="" if prompt is None else prompt)
    if command == "approve":
        proposal_id, operator, digest = (
            flag("--proposal-id"), flag("--operator"), flag("--confirm-digest"))
        if None in (proposal_id, operator, digest):
            return None
        return session.approve(
            proposal_id=proposal_id, operator=operator, confirm_digest=digest)
    if command == "veto":
        proposal_id, operator, reason = flag("--proposal-id"), flag("--operator"), flag("--reason")
        if None in (proposal_id, operator, reason):
            return None
        return session.veto(proposal_id=proposal_id, operator=operator, reason=reason)
    if command == "stop":
        operator, reason = flag("--operator"), flag("--reason")
        if None in (operator, reason):
            return None
        return session.stop(operator=operator, reason=reason)
    return None


def _check_refusal(
    command: str, argv: list[str], prior: list[dict[str, Any]], refusal: dict[str, Any],
) -> list[str]:
    """A refusal must be the ``command_refused`` event these arguments write."""

    if command not in ("propose", "approve", "veto", "stop"):
        return []
    if not prior:
        return [f"{command}: refusal has no session to check"]
    stamp = refusal.get("at")
    if not isinstance(stamp, str):
        return [f"{command}: refusal record differs from the one these arguments write"]
    try:
        session = OperatorSession.replay(
            prior, source=_NoSource(), clock=lambda stamp=stamp: stamp)
        before = len(session.records())
        result = _invoke(session, command, argv)
    except (AuditError, TypeError, ValueError) as error:
        return [f"{command}: cannot re-check the refusal ({type(error).__name__})"]
    if result is None:
        return [f"{command}: refusal arguments are incomplete"]
    if result.ok or result.code != refusal.get("code"):
        return [f"{command}: arguments would not be refused as {refusal.get('code')!r}"]
    emitted = session.records()[before:]
    if (len(emitted) != 1 or emitted[0].get("kind") != "command_refused"
            or emitted[0].get("data") != refusal):
        return [f"{command}: refusal record differs from the one these arguments write"]
    return []


def _view_lines(
    session: OperatorSession, registry: Registry, command: str, segment: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The response the CLI would have shown, rebuilt from the replayed session."""

    svc = OperatorService(session, registry)
    kinds = [record["kind"] for record in segment]
    data = [record["data"] for record in segment]

    def show(ok: bool, code: object, extra: list[views.Line] | None = None) -> dict[str, Any]:
        shown = views.code("result", code)
        lines = [shown, *(extra or []), *svc._status_lines()]
        return svc._reply(ok, shown.value, lines)

    if command == "status":
        return svc._reply(True, "status", svc._status_lines())
    if command == "audit":
        return svc._reply(True, "audit", svc._audit_lines())
    if command == "open":
        return svc._reply(True, "status", svc._status_lines())
    if kinds == ["command_refused"]:
        return show(False, data[0].get("code"))
    if command == "propose" and kinds == ["proposal_requested", "proposal_received"]:
        return show(True, "proposal_received")
    if command == "propose" and kinds == ["proposal_requested", "proposal_refused"]:
        return show(False, data[1].get("reason"))
    if command == "approve" and kinds == ["proposal_approved"]:
        record = segment[0]
        extra = [
            views.Line("text", "authority", "none, inert record"),
            views.Line("id", "approved intent", str(record["data"].get("proposal_id"))),
            views.Line("digest", "audit hash", record["hash"]),
        ]
        return show(True, "approved", extra)
    if command == "veto" and kinds == ["proposal_vetoed"]:
        return show(True, "vetoed")
    if command == "stop" and kinds == ["session_stopped"]:
        return show(True, "stopped")
    return None


def _check_steps(
    steps: object, chain: list[dict[str, Any]], head: str, registry: Registry,
) -> list[str]:
    if not isinstance(steps, list) or not steps:
        return ["steps are missing or not a list"]
    found: list[str] = []
    index_of = {r["hash"]: i for i, r in enumerate(chain)}
    cursor = -1
    codes: list[object] = []
    refusals: list[object] = []
    for number, step in enumerate(steps):
        where = f"step {number}"
        response = step.get("response") if isinstance(step, dict) else None
        argv = step.get("argv") if isinstance(step, dict) else None
        if (not isinstance(response, dict) or not isinstance(argv, list) or not argv
                or not all(isinstance(a, str) for a in argv)):
            found.append(f"{where}: argv or response is missing or malformed")
            continue
        namespace = _parsed(argv)
        if namespace is None or namespace.command != argv[0]:
            found.append(f"{where}: argv is not a command the CLI accepts")
            continue
        ok, step_head = response.get("ok"), response.get("head")
        if not isinstance(ok, bool):
            found.append(f"{where}: ok is not a boolean")
            continue
        exit_code = step.get("exit_code")
        if type(exit_code) is not int or exit_code != (EXIT_OK if ok else EXIT_REFUSED):
            found.append(f"{where}: exit code does not match the response")
        lines = response.get("lines")
        try:
            views.validate_lines([views.Line(**i) for i in lines], registry)
        except (views.ViewError, TypeError):
            found.append(f"{where}: lines are malformed or carry an unlinked number")
        else:
            shown = [i["value"] for i in lines if i.get("label") == "chain head"]
            if any(value != step_head for value in shown):
                found.append(f"{where}: chain head line differs from the response head")
        if not isinstance(step_head, str) or step_head not in index_of:
            found.append(f"{where}: head is not in the chain")
            continue
        end = index_of[step_head]
        if end < cursor:
            found.append(f"{where}: head moves backwards in the chain")
            continue
        previous = cursor
        segment = chain[cursor + 1:end + 1]
        cursor = end
        found += [f"{where}: {m}" for m in _check_step(argv[0], argv, response, segment)]
        if len(segment) == 1 and segment[0].get("kind") == "command_refused":
            prior = chain[: previous + 1] if previous >= 0 else []
            found += [f"{where}: {m}" for m in _check_refusal(
                argv[0], argv, prior, segment[0].get("data") or {})]
        try:
            snap = OperatorSession.replay(chain[: end + 1], source=_NoSource())
            expected = _view_lines(snap, registry, argv[0], segment)
        except (AuditError, TypeError, KeyError, AttributeError, ValueError) as error:
            found.append(f"{where}: cannot rebuild the view ({type(error).__name__})")
        else:
            if expected is None:
                found.append(f"{where}: no rendered view for this command")
            elif response != expected:
                found.append(f"{where}: response differs from the replayed view")
        (codes if ok else refusals).append(response.get("code"))
    if cursor != len(chain) - 1 or head != chain[-1]["hash"]:
        found.append("the steps do not cover the whole chain")
    for needed in REQUIRED_CODES:
        if needed not in codes:
            found.append(f"no successful {needed} step")
    if "session_stopped" not in refusals:
        found.append("no refusal after stop was recorded")
    return found


def _provenance_problems(record: dict[str, Any]) -> list[str]:
    """The driver and contract the record names must be the fake driver and the committed pin."""

    found: list[str] = []
    chain = record.get("chain")
    first = chain[0] if isinstance(chain, list) and chain and isinstance(chain[0], dict) else {}
    opened = first.get("data") if isinstance(first.get("data"), dict) else {}
    if record.get("adapter_kind") != ADAPTER_KIND or opened.get("adapter_kind") != ADAPTER_KIND:
        found.append(f"adapter kind is not {ADAPTER_KIND!r} in the record and its opening event")
    pinned = pin.load_pin()
    expected = {
        "repository": pinned["source"]["repository"],
        "pull_request": pinned["source"]["pull_request"],
        "head_sha": pinned["source"]["head_sha"],
        "schema_sha256": pinned["vendored"][pin.SCHEMA_KEY]["sha256"],
    }
    if record.get("contract") != expected:
        found.append("contract identity differs from the committed route contract pin")
    return found


def problems(record: dict[str, Any], registry: Registry) -> list[str]:
    if not isinstance(record, dict):
        return ["record is not an object"]
    found: list[str] = []
    if record.get("platform") != "linux":
        found.append("not recorded as a linux session")
    if record.get("live_calls") is not False:
        found.append("live_calls is not false")
    if record.get("kind") != EXPECTED_KIND:
        found.append("record kind is not the expected no-verdict kind")
    if record.get("label") != EXPECTED_LABEL:
        found.append("record does not carry the expected deferral label")
    found += _provenance_problems(record)
    if record.get("projection_registry_digest") != registry.digest:
        found.append("projection registry digest differs from the committed registry")
    chain = record.get("chain")
    try:
        head = verify_chain(chain)
    except (AuditError, TypeError, KeyError, AttributeError) as error:
        return found + [f"chain: {error}"]
    if record.get("head") != head:
        found.append("recorded head is not the chain head")
    source: ProposalSource = _NoSource()
    try:
        session = OperatorSession.replay(chain, source=source)
    except AuditError as error:
        return found + [f"replay: {error}"]
    states = {p["proposal_id"]: p["state"] for p in session.proposals()}
    if not {"approved", "vetoed", "abandoned"} <= set(states.values()):
        found.append("final states do not include approved, vetoed and abandoned")
    try:
        found += _check_steps(record.get("steps"), chain, head, registry)
    except (TypeError, KeyError, AttributeError, ValueError) as error:
        found.append(f"steps: malformed ({type(error).__name__})")
    return found
