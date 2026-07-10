"""W12 — startup reconciliation (security-hardening-backlog SEC-13).

``OrderGateway.submit`` writes an ``intent`` ``AuditEntry`` BEFORE the private broker call
and a ``result`` entry AFTER (``storage/audit.py``'s two-phase primitives). A crash between
the broker's ACK and the result commit leaves a real order with ONLY an intent entry —
``AuditLog.unresolved_intents()`` finds it, but nothing acted on that finding before W12.

This module runs ONCE at startup, before the scheduler/API start (wired in
``main.compose()``): for every unresolved ``order_submit`` intent, ask the injected
``broker_order_lookup`` (a real broker query in production; a mocked stand-in here — no
live MCP/Alpaca call in this module) for that order's actual status and BACKFILL the
missing result entry so the chain reflects what really happened. Anything it cannot
resolve — a non-order-submit intent, a missing lookup, or a broker that has no record —
is raised as a ``#system`` alert instead of being silently left open (an unresolved
intent must never resolve itself as "assume filled"). It also surfaces the aggregated 1:1
completeness gate (``AuditLog.verify_completeness_aggregate``) so an orphan
``RiskDecision``/``ApprovalRecord``/``Order`` fails loudly at startup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from schemas.models.risk_decision import RiskDecision

from .mode_ladder import ModeLadder
from .state import OrchestratorState, SystemEvent

# The load-bearing models the 1:1 completeness gate covers (SEC-13).
RECONCILED_MODELS = (RiskDecision, ApprovalRecord, Order)


@dataclass
class ReconcileResult:
    resolved_intents: list[int] = field(default_factory=list)
    alerts: list[SystemEvent] = field(default_factory=list)
    completeness_ok: bool = True

    @property
    def ok(self) -> bool:
        return self.completeness_ok and not self.alerts


def _entry_by_seq(state: OrchestratorState, seq: int):
    from sqlalchemy import select

    with state.session_factory() as s:
        row = s.execute(select(AuditEntry).where(AuditEntry.seq == seq)).scalar_one_or_none()
        if row is not None:
            s.expunge(row)
        return row


def _alert(state, clock, *, kind: str, reason: str, refs=None, payload=None) -> SystemEvent:
    mode_str = ModeLadder(state).current().value
    state.audit.append("reconcile_alert", actor="startup_reconcile", refs=refs, payload=payload)
    return SystemEvent("#system", kind, None, reason, mode_str, clock.now())


def reconcile_startup(
    state: OrchestratorState, *,
    broker_order_lookup: Callable[[str], dict | None] | None = None,
    clock=None,
) -> ReconcileResult:
    """Run once at startup. ``broker_order_lookup(order_id) -> {"broker_order_id",
    "status", "filled_qty", "avg_fill_price"} | None`` — ``None`` means "the broker has no
    record of this order" and is itself an alert condition, never a silent resolution."""
    clock = clock or state.clock
    result = ReconcileResult()

    for seq in state.audit.unresolved_intents():
        entry = _entry_by_seq(state, seq)
        if entry is None:
            continue
        op = (entry.payload or {}).get("_op")
        order_id = (entry.refs or {}).get("order_id")
        broker_state = None
        if op == "order_submit" and broker_order_lookup is not None and order_id:
            broker_state = broker_order_lookup(order_id)

        if broker_state is not None:
            state.audit.result(
                entry, op,
                refs={"order_id": order_id, "broker_order_id": broker_state.get("broker_order_id")},
                payload={
                    "status": broker_state.get("status"),
                    "filled_qty": str(broker_state.get("filled_qty", 0)),
                    "avg_fill_price": (None if broker_state.get("avg_fill_price") is None
                                       else str(broker_state.get("avg_fill_price"))),
                    "reconciled": True,
                },
            )
            result.resolved_intents.append(seq)
            continue

        result.alerts.append(_alert(
            state, clock, kind="unresolved_intent",
            reason=(f"intent seq={seq} op={op!r} order_id={order_id!r} has no result and "
                    "could not be reconciled against broker history"),
            refs={"intent_seq": seq}, payload={"op": op, "order_id": order_id},
        ))

    completeness = state.audit.verify_completeness_aggregate(*RECONCILED_MODELS)
    result.completeness_ok = completeness.ok
    if not completeness.ok:
        result.alerts.append(_alert(
            state, clock, kind="completeness_gap",
            reason=(f"startup completeness gate failed: "
                    f"unresolved={completeness.unresolved_intents} "
                    f"orphans={completeness.orphan_results} "
                    f"uncovered={completeness.uncovered_rows}"),
            payload={"unresolved_intents": completeness.unresolved_intents,
                     "orphan_results": completeness.orphan_results,
                     "uncovered_rows": completeness.uncovered_rows},
        ))

    return result
