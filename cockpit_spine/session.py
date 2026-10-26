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
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jsonschema

from cockpit_spine.audit import AuditChain, AuditError, verify_chain
from cockpit_spine.ids import is_id
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
OPERATOR_INVALID = "operator_invalid"
REASON_REQUIRED = "reason_required"
MODE_READ_ONLY = "mode_read_only"
DUPLICATE_REQUEST = "duplicate_request"
DUPLICATE_PROPOSAL = "duplicate_proposal"
PAYLOAD_INVALID = "payload_failed_validation"
SOURCE_ERROR = "source_error"
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


def _clean_id(text: object, *, limit: int = 64) -> str | None:
    cleaned = _clean(text, limit=limit)
    return cleaned if cleaned is not None and is_id(cleaned) else None


def _is_exact_clean(text: object, *, limit: int = MAX_REASON) -> bool:
    """True only for text the live commands would store: cleaned, and unchanged."""

    cleaned = _clean(text, limit=limit)
    return cleaned is not None and cleaned == text


def _is_exact_id(text: object, *, limit: int = 64) -> bool:
    """Like ``_is_exact_clean``, and the value must be an identifier. Null fails."""

    cleaned = _clean_id(text, limit=limit)
    return cleaned is not None and cleaned == text


_REPLY_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _is_reply_digest(value: object) -> bool:
    return value is None or (isinstance(value, str) and _REPLY_DIGEST.fullmatch(value) is not None)


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
        self._answered: set[str] = set()
        self._schema = load_output_schema(DECISION_PROFILE[PROPOSAL_WORKLOAD])

    @classmethod
    def open(
        cls, *, session_id: str, mode: str, source: ProposalSource,
        clock: Callable[[], str] = utc_now,
    ) -> OperatorSession:
        if mode not in SESSION_MODES:
            raise ValueError(f"mode must be one of {SESSION_MODES}; auto rungs are not expressible")
        cleaned_id = _clean_id(session_id)
        if cleaned_id is None:
            raise ValueError("session_id must be a short identifier")
        session = cls(source=source, clock=clock)
        session._emit("session_opened", {
            "session_id": cleaned_id, "mode": mode, "adapter_kind": source.adapter_kind,
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
            stored = session._chain.append(record["kind"], record["data"])
            # Apply the chain's copy, not the caller's record.
            session._apply(stored["kind"], stored["data"])
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
        """The one place state changes, shared by live commands and replay.

        A chain only proves records link: the hashes carry no key, so anyone can
        recompute them. Every invariant therefore lives here, and a record that
        the live commands would refuse is refused here too. Checks run before
        any mutation, so a refused record leaves the state untouched.
        """

        try:
            self._apply_checked(kind, copy.deepcopy(data))
        except (KeyError, TypeError, AttributeError) as error:
            raise AuditError(f"{kind}: malformed record ({type(error).__name__})") from None

    def _apply_checked(self, kind: str, data: dict[str, Any]) -> None:
        if not isinstance(data, dict) or not isinstance(data["at"], str):
            raise AuditError(f"{kind}: record has no timestamp")
        if kind == "session_opened":
            if self.session_id is not None:
                raise AuditError("session opened twice")
            if data["mode"] not in SESSION_MODES:
                raise AuditError("session_opened: mode is not read_only or paper")
            if not _is_exact_id(data["session_id"]):
                raise AuditError("session_opened: session_id is not a short identifier")
            if not isinstance(data["adapter_kind"], str):
                raise AuditError("session_opened: adapter_kind missing")
            self.session_id, self.mode = data["session_id"], data["mode"]
            return
        if self.session_id is None:
            raise AuditError(f"{kind}: no session is open")
        if kind == "command_refused":
            if not isinstance(data["command"], str) or not isinstance(data["code"], str):
                raise AuditError("command_refused: command and code are required")
            return
        if self.stopped:
            raise AuditError(f"{kind}: the session is stopped")
        if kind == "proposal_requested":
            request_id = data["request_id"]
            if not _is_exact_clean(request_id, limit=64):
                raise AuditError("proposal_requested: request_id is not clean text")
            if request_id in self._requests:
                raise AuditError("proposal_requested: request_id already used")
            if not isinstance(data["prompt_digest"], str):
                raise AuditError("proposal_requested: prompt_digest missing")
            self._requests.add(request_id)
        elif kind == "proposal_received":
            self._apply_received(data)
        elif kind == "proposal_refused":
            self._take_request(data["request_id"], "proposal_refused")
            quarantined = data["quarantined"]
            if not isinstance(quarantined, bool) or not _is_exact_clean(data["reason"]):
                raise AuditError("proposal_refused: reason and quarantined are required")
            if data["fail_closed_to"] != (None if quarantined else WATCH_ONLY):
                raise AuditError("proposal_refused: fail_closed_to does not match quarantined")
        elif kind in ("proposal_approved", "proposal_vetoed"):
            self._apply_decision(kind, data)
        elif kind == "session_stopped":
            self._apply_stop(data)
        else:
            raise AuditError(f"unknown record kind {kind!r}")

    def _take_request(self, request_id: object, kind: str) -> None:
        if request_id not in self._requests:
            raise AuditError(f"{kind}: no matching request")
        if request_id in self._answered:
            raise AuditError(f"{kind}: request already has an outcome")
        self._answered.add(request_id)

    def _apply_received(self, data: dict[str, Any]) -> None:
        payload = data["payload"]
        validator = jsonschema.Draft202012Validator(
            self._schema, format_checker=jsonschema.FormatChecker()
        )
        if not validator.is_valid(payload):
            raise AuditError("proposal_received: payload fails the output schema")
        if data["payload_digest"] != canonical_digest(payload):
            raise AuditError("proposal_received: payload_digest is not the payload's digest")
        proposal_id = data["proposal_id"]
        if proposal_id != payload["proposal_id"]:
            raise AuditError("proposal_received: proposal_id differs from the payload's")
        if proposal_id in self._proposals:
            raise AuditError("proposal_received: duplicate proposal_id")
        reply_digest = data["reply_output_digest"]
        if not _is_reply_digest(reply_digest):
            raise AuditError("proposal_received: reply_output_digest is not a sha256 digest")
        self._take_request(data["request_id"], "proposal_received")
        self._proposals[proposal_id] = {
            "proposal_id": proposal_id, "request_id": data["request_id"],
            "payload": copy.deepcopy(payload), "payload_digest": data["payload_digest"],
            "state": PENDING, "operator": None, "reason": None,
        }

    def _apply_decision(self, kind: str, data: dict[str, Any]) -> None:
        item = self._proposals.get(data["proposal_id"])
        if item is None or item["state"] != PENDING:
            raise AuditError(f"{kind}: proposal is not pending")
        operator = data["operator"]
        if not _is_exact_id(operator):
            raise AuditError(f"{kind}: operator is not a short identifier")
        if kind == "proposal_approved":
            if self.mode != "paper":
                raise AuditError("proposal_approved: the session is not in paper mode")
            if data["payload_digest"] != item["payload_digest"]:
                raise AuditError("proposal_approved: digest does not match the proposal")
            item["state"], item["reason"] = APPROVED, None
        else:
            reason = data["reason"]
            if not _is_exact_clean(reason):
                raise AuditError("proposal_vetoed: reason is not clean text")
            item["state"], item["reason"] = VETOED, reason
        item["operator"] = operator

    def _apply_stop(self, data: dict[str, Any]) -> None:
        if not _is_exact_id(data["operator"]):
            raise AuditError("session_stopped: operator is not a short identifier")
        if not _is_exact_clean(data["reason"]):
            raise AuditError("session_stopped: reason is not clean text")
        if data["fail_closed_to"] != WATCH_ONLY:
            raise AuditError("session_stopped: must fail closed to watch_only")
        abandoned = data["abandoned"]
        pending = [pid for pid, item in self._proposals.items() if item["state"] == PENDING]
        if not isinstance(abandoned, list) or sorted(abandoned) != sorted(pending):
            raise AuditError("session_stopped: abandoned is not exactly the pending set")
        for proposal_id in abandoned:
            self._proposals[proposal_id]["state"] = ABANDONED
        self.stopped = True

    def _refused(self, command: str, code: str, **detail: Any) -> CommandResult:
        if self.session_id is not None:
            self._emit("command_refused", {"command": command, "code": code, **detail})
        return _refuse(code)

    def request_proposal(self, *, request_id: str, prompt: str) -> CommandResult:
        if self.stopped:
            return self._refused("propose", SESSION_STOPPED)
        cleaned_id = _clean(request_id, limit=64)
        if cleaned_id is None or not isinstance(prompt, str):
            return self._refused("propose", UNSAFE_TEXT)
        request_id = cleaned_id
        if request_id in self._requests:
            return self._refused("propose", DUPLICATE_REQUEST, request_id=request_id)
        self._emit("proposal_requested", {
            "request_id": request_id, "prompt_digest": prompt_digest(prompt),
        })
        try:
            outcome = self._source.fetch(
                request_id=request_id, mode=self.mode or "", prompt=prompt
            )
            return self._from_outcome(request_id, outcome)
        except Exception:  # noqa: BLE001 - any source failure must fail closed, not dangle
            if request_id in self._answered:
                raise
            return self._refusal_record(request_id, SOURCE_ERROR, quarantined=False)

    def _from_outcome(self, request_id: str, outcome: object) -> CommandResult:
        """Read a source result. Any failure here is a source error, not a dangling request."""

        raw_reason = getattr(outcome, "reason", None)
        cleaned_reason = _clean(raw_reason)
        reason = cleaned_reason if cleaned_reason is not None else SOURCE_ERROR
        disposition = getattr(outcome, "disposition")
        usable = getattr(outcome, "usable")
        if disposition == NEEDS_HUMAN_REVIEW:
            return self._refusal_record(request_id, reason, quarantined=True)
        if disposition != ACCEPTED or not usable:
            return self._refusal_record(request_id, reason, quarantined=False)
        reply_record = getattr(outcome, "reply_record")
        reply = reply_record if isinstance(reply_record, dict) else None
        reply_digest = None if reply is None else reply.get("output_digest")
        if not _is_reply_digest(reply_digest):
            return self._refusal_record(request_id, SOURCE_ERROR, quarantined=False)
        payload = copy.deepcopy(getattr(outcome, "payload"))
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
            "reply_output_digest": reply_digest,
        })
        return CommandResult(True, "proposal_received", record)

    def _refusal_record(self, request_id: str, reason: str, *, quarantined: bool) -> CommandResult:
        record = self._emit("proposal_refused", {
            "request_id": request_id, "reason": reason, "quarantined": quarantined,
            "fail_closed_to": None if quarantined else WATCH_ONLY,
        })
        return CommandResult(False, reason, record)

    def _operator_refusal(self, command: str, operator: object) -> CommandResult | None:
        if _clean(operator, limit=64) is None:
            return self._refused(command, OPERATOR_REQUIRED)
        if _clean_id(operator) is None:
            return self._refused(command, OPERATOR_INVALID)
        return None

    def _gate(self, command: str, proposal_id: str, operator: str) -> CommandResult | None:
        if self.stopped:
            return self._refused(command, SESSION_STOPPED, proposal_id=str(proposal_id)[:64])
        refusal = self._operator_refusal(command, operator)
        if refusal is not None:
            return refusal
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
        refusal = self._operator_refusal("stop", operator)
        if refusal is not None:
            return refusal
        cleaned = _clean(reason)
        if cleaned is None:
            return self._refused("stop", REASON_REQUIRED)
        abandoned = [pid for pid, item in self._proposals.items() if item["state"] == PENDING]
        record = self._emit("session_stopped", {
            "operator": operator.strip(), "reason": cleaned, "abandoned": abandoned,
            "fail_closed_to": WATCH_ONLY,
        })
        return CommandResult(True, "stopped", record)
