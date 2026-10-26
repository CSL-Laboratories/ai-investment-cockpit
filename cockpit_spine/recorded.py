"""Check a recorded operator session without re-running it.

A reviewer who has no Recursion checkout can still prove the committed record
is whole. The chain verifies and replays under the session rules, and the step
transcript is bound to that chain: every step's head must be a chain hash, heads
may only move forward, and the chain records a step added (none for status and
audit, one or two for a command) must be exactly the event its command, arguments,
``ok`` and ``code`` claim. The lines the operator saw are rebuilt from the
replayed session at that head, using the same ``OperatorService`` rendering the
CLI uses, and must match exactly (result, state, mode, authority, and anything
added later). The steps must cover the whole chain, every displayed number in
every step is a verified published-tier link, and approve, veto and stop each
happened. Malformed input is reported as a problem, never raised.
"""

from __future__ import annotations

from typing import Any

from cockpit_spine import views
from cockpit_spine.audit import AuditError, verify_chain
from cockpit_spine.projections import Registry
from cockpit_spine.service import OperatorService
from cockpit_spine.session import OperatorSession
from cockpit_spine.sources import ProposalSource

REQUIRED_CODES = ("approved", "vetoed", "stopped")
EXPECTED_KIND = "operator session transcript; not a model comparison; no CP7 result; no verdict"
EXPECTED_LABEL = "live route integration deferred; recorded on the fake driver; no verdict"
EXIT_OK, EXIT_REFUSED = 0, 2


class _NoSource:
    adapter_kind = "none"

    def fetch(self, **_: Any) -> Any:
        raise AssertionError("a replay never contacts a source")


def _flag(argv: list[str], name: str) -> str | None:
    for position, item in enumerate(argv[:-1]):
        if item == name:
            return argv[position + 1]
    return None


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
        if _flag(argv, flag) != recorded:
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


def _view_lines(
    session: OperatorSession, registry: Registry, command: str, segment: list[dict[str, Any]],
) -> list[dict[str, str]] | None:
    """The lines the CLI would have shown, rebuilt from the replayed session."""

    svc = OperatorService(session, registry)
    kinds = [record["kind"] for record in segment]
    data = [record["data"] for record in segment]

    def show(ok: bool, code: object, extra: list[views.Line] | None = None) -> list[dict[str, str]]:
        shown = views.code("result", code)
        lines = [shown, *(extra or []), *svc._status_lines()]
        return svc._reply(ok, shown.value, lines)["lines"]

    if command == "status":
        return svc._reply(True, "status", svc._status_lines())["lines"]
    if command == "audit":
        return svc._reply(True, "audit", svc._audit_lines())["lines"]
    if command == "open":
        return svc._reply(True, "status", svc._status_lines())["lines"]
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
        ok, step_head = response.get("ok"), response.get("head")
        if not isinstance(ok, bool):
            found.append(f"{where}: ok is not a boolean")
            continue
        if step.get("exit_code") != (EXIT_OK if ok else EXIT_REFUSED):
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
        segment = chain[cursor + 1:end + 1]
        cursor = end
        found += [f"{where}: {m}" for m in _check_step(argv[0], argv, response, segment)]
        try:
            snap = OperatorSession.replay(chain[: end + 1], source=_NoSource())
            expected = _view_lines(snap, registry, argv[0], segment)
        except (AuditError, TypeError, KeyError, AttributeError, ValueError) as error:
            found.append(f"{where}: cannot rebuild the view ({type(error).__name__})")
        else:
            if expected is None:
                found.append(f"{where}: no rendered view for this command")
            elif response.get("lines") != expected:
                found.append(f"{where}: lines differ from the replayed view")
        (codes if ok else refusals).append(response.get("code"))
    if cursor != len(chain) - 1 or head != chain[-1]["hash"]:
        found.append("the steps do not cover the whole chain")
    for needed in REQUIRED_CODES:
        if needed not in codes:
            found.append(f"no successful {needed} step")
    if "session_stopped" not in refusals:
        found.append("no refusal after stop was recorded")
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
