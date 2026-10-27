"""Operator service API v0: one command in, one typed view out.

Every surface (CLI, loopback view) is a thin client of ``OperatorService.handle``
and renders the lines it returns. Requests are strict: exact command names,
exact argument keys, strings only. Anything else is refused with a code and
changes nothing. Each response line is validated before it leaves, so an
unlinked number cannot reach a screen through any surface. A line that fails
validation is replaced by a withheld marker; it never changes ``ok`` or ``code``,
which always report what the session did, and it never hides the other lines.
"""

from __future__ import annotations

from typing import Any

from cockpit_spine import views
from cockpit_spine.projections import ProjectionError, Registry, WITHHELD
from cockpit_spine.session import OperatorSession
from cockpit_spine.views import Line

API_VERSION = "operator-api/0"
CONTEXT_FIGURES = (
    ("Plumbing cells accepted (route plumbing record, not a result)",
     "x2-fake-driver-cells-accepted"),
    ("Plumbing cells in that record", "x2-fake-driver-cells-total"),
)
COMMANDS: dict[str, frozenset[str]] = {
    "status": frozenset(),
    "audit": frozenset(),
    "propose": frozenset({"request_id", "prompt"}),
    "approve": frozenset({"proposal_id", "operator", "confirm_digest"}),
    "veto": frozenset({"proposal_id", "operator", "reason"}),
    "stop": frozenset({"operator", "reason"}),
}
OPTIONAL_ARGS = {"propose": frozenset({"prompt"})}
BAD_REQUEST = "bad_request"
UNKNOWN_COMMAND = "unknown_command"
LABEL_DEFERRED = "live route integration deferred; route contract plumbing only"


class OperatorService:
    def __init__(self, session: OperatorSession, registry: Registry) -> None:
        self.session = session
        self._registry = registry

    def handle(self, request: object) -> dict[str, Any]:
        if not isinstance(request, dict) or request.get("api") != API_VERSION:
            return self._reply(False, BAD_REQUEST, [])
        name, args = request.get("command"), request.get("args", {})
        if not isinstance(name, str) or name not in COMMANDS:
            return self._reply(False, UNKNOWN_COMMAND, [])
        required = COMMANDS[name] - OPTIONAL_ARGS.get(name, frozenset())
        if (not isinstance(args, dict) or not required <= set(args)
                or not set(args) <= COMMANDS[name]
                or not all(isinstance(v, str) for v in args.values())):
            return self._reply(False, BAD_REQUEST, [])
        return getattr(self, f"_{name}")(args)

    def _reply(self, ok: bool, code: str, lines: list[Line]) -> dict[str, Any]:
        safe, withheld = views.sanitize_lines(lines, self._registry)
        return {"api": API_VERSION, "ok": ok, "code": code, "head": self.session.head,
                "withheld": withheld, "lines": [item.as_dict() for item in safe]}

    def _status_lines(self) -> list[Line]:
        session = self.session
        lines = [
            Line("id", "api", API_VERSION),
            Line("id", "session", session.session_id or "none"),
            Line("text", "mode", session.mode or "none"),
            Line("text", "state", "stopped, watch_only" if session.stopped else "running"),
            Line("digest", "chain head", session.head),
            Line("text", "label", LABEL_DEFERRED),
        ]
        for label, projection_id in CONTEXT_FIGURES:
            try:
                shown = views.figure(
                    label, self._registry.figure(projection_id), self._registry)
            except ProjectionError:
                # Stop (and status) must still render when a figure has no
                # verified projection. The number is withheld; the command stands.
                shown = Line("figure", label, WITHHELD)
            lines.append(shown)
        for item in session.proposals():
            payload = item["payload"]
            lines += [
                Line("id", "proposal", item["proposal_id"]),
                Line("text", "proposal state", item["state"]),
                views.term("symbol", payload["symbol"]),
                views.term("side", payload["side"]),
                views.term("asset_type", payload["asset_type"]),
                views.term("order_type", payload["order_type"]),
                # quantity and notional are optional on TradeProposal; a missing
                # key is "none", not a crash. The other fields below are required.
                views.term("quantity", payload.get("quantity")),
                views.term("notional", payload.get("notional")),
                views.term("time_horizon", payload["time_horizon"]),
                views.quoted("thesis (model text, unverified)", payload["thesis"]),
                views.quoted("invalidation (model text, unverified)", payload["invalidation"]),
                *[Line("id", "source ref", ref) for ref in payload["source_refs"]],
                Line("digest", "confirm digest", item["payload_digest"]),
            ]
            if item["operator"]:
                lines.append(Line("id", "decided by", item["operator"]))
            if item["reason"]:
                lines.append(views.quoted("reason", item["reason"]))
        return lines

    def _audit_lines(self) -> list[Line]:
        lines = []
        for record in self.session.records():
            lines += [
                Line("id", "record", f"{record['seq']}:{record['kind']}"),
                Line("time", "at", record["data"]["at"]),
                Line("digest", "hash", record["hash"]),
            ]
        return lines

    def _status(self, args: dict[str, str]) -> dict[str, Any]:
        return self._reply(True, "status", self._status_lines())

    def _audit(self, args: dict[str, str]) -> dict[str, Any]:
        return self._reply(True, "audit", self._audit_lines())

    def _outcome(self, result: Any, extra: list[Line] | None = None) -> dict[str, Any]:
        shown = views.code("result", result.code)
        lines = [shown] + (extra or []) + self._status_lines()
        return self._reply(result.ok, shown.value, lines)

    def _propose(self, args: dict[str, str]) -> dict[str, Any]:
        result = self.session.request_proposal(
            request_id=args["request_id"], prompt=args.get("prompt", ""))
        return self._outcome(result)

    def _approve(self, args: dict[str, str]) -> dict[str, Any]:
        result = self.session.approve(
            proposal_id=args["proposal_id"], operator=args["operator"],
            confirm_digest=args["confirm_digest"])
        extra = []
        if result.ok and result.record is not None:
            extra = [
                Line("text", "authority", "none, inert record"),
                Line("id", "approved intent", result.record["proposal_id"]),
                Line("digest", "audit hash", result.record["audit_hash"]),
            ]
        return self._outcome(result, extra)

    def _veto(self, args: dict[str, str]) -> dict[str, Any]:
        return self._outcome(self.session.veto(
            proposal_id=args["proposal_id"], operator=args["operator"], reason=args["reason"]))

    def _stop(self, args: dict[str, str]) -> dict[str, Any]:
        return self._outcome(self.session.stop(operator=args["operator"], reason=args["reason"]))


__all__ = ("API_VERSION", "COMMANDS", "OperatorService")
