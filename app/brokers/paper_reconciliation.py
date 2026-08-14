"""RW-15 — deterministic Alpaca paper-order polling and lifecycle reconciliation.

This service has read authority only.  It can GET an already-audited paper order and advance
the local audited FSM from broker evidence; it has no submit/cancel/replace method and never
creates an order.  Missing, malformed, conflicting, or identity-mismatched evidence leaves the
order open (the fail-safe direction for SEC-07 pending exposure).
"""
from __future__ import annotations

from dataclasses import dataclass
import logging

from sqlalchemy import select

from brokers.gateway import (
    _BROKER_STATUS_TO_STATE,
    _ack_consistent,
    _sanitize_broker_response,
)
from brokers.order_state_machine import (
    TERMINAL_STATES,
    legal_order_transition,
    record_fill_progress,
    transition,
)
from orchestrator.state import EVENT_ADJUDICATION, EVENT_ORDER_TRANSITION
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order

log = logging.getLogger("cockpit.paper_reconciliation")

MAX_ORDERS_PER_POLL = 100
_OPEN_STATES = frozenset({"submitted", "partially_filled"})


@dataclass(frozen=True)
class OrderPollReport:
    scanned: tuple[str, ...] = ()
    transitioned: tuple[str, ...] = ()
    progressed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    not_found: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    deferred: int = 0
    chain_verified: bool = True

    @property
    def ok(self) -> bool:
        return self.chain_verified and not self.errors


@dataclass(frozen=True)
class _Evidence:
    intent_order_ids: frozenset[str]
    latest_states: dict[str, str]
    broker_ids: dict[str, str]
    conflicting_broker_ids: frozenset[str]
    approval_ids: dict[str, str]
    conflicting_approval_ids: frozenset[str]
    approval_bindings: dict[str, dict]
    consumed_approvals: frozenset[str]


def _verified_evidence(st) -> _Evidence:
    """Derive lookup/state authority only from the verified append-only audit chain."""
    if not st.audit.verify_chain().ok:
        raise RuntimeError("audit chain is not verified")
    with st.session_factory() as session:
        entries = session.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()

    intent_ids: set[str] = set()
    latest_states: dict[str, str] = {}
    broker_ids_by_order: dict[str, set[str]] = {}
    approval_ids_by_order: dict[str, set[str]] = {}
    approval_bindings: dict[str, dict] = {}
    consumed_approvals: set[str] = set()
    for entry in entries:
        if not st.audit.verify_entry(entry):
            raise RuntimeError("audit entry failed verification")
        payload = entry.payload or {}
        refs = entry.refs or {}
        order_id = refs.get("order_id") or refs.get("row_id")
        if payload.get("_phase") == "intent" and payload.get("_op") == "order_submit":
            if refs.get("order_id"):
                intent_ids.add(str(refs["order_id"]))
        if entry.event_type == EVENT_ORDER_TRANSITION and refs.get("row_id"):
            to_state = payload.get("to")
            if to_state:
                latest_states[str(refs["row_id"])] = str(to_state)
        if order_id:
            broker_order_id = refs.get("broker_order_id") or payload.get("broker_order_id")
            if broker_order_id:
                broker_ids_by_order.setdefault(str(order_id), set()).add(
                    str(broker_order_id)
                )
            approval_id = refs.get("approval_id") or payload.get("approval_id")
            if approval_id:
                approval_ids_by_order.setdefault(str(order_id), set()).add(str(approval_id))
        if entry.event_type == EVENT_ADJUDICATION and refs.get("row_id"):
            approval_id = str(refs["row_id"])
            if payload.get("decision") == "approve":
                approval_bindings[approval_id] = {
                    "proposal_id": payload.get("proposal_id"),
                    "risk_decision_id": payload.get("risk_decision_id"),
                    "order_tuple_hash": payload.get("order_tuple_hash"),
                }
            if payload.get("kind") == "approval_consumed":
                consumed_approvals.add(approval_id)

    conflicts = frozenset(
        order_id for order_id, values in broker_ids_by_order.items() if len(values) != 1
    )
    broker_ids = {
        order_id: next(iter(values))
        for order_id, values in broker_ids_by_order.items()
        if len(values) == 1
    }
    approval_conflicts = frozenset(
        order_id for order_id, values in approval_ids_by_order.items() if len(values) != 1
    )
    approval_ids = {
        order_id: next(iter(values))
        for order_id, values in approval_ids_by_order.items()
        if len(values) == 1
    }
    return _Evidence(
        intent_order_ids=frozenset(intent_ids),
        latest_states=latest_states,
        broker_ids=broker_ids,
        conflicting_broker_ids=conflicts,
        approval_ids=approval_ids,
        conflicting_approval_ids=approval_conflicts,
        approval_bindings=approval_bindings,
        consumed_approvals=frozenset(consumed_approvals),
    )


class PaperOrderReconciler:
    """Poll existing paper orders and consume broker ACKs through the one local FSM."""

    def __init__(self, state, *, broker, max_orders_per_poll: int = MAX_ORDERS_PER_POLL) -> None:
        self.state = state
        self._broker = broker
        self._limit = max(1, min(int(max_orders_per_poll), MAX_ORDERS_PER_POLL))
        self._cursor: str | None = None

    def _candidate_ids(self, evidence: _Evidence) -> tuple[tuple[str, ...], int]:
        with self.state.session_factory() as session:
            rows = session.execute(select(Order)).scalars().all()
        known_ids = {row.id for row in rows}
        ids = {
            row.id
            for row in rows
            if row.state not in TERMINAL_STATES
            and (row.state in _OPEN_STATES or row.id in evidence.intent_order_ids)
        }
        # A signed submit intent whose mutable row disappeared is itself actionable evidence;
        # include it so the report stays visibly fail-closed instead of silently skipping it.
        ids.update(evidence.intent_order_ids - known_ids)
        ordered = sorted(ids)
        if not ordered:
            return (), 0
        start = 0
        if self._cursor in ordered:
            start = (ordered.index(self._cursor) + 1) % len(ordered)
        rotated = ordered[start:] + ordered[:start]
        selected = tuple(rotated[: self._limit])
        if selected:
            self._cursor = selected[-1]
        return selected, max(0, len(ordered) - len(selected))

    def order_history(self, order_id: str) -> list[dict]:
        """Broker-history contract consumed by ``reconcile_startup``.

        The returned ``order_id`` is always the queried durable local id.  Broker identity is
        independently bound by a signed broker id when one exists, or by the deterministic
        client id on the lost-response path.
        """
        evidence = _verified_evidence(self.state)
        clean_id = str(order_id)
        if clean_id not in evidence.intent_order_ids:
            return []
        if clean_id in evidence.conflicting_broker_ids:
            raise RuntimeError("conflicting signed broker ids for local order")
        with self.state.session_factory() as session:
            row = session.get(Order, clean_id)
            if row is not None:
                session.expunge(row)
        if row is None:
            return []

        signed_broker_id = evidence.broker_ids.get(clean_id)
        mutable_broker_id = None if row.broker_order_id is None else str(row.broker_order_id)
        if signed_broker_id and mutable_broker_id and mutable_broker_id != signed_broker_id:
            raise RuntimeError("mutable broker id disagrees with signed audit evidence")
        result = self._broker.find_order(
            broker_order_id=signed_broker_id,
            client_order_id=f"cockpit-{clean_id}"[:128],
        )
        if result is None:
            return []
        normalized = _sanitize_broker_response(result)
        broker_order_id = normalized.get("broker_order_id")
        if not broker_order_id:
            raise RuntimeError("broker history omitted broker order id")
        if signed_broker_id and broker_order_id != signed_broker_id:
            raise RuntimeError("broker response disagrees with signed broker id")
        return [{"order_id": clean_id, **normalized}]

    def _apply(self, order_id: str, record: dict) -> tuple[bool, bool]:
        """Return ``(transitioned, progressed)`` after a locked, fresh evidence re-check."""
        with self.state.lock:
            evidence = _verified_evidence(self.state)
            if order_id not in evidence.intent_order_ids:
                raise RuntimeError("order lost its verified submit intent")
            if order_id in evidence.conflicting_broker_ids:
                raise RuntimeError("conflicting signed broker ids for local order")
            if order_id in evidence.conflicting_approval_ids:
                raise RuntimeError("conflicting signed approval ids for local order")
            with self.state.session_factory() as session:
                order = session.get(Order, order_id)
                if order is not None:
                    session.expunge(order)
            if order is None:
                raise RuntimeError("audited submit intent has no local order row")
            if order.state in TERMINAL_STATES:
                return False, False
            if evidence.latest_states.get(order_id) != order.state:
                raise RuntimeError("mutable order state disagrees with signed transition history")

            broker_order_id = record.get("broker_order_id")
            signed_broker_id = evidence.broker_ids.get(order_id)
            if not broker_order_id or (signed_broker_id and broker_order_id != signed_broker_id):
                raise RuntimeError("broker response identity is not audit-bound")
            if order.broker_order_id and str(order.broker_order_id) != str(broker_order_id):
                raise RuntimeError("mutable broker id disagrees with broker response")

            approval_id = evidence.approval_ids.get(order_id)
            if not approval_id:
                raise RuntimeError("submitted order has no signed approval id")
            binding = evidence.approval_bindings.get(approval_id)
            with self.state.session_factory() as session:
                approval = session.get(ApprovalRecord, approval_id)
                if approval is not None:
                    session.expunge(approval)
            if approval is None or binding != {
                "proposal_id": order.proposal_id,
                "risk_decision_id": order.risk_decision_id,
                "order_tuple_hash": order.order_tuple_hash,
            }:
                raise RuntimeError("signed approval does not bind the local order")
            if (
                approval.proposal_id != order.proposal_id
                or approval.risk_decision_id != order.risk_decision_id
                or approval.order_tuple_hash != order.order_tuple_hash
                or approval.user_decision != "approve"
            ):
                raise RuntimeError("mutable approval disagrees with signed adjudication")
            signed_consumed = approval_id in evidence.consumed_approvals
            if bool(approval.single_use_consumed) != signed_consumed:
                raise RuntimeError("mutable approval consumption disagrees with audit chain")
            if not signed_consumed:
                approval.single_use_consumed = True
                self.state.audit.record_state_change(
                    approval,
                    EVENT_ADJUDICATION,
                    actor="alpaca_paper_reconcile",
                    payload={
                        "kind": "approval_consumed",
                        "proposal_id": order.proposal_id,
                        "order_id": order.id,
                        "order_tuple_hash": order.order_tuple_hash,
                        "reconciled": True,
                    },
                )
            if order.approval_id and str(order.approval_id) != approval_id:
                raise RuntimeError("mutable order approval id disagrees with signed submit")
            order.approval_id = approval_id

            transitioned = False
            # A response returned by the exact broker/client-id lookup proves the submit landed.
            # Reconstruct the missing local submit steps before consuming its current ACK.  This
            # is state repair only; no broker write is reachable from this service.
            if order.state == "proposed":
                transition(
                    self.state,
                    order,
                    "approved",
                    actor="alpaca_paper_reconcile",
                    reason="broker confirms lost-response submit",
                    clock=self.state.clock,
                )
                transitioned = True
            if order.state == "approved":
                transition(
                    self.state,
                    order,
                    "submitted",
                    actor="alpaca_paper_reconcile",
                    reason="broker confirms submitted order",
                    clock=self.state.clock,
                    broker_order_id=broker_order_id,
                )
                transitioned = True

            status = str(record.get("status") or "").strip().lower()
            ack_state = _BROKER_STATUS_TO_STATE.get(status)
            if ack_state == "partially_filled" and order.state == "partially_filled":
                if not _ack_consistent(ack_state, record, order):
                    self._audit_ignored(order, record, "partial fill data is inconsistent")
                    return transitioned, False
                if record["filled_qty"] > order.filled_qty:
                    record_fill_progress(
                        self.state,
                        order,
                        filled_qty=record["filled_qty"],
                        avg_fill_price=record.get("avg_fill_price"),
                        actor="alpaca_paper_reconcile",
                        reason="broker reported cumulative partial-fill progress",
                        clock=self.state.clock,
                    )
                    return transitioned, True
                return transitioned, False

            if ack_state and ack_state != order.state and legal_order_transition(
                order.state, ack_state
            ):
                if not _ack_consistent(ack_state, record, order):
                    self._audit_ignored(order, record, "fill data is inconsistent with status")
                    return transitioned, False
                transition(
                    self.state,
                    order,
                    ack_state,
                    actor="alpaca_paper_reconcile",
                    reason=f"broker status poll reported {status!r}",
                    clock=self.state.clock,
                    broker_order_id=broker_order_id,
                    filled_qty=record.get("filled_qty"),
                    avg_fill_price=record.get("avg_fill_price"),
                )
                transitioned = True
            return transitioned, False

    def _audit_ignored(self, order: Order, record: dict, reason: str) -> None:
        self.state.audit.append(
            "order_status_ignored",
            actor="alpaca_paper_reconcile",
            refs={"order_id": order.id},
            payload={
                "status": record.get("status"),
                "filled_qty": (
                    None
                    if record.get("filled_qty") is None
                    else str(record.get("filled_qty"))
                ),
                "qty": str(order.qty),
                "reason": reason,
            },
        )

    def poll(self) -> OrderPollReport:
        try:
            evidence = _verified_evidence(self.state)
        except Exception:  # noqa: BLE001 - chain uncertainty permits no lifecycle mutation
            return OrderPollReport(errors=("audit_chain",), chain_verified=False)
        candidates, deferred = self._candidate_ids(evidence)
        transitioned: list[str] = []
        progressed: list[str] = []
        unchanged: list[str] = []
        not_found: list[str] = []
        errors: list[str] = []
        for order_id in candidates:
            try:
                history = self.order_history(order_id)
                if not history:
                    not_found.append(order_id)
                    continue
                moved, advanced = self._apply(order_id, history[-1])
                if moved:
                    transitioned.append(order_id)
                if advanced:
                    progressed.append(order_id)
                if not moved and not advanced:
                    unchanged.append(order_id)
            except Exception as exc:  # noqa: BLE001 - one bad broker row cannot hide siblings
                errors.append(order_id)
                with self.state.lock:
                    self.state.broker_errors_since_sample += 1
                log.warning("paper order poll failed for %s (%s)", order_id, type(exc).__name__)
        return OrderPollReport(
            scanned=candidates,
            transitioned=tuple(transitioned),
            progressed=tuple(progressed),
            unchanged=tuple(unchanged),
            not_found=tuple(not_found),
            errors=tuple(errors),
            deferred=deferred,
        )
