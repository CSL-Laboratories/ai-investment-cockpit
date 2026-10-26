"""The operator session: propose, approve, veto, stop. Event-sourced, fail closed.

What it is: the human gate of the Cockpit spine, driven by a ``ProposalSource``
(the route consumer in front of a fake driver, today). Every state change is
a record in a hash chain, and the whole state can be rebuilt from that chain.

What it is not: an order path. ``approve`` returns an inert ``ApprovedIntent``
that carries ``authority: none``. Nothing here reaches a broker, the risk
engine or ``OrderGateway``; an approval is only ever the human step that a
later, separately reviewed wiring may require (Inv 1). ``mode`` is limited to
``read_only`` and ``paper``, so an auto rung cannot be expressed.

Stop is final: pending proposals are abandoned, the source is never contacted
again, and every later command is refused (Inv 7, fail closed).
"""

from __future__ import annotations

import copy
import datetime
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jsonschema

from cockpit_spine.audit import AuditChain, AuditError, verify_chain
from cockpit_spine.sources import PROPOSAL_WORKLOAD, ProposalSource
from successor.route_consumer import ACCEPTED, DECISION_PROFILE, NEEDS_HUMAN_REVIEW
from successor.route_consumer.profile import canonical_digest, load_output_schema
from successor.route_consumer.records import prompt_digest

SESSION_MODES = ("read_only", "paper")
WATCH_ONLY = "watch_only"
INERT_NOTE = (
    "inert record: no order authority; any order still goes risk -> mode -> approval "
    "-> OrderGateway elsewhere (Inv 1)"
)

PENDING, APPROVED, VETOED, ABANDONED = "pending", "approved", "vetoed", "abandoned"

# Refusal codes.
SESSION_STOPPED = "session_stopped"
UNKNOWN_PROPOSAL = "unknown_proposal"
NOT_PENDING = "not_pending"
DIGEST_MISMATCH = "digest_mismatch"
OPERATOR_REQUIRED = "operator_required"
REASON_REQUIRED = "reason_required"
MODE_READ_ONLY = "mode_read_only"
DUPLICATE_REQUEST = "duplicate_request"
DUPLICATE_PROPOSAL = "duplicate_proposal"
PAYLOAD_INVALID = "payload_failed_validation"
UNSAFE_TEXT = "text_too_long_or_unprintable"
MAX_REASON = 200


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    code: str
    record: dict[str, Any] | None = None


def _refuse(code: str) -> CommandResult:
    return CommandResult(False, code)


def _clean(text: object, *, limit: int = MAX_REASON) -> str | None:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped or len(stripped) > limit or not stripped.isprintable():
        return None
    return stripped


class OperatorSession:
    def __init__(self, *, source: ProposalSource, clock: Callable[[], str] = utc_now) -> None:
        self._source = source
        self._clock = clock
        self._chain = AuditChain()
        self.session_id: str | None = None
        self.mode: str | None = None
        self.stopped = False
        self._proposals: dict[str, dict[str, Any]] = {}
        self._requests: set[str] = set()
        self._schema = load_output_schema(DECISION_PROFILE[PROPOSAL_WORKLOAD])

    @classmethod
    def open(
        cls, *, session_id: str, mode: str, source: ProposalSource,
        clock: Callable[[], str] = utc_now,
    ) -> OperatorSession:
        if mode not in SESSION_MODES:
            raise ValueError(f"mode must be one of {SESSION_MODES}; auto rungs are not expressible")
        if _clean(session_id, limit=64) is None:
            raise ValueError("session_id must be short printable text")
        session = cls(source=source, clock=clock)
        session._emit("session_opened", {
            "session_id": session_id, "mode": mode, "adapter_kind": source.adapter_kind,
        })
        return session

    @classmethod
    def replay(
        cls, records: list[dict[str, Any]], *, source: ProposalSource,
        clock: Callable[[], str] = utc_now,
    ) -> OperatorSession:
        verify_chain(records)
        session = cls(source=source, clock=clock)
        for record in records:
            session._chain.append(record["kind"], record["data"])
            session._apply(record["kind"], record["data"])
        if session._chain.records() != records:
            raise AuditError("replayed chain differs from the stored chain")
        if session.session_id is None:
            raise AuditError("chain does not start with session_opened")
        return session

    def records(self) -> list[dict[str, Any]]:
        return self._chain.records()

    @property
    def head(self) -> str:
        return self._chain.head

    def proposals(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(p) for p in self._proposals.values()]

    def proposal(self, proposal_id: str) -> dict[str, Any] | None:
        found = self._proposals.get(proposal_id)
        return copy.deepcopy(found) if found else None

    def _emit(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        data = {**data, "at": self._clock()}
        self._apply(kind, data)
        return self._chain.append(kind, data)

    def _apply(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "session_opened":
            if self.session_id is not None:
                raise AuditError("session opened twice")
            self.session_id, self.mode = data["session_id"], data["mode"]
            return
        if self.session_id is None:
            raise AuditError(f"{kind}: no session is open")
        if kind == "command_refused":
            return
        if self.stopped:
            raise AuditError(f"{kind}: the session is stopped")
        if kind == "proposal_requested":
            self._requests.add(data["request_id"])
        elif kind == "proposal_received":
            if data["request_id"] not in self._requests:
                raise AuditError("proposal_received: no matching request")
            self._proposals[data["proposal_id"]] = {
                "proposal_id": data["proposal_id"], "request_id": data["request_id"],
                "payload": data["payload"], "payload_digest": data["payload_digest"],
                "state": PENDING, "operator": None, "reason": None,
            }
        elif kind == "proposal_refused":
            pass
        elif kind in ("proposal_approved", "proposal_vetoed"):
            item = self._proposals.get(data["proposal_id"])
            if item is None or item["state"] != PENDING:
                raise AuditError(f"{kind}: proposal is not pending")
            item["state"] = APPROVED if kind == "proposal_approved" else VETOED
            item["operator"] = data["operator"]
            item["reason"] = data.get("reason")
        elif kind == "session_stopped":
            for proposal_id in data["abandoned"]:
                item = self._proposals.get(proposal_id)
                if item is None or item["state"] != PENDING:
                    raise AuditError("session_stopped: abandoned a non-pending proposal")
                item["state"] = ABANDONED
            self.stopped = True
        else:
            raise AuditError(f"unknown record kind {kind!r}")

    def _refused(self, command: str, code: str, **detail: Any) -> CommandResult:
        if self.session_id is not None:
            self._emit("command_refused", {"command": command, "code": code, **detail})
        return _refuse(code)

    def request_proposal(self, *, request_id: str, prompt: str) -> CommandResult:
        if self.stopped:
            return self._refused("propose", SESSION_STOPPED)
        if _clean(request_id, limit=64) is None or not isinstance(prompt, str):
            return self._refused("propose", UNSAFE_TEXT)
        if request_id in self._requests:
            return self._refused("propose", DUPLICATE_REQUEST, request_id=request_id)
        self._emit("proposal_requested", {
            "request_id": request_id, "prompt_digest": prompt_digest(prompt),
        })
        outcome = self._source.fetch(request_id=request_id, mode=self.mode or "", prompt=prompt)
        if outcome.disposition == NEEDS_HUMAN_REVIEW:
            return self._refusal_record(request_id, outcome.reason, quarantined=True)
        if outcome.disposition != ACCEPTED or not outcome.usable:
            return self._refusal_record(request_id, outcome.reason, quarantined=False)
        payload = copy.deepcopy(outcome.payload)
        validator = jsonschema.Draft202012Validator(
            self._schema, format_checker=jsonschema.FormatChecker()
        )
        if not validator.is_valid(payload):
            return self._refusal_record(request_id, PAYLOAD_INVALID, quarantined=True)
        proposal_id = payload["proposal_id"]
        if proposal_id in self._proposals:
            return self._refusal_record(request_id, DUPLICATE_PROPOSAL, quarantined=False)
        record = self._emit("proposal_received", {
            "request_id": request_id, "proposal_id": proposal_id, "payload": payload,
            "payload_digest": canonical_digest(payload),
            "reply_output_digest": (outcome.reply_record or {}).get("output_digest"),
        })
        return CommandResult(True, "proposal_received", record)

    def _refusal_record(self, request_id: str, reason: str, *, quarantined: bool) -> CommandResult:
        record = self._emit("proposal_refused", {
            "request_id": request_id, "reason": reason, "quarantined": quarantined,
            "fail_closed_to": None if quarantined else WATCH_ONLY,
        })
        return CommandResult(False, reason, record)

    def _gate(self, command: str, proposal_id: str, operator: str) -> CommandResult | None:
        if self.stopped:
            return self._refused(command, SESSION_STOPPED, proposal_id=str(proposal_id)[:64])
        if _clean(operator, limit=64) is None:
            return self._refused(command, OPERATOR_REQUIRED)
        item = self._proposals.get(proposal_id) if isinstance(proposal_id, str) else None
        if item is None:
            return self._refused(command, UNKNOWN_PROPOSAL)
        if item["state"] != PENDING:
            return self._refused(command, NOT_PENDING, proposal_id=proposal_id)
        return None

    def approve(self, *, proposal_id: str, operator: str, confirm_digest: str) -> CommandResult:
        refusal = self._gate("approve", proposal_id, operator)
        if refusal is not None:
            return refusal
        if self.mode != "paper":
            return self._refused("approve", MODE_READ_ONLY, proposal_id=proposal_id)
        item = self._proposals[proposal_id]
        if confirm_digest != item["payload_digest"]:
            return self._refused("approve", DIGEST_MISMATCH, proposal_id=proposal_id)
        record = self._emit("proposal_approved", {
            "proposal_id": proposal_id, "operator": operator.strip(),
            "payload_digest": item["payload_digest"],
        })
        intent = {
            "type": "ApprovedIntent@spine0", "proposal_id": proposal_id,
            "payload_digest": item["payload_digest"], "operator": operator.strip(),
            "authority": "none", "note": INERT_NOTE, "audit_hash": record["hash"],
        }
        return CommandResult(True, "approved", intent)

    def veto(self, *, proposal_id: str, operator: str, reason: str) -> CommandResult:
        refusal = self._gate("veto", proposal_id, operator)
        if refusal is not None:
            return refusal
        cleaned = _clean(reason)
        if cleaned is None:
            return self._refused("veto", REASON_REQUIRED, proposal_id=proposal_id)
        record = self._emit("proposal_vetoed", {
            "proposal_id": proposal_id, "operator": operator.strip(), "reason": cleaned,
        })
        return CommandResult(True, "vetoed", record)

    def stop(self, *, operator: str, reason: str) -> CommandResult:
        if self.stopped:
            return self._refused("stop", SESSION_STOPPED)
        if _clean(operator, limit=64) is None:
            return self._refused("stop", OPERATOR_REQUIRED)
        cleaned = _clean(reason)
        if cleaned is None:
            return self._refused("stop", REASON_REQUIRED)
        abandoned = [pid for pid, item in self._proposals.items() if item["state"] == PENDING]
        record = self._emit("session_stopped", {
            "operator": operator.strip(), "reason": cleaned, "abandoned": abandoned,
            "fail_closed_to": WATCH_ONLY,
        })
        return CommandResult(True, "stopped", record)
