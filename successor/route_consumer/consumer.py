"""The Cockpit consumer of a route: build a request, read the reply, fail closed.

This module owns no execution, no session state, no queue, no lease and no
choice between routes. It speaks only the pinned Route Contract v0 records
through a ``RoutePort`` that Recursion implements. Whatever the reply, the
outcome is one of three dispositions:

* ``accepted``: the route returned ``ok`` and the payload passed this
  consumer's own re-validation against the workload's output schema (Inv 3).
* ``needs_human_review``: the output was quarantined, never used (Inv 3).
* ``fail_closed``: anything else. Inv 7 says a vendor limit, a stale or
  missing analysis, or a refusal must never become "trade without fresh
  analysis", so the caller is told to fall to ``watch_only``.

Nothing here touches the order path. A payload is analysis only; any order
still goes risk -> mode -> approval -> ``OrderGateway`` elsewhere (Inv 1).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Protocol

import jsonschema

from successor.route_consumer import pin
from successor.route_consumer.profile import (
    FAIL_CLOSED_TO,
    ProfileRefusal,
    WorkloadProfile,
    check_output_schema,
    engines_allowed,
    load_output_schema,
)
from successor.route_consumer.records import RouteBinding, build_request_record

ACCEPTED = "accepted"
NEEDS_HUMAN_REVIEW = "needs_human_review"
FAIL_CLOSED = "fail_closed"


class RoutePort(Protocol):
    """What Recursion provides. Fake-driver in-process today; live is unsupported."""

    def support(self) -> dict[str, Any]:
        """An ``AdapterSupport@0`` record."""

    def submit(
        self, request: dict[str, Any], *, prompt: str, output_schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a ``RouteExecutionResult@0`` or ``RouteRefusal@0`` record."""


@dataclass(frozen=True)
class DecisionOutcome:
    disposition: str
    reason: str
    payload: dict[str, Any] | None
    request_record: dict[str, Any] | None
    reply_record: dict[str, Any] | None
    fail_closed_to: str | None

    @property
    def usable(self) -> bool:
        return self.disposition == ACCEPTED and self.payload is not None


def _closed(
    reason: str,
    request: dict[str, Any] | None = None,
    reply: dict[str, Any] | None = None,
) -> DecisionOutcome:
    return DecisionOutcome(FAIL_CLOSED, reason, None, request, reply, FAIL_CLOSED_TO)


class RouteConsumer:
    def __init__(self, port: RoutePort, *, binding: RouteBinding) -> None:
        self._port = port
        self._binding = binding

    def decide(
        self,
        *,
        request_id: str,
        profile: WorkloadProfile,
        mode: str,
        prompt: str,
        output_schema: dict[str, Any] | None = None,
    ) -> DecisionOutcome:
        try:
            if self._binding.engine_id not in engines_allowed(mode):
                return _closed("engine_not_permitted_in_mode")
            if output_schema is None:
                output_schema = load_output_schema(profile)
            else:
                check_output_schema(profile, output_schema)
        except ProfileRefusal as refusal:
            return _closed(refusal.code)

        refusal_reason = self._support_refusal()
        if refusal_reason is not None:
            return _closed(refusal_reason)

        request = build_request_record(
            request_id=request_id,
            binding=self._binding,
            profile=profile,
            prompt=prompt,
            output_schema=output_schema,
        )
        if pin.validation_errors("RouteExecutionRequest", request):
            return _closed("invalid_request")

        try:
            reply = self._port.submit(
                copy.deepcopy(request), prompt=prompt, output_schema=copy.deepcopy(output_schema)
            )
        except Exception:
            return _closed("route_port_error", request)
        return self._read_reply(request, reply, output_schema)

    def _support_refusal(self) -> str | None:
        try:
            support = self._port.support()
        except Exception:
            return "route_port_error"
        if not isinstance(support, dict) or pin.validation_errors("AdapterSupport", support):
            return "invalid_route_reply"
        if support["engine_id"] != self._binding.engine_id:
            return "invalid_route_reply"
        if support["state"] != "supported" or support["live_calls"] is not False:
            return "unsupported"
        return None

    def _read_reply(
        self, request: dict[str, Any], reply: object, output_schema: dict[str, Any]
    ) -> DecisionOutcome:
        if not isinstance(reply, dict):
            return _closed("invalid_route_reply", request)
        kind = reply.get("type")
        if kind == "RouteRefusal@0":
            name = "RouteRefusal"
        elif kind == "RouteExecutionResult@0":
            name = "RouteExecutionResult"
        else:
            return _closed("invalid_route_reply", request)
        if pin.validation_errors(name, reply):
            return _closed("invalid_route_reply", request)
        request_ref = reply["request_id"]
        if request_ref != request["id"]:
            return _closed("invalid_route_reply", request)

        if name == "RouteRefusal":
            return _closed(reply["code"], request, reply)

        status = reply["status"]
        if status == "ok":
            return self._accept(request, reply, output_schema)
        reason = reply["error"] or status
        if status == "needs_human_review":
            return DecisionOutcome(NEEDS_HUMAN_REVIEW, reason, None, request, reply, None)
        return _closed(reason, request, reply)

    def _accept(
        self, request: dict[str, Any], reply: dict[str, Any], output_schema: dict[str, Any]
    ) -> DecisionOutcome:
        payload = copy.deepcopy(reply["validated"])
        validator = jsonschema.Draft202012Validator(
            output_schema, format_checker=jsonschema.FormatChecker()
        )
        if not validator.is_valid(payload):
            return DecisionOutcome(
                NEEDS_HUMAN_REVIEW, "consumer_revalidation_failed", None, request, reply, None
            )
        return DecisionOutcome(ACCEPTED, "ok", payload, request, reply, None)
