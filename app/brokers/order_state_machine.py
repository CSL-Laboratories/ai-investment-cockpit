"""WP7 — the order-lifecycle finite state machine (G7; Inv 1/6).

The ONLY sanctioned way to move an ``Order`` row between lifecycle states. Every step:

  * is validated against the pinned legal adjacency (any other move raises
    ``IllegalOrderTransition`` — fail closed); a TERMINAL state never transitions out;
  * writes its covering ``AuditEntry`` BEFORE returning success, atomically with the row
    update, via ``AuditLog.record_state_change(order, EVENT_ORDER_TRANSITION, ...)`` — so
    there is no order-state change without a hash-chained record (Inv 6 / G7).

Legal adjacency (``canary_breach`` is a forced safety terminal reachable from any
non-terminal state — an autonomy-reducing breach edge, like a breaker drop):

    proposed          -> approved, cancelled, expired, rejected            (+ canary_breach)
    approved          -> submitted, cancelled, expired, rejected           (+ canary_breach)
    submitted         -> partially_filled, filled, cancelled, expired, rejected (+ canary_breach)
    partially_filled  -> filled, cancelled, expired                        (+ canary_breach)

Terminal: filled, cancelled, expired, rejected, canary_breach.

``review_equity_order`` / ``place_equity_order`` — the pre-trade gate (all SEC-01/04/07/08
re-checks) and the submit — live on ``brokers.gateway.OrderGateway``; ``place_equity_order``
PROVABLY calls ``review_equity_order`` first and refuses if the review refuses (G7: review
precedes place). This module owns the ``transition`` primitive both build on.

WP8 (Owner C — SEC-07 "Sharpened" closure, docs/security-hardening-backlog.md): every signed
``EVENT_ORDER_TRANSITION`` payload — the birth in ``create_order`` AND every ``transition()``
step — now also carries ``notional`` (str; the row's explicit notional, else qty*limit_price,
else "0" for an un-priced market order — the same rule as
``gateway.OrderGateway._order_notional``) and ``symbol``, both JSON-native strings, so the
SEC-07 pending-exposure cap can be derived entirely from the HMAC-signed ledger instead of the
mutable ``order_row.state`` / ``order_row.notional`` columns.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import inspect, select
from sqlalchemy.orm.attributes import set_committed_value

from core.ids import new_id
from orchestrator.state import (
    EVENT_ORDER_FILL_PROGRESS,
    EVENT_ORDER_TRANSITION,
    OrchestratorState,
)
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from storage.audit import AuditIntegrityError

# ---- pinned legal adjacency (fail closed on anything else) ----------------------------
_CORE_ADJACENCY: dict[str, set[str]] = {
    "proposed": {"approved", "cancelled", "expired", "rejected"},
    "approved": {"submitted", "cancelled", "expired", "rejected"},
    "submitted": {"partially_filled", "filled", "cancelled", "expired", "rejected"},
    "partially_filled": {"filled", "cancelled", "expired"},
}
TERMINAL_STATES: frozenset[str] = frozenset(
    {"filled", "cancelled", "expired", "rejected", "canary_breach"}
)
# Every non-terminal state may be force-dropped to the canary_breach safety terminal.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    src: frozenset(dsts | {"canary_breach"}) for src, dsts in _CORE_ADJACENCY.items()
}


class IllegalOrderTransition(Exception):
    """An illegal lifecycle edge, stale caller, or row inconsistent with signed history."""


def legal_order_transition(src: str, dst: str) -> bool:
    """Pure adjacency predicate. A terminal source has no outgoing edges."""
    if src in TERMINAL_STATES:
        return False
    return dst in LEGAL_TRANSITIONS.get(src, frozenset())


def _dec(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _payload_notional(order: Order) -> str:
    """JSON-native notional for the SIGNED transition payload (WP8 SEC-07 "Sharpened"
    closure): the order row's explicit notional, else qty*limit_price, else "0" for an
    un-priced market order — the SAME rule as ``gateway.OrderGateway._order_notional``, so
    the chain-anchored pending-exposure sum folds exactly the value the cap would use."""
    if order.notional is not None:
        n = _dec(order.notional)
        if n.is_finite():
            return str(n)
    if order.limit_price is not None:
        n = _dec(order.qty) * _dec(order.limit_price)
        if n.is_finite():
            return str(n)
    return "0"


def _row_values(order: Order) -> dict:
    return {column.name: getattr(order, column.name) for column in Order.__table__.columns}


def _comparable(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value


def _current_order(st: OrchestratorState, session, order: Order) -> Order:
    """BUG-125: check caller, durable row and signed predecessor in ONE read/write snapshot.

    Verify before staging any write (never publish a tentative verification floor). SQLite's
    pinned snapshot cannot upgrade after a competing commit; that loser must reload, not retry
    a stale fragment. Pending approval binding is the existing gateway/reconciler convention.
    """
    verified = st.audit.verify_session(session)
    if not verified.ok:
        raise AuditIntegrityError(verified.error or "invalid order history")
    current = session.get(Order, order.id)
    if current is None:
        raise IllegalOrderTransition(f"order {order.id} is missing from durable history")
    expected = _row_values(order)
    approval_history = inspect(order).attrs.approval_id.history
    if approval_history.has_changes() and approval_history.deleted:
        expected["approval_id"] = approval_history.deleted[0]
    if any(_comparable(value) != _comparable(getattr(current, name))
           for name, value in expected.items()):
        raise IllegalOrderTransition(f"stale order {order.id}; reload before transitioning")
    prior = session.execute(
        select(AuditEntry).where(
            AuditEntry.event_type.in_((EVENT_ORDER_TRANSITION, EVENT_ORDER_FILL_PROGRESS)),
            AuditEntry.refs["model"].as_string() == Order.__tablename__,
            AuditEntry.refs["row_id"].as_string() == order.id,
        ).order_by(AuditEntry.seq.desc()).limit(1)
    ).scalar_one_or_none()
    if prior is None:
        raise IllegalOrderTransition(f"order {order.id} has no signed predecessor")
    p = prior.payload
    fields = {
        "to": current.state, "proposal_id": current.proposal_id,
        "risk_decision_id": current.risk_decision_id,
        "order_tuple_hash": current.order_tuple_hash, "symbol": current.symbol,
        "side": current.side, "approval_id": current.approval_id,
        "broker_order_id": current.broker_order_id,
    }
    # Legacy births may omit NULL binding IDs; an unsigned non-null value still refuses.
    birth = (prior.event_type == EVENT_ORDER_TRANSITION and "from" in p
             and p["from"] is None and p.get("to") == "proposed")
    if any((name not in p and not (birth and name in {"approval_id", "broker_order_id"}))
           or p.get(name) != value for name, value in fields.items()):
        raise IllegalOrderTransition(f"order {order.id} disagrees with its signed predecessor")
    for name, value in {"notional": _payload_notional(current),
                        "filled_qty": current.filled_qty,
                        "avg_fill_price": current.avg_fill_price}.items():
        signed = p.get(name)
        if name not in p or (value is None and signed is not None) or (
            value is not None and (not _dec(signed).is_finite() or _dec(signed) != _dec(value))
        ):
            raise IllegalOrderTransition(f"order {order.id} has inconsistent signed {name}")
    return current


def _transition_payload(order: Order, from_state: str | None, reason: str | None) -> dict:
    return {
        "from": from_state, "to": order.state, "reason": reason,
        "proposal_id": order.proposal_id, "risk_decision_id": order.risk_decision_id,
        "order_tuple_hash": order.order_tuple_hash, "approval_id": order.approval_id,
        "notional": _payload_notional(order), "symbol": order.symbol, "side": order.side,
        "broker_order_id": order.broker_order_id, "filled_qty": str(_dec(order.filled_qty)),
        "avg_fill_price": None if order.avg_fill_price is None else str(_dec(order.avg_fill_price)),
    }


def _publish(order: Order, committed: Order) -> Order:
    # Preserve caller identity and clear ORM dirty history only AFTER successful commit.
    for name, value in _row_values(committed).items():
        set_committed_value(order, name, value)
    return order


def create_order(
    st: OrchestratorState,
    *,
    proposal_id: str,
    risk_decision_id: str,
    account: str,
    symbol: str,
    side: str,
    qty,
    order_type: str,
    tif: str,
    order_tuple_hash: str,
    limit_price=None,
    notional=None,
    approval_id: str | None = None,
    id: str | None = None,
    actor: str | None = None,
    clock=None,
) -> Order:
    """Create the initial ``Order`` row in state ``proposed`` and audit the birth
    transition (``None -> proposed``) atomically (Inv 6). Returns the detached row."""
    clock = clock or st.clock
    now = clock.now()
    order = Order(
        id=id or new_id("ord"),
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        approval_id=approval_id,
        account=account,
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        limit_price=limit_price,
        tif=tif,
        notional=notional,
        order_tuple_hash=order_tuple_hash,
        state="proposed",
        filled_qty=0,
        created_at=now,
        updated_at=now,
    )
    st.audit.record_state_change(
        order, EVENT_ORDER_TRANSITION, actor=actor,
        payload=lambda row: _transition_payload(row, None, "order created"),
    )
    return order


def transition(
    st: OrchestratorState,
    order: Order,
    to_state: str,
    *,
    actor: str | None = None,
    reason: str | None = None,
    clock=None,
    proposal_id: str | None = None,
    risk_decision_id: str | None = None,
    order_tuple_hash: str | None = None,
    broker_order_id: str | None = None,
    filled_qty=None,
    avg_fill_price=None,
) -> Order:
    """Advance ``order`` to ``to_state``. Validates the move against the pinned adjacency
    (raises ``IllegalOrderTransition`` on any illegal move or a move out of a terminal
    state) and writes the covering ``EVENT_ORDER_TRANSITION`` AuditEntry BEFORE returning —
    atomically with the row update, so no order-state change is unaudited (G7)."""
    clock = clock or st.clock
    from_state = order.state

    def prepare(session):
        nonlocal from_state
        current = _current_order(st, session, order)
        from_state = current.state
        if not legal_order_transition(current.state, to_state):
            raise IllegalOrderTransition(
                f"illegal order transition {current.state!r} -> {to_state!r} (order {order.id})"
            )
        for name, override in (("proposal_id", proposal_id),
                               ("risk_decision_id", risk_decision_id),
                               ("order_tuple_hash", order_tuple_hash)):
            if override is not None and override != getattr(current, name):
                raise IllegalOrderTransition(f"transition {name} disagrees with order {order.id}")
        current.state = to_state
        current.approval_id = order.approval_id
        current.updated_at = clock.now()
        if reason is not None:
            current.reason = reason
        if broker_order_id is not None:
            current.broker_order_id = broker_order_id
        if filled_qty is not None:
            current.filled_qty = filled_qty
        if avg_fill_price is not None:
            current.avg_fill_price = avg_fill_price
        return current

    committed, _ = st.audit.record_state_change(
        order, EVENT_ORDER_TRANSITION, actor=actor, prepare=prepare,
        payload=lambda row: _transition_payload(row, from_state, reason),
    )
    return _publish(order, committed)


def record_fill_progress(
    st: OrchestratorState,
    order: Order,
    *,
    filled_qty,
    avg_fill_price=None,
    actor: str | None = None,
    reason: str | None = None,
    clock=None,
) -> Order:
    """Persist increasing cumulative fill data while the FSM stays ``partially_filled``.

    A broker can report several partial fills before the terminal fill.  Treating those as an
    illegal state self-transition either loses real fill progress or tempts callers to update
    the row directly.  This primitive keeps the state fixed, requires strictly increasing sane
    cumulative quantity, and writes the row plus its dedicated audit entry atomically.
    """
    clock = clock or st.clock
    old_qty = None

    def prepare(session):
        nonlocal old_qty
        current = _current_order(st, session, order)
        if current.state != "partially_filled":
            raise IllegalOrderTransition(
                f"fill progress requires 'partially_filled' state (order {order.id})"
            )
        new_qty = _dec(filled_qty)
        old_qty = _dec(current.filled_qty)
        total_qty = _dec(current.qty)
        if not all(value.is_finite() for value in (new_qty, old_qty, total_qty)):
            raise ValueError("fill progress quantities must be finite")
        if not old_qty < new_qty < total_qty:
            raise ValueError("fill progress must strictly increase below total order quantity")
        price = None if avg_fill_price is None else _dec(avg_fill_price)
        if price is not None and (not price.is_finite() or price <= 0):
            raise ValueError("average fill price must be positive and finite")
        current.filled_qty = new_qty
        current.approval_id = order.approval_id
        if price is not None:
            current.avg_fill_price = price
        current.updated_at = clock.now()
        if reason is not None:
            current.reason = reason
        return current

    def payload(row):
        # Numeric storage may round away a tiny increase or round up to the total.
        # Refuse the whole write if the actual persisted values violate the progress edge.
        if not old_qty < _dec(row.filled_qty) < _dec(row.qty):
            raise ValueError("stored fill progress must strictly increase below total quantity")
        if row.avg_fill_price is not None and _dec(row.avg_fill_price) <= 0:
            raise ValueError("stored average fill price must be positive")
        return _transition_payload(row, "partially_filled", reason)

    committed, _ = st.audit.record_state_change(
        order, EVENT_ORDER_FILL_PROGRESS, actor=actor, prepare=prepare, payload=payload,
    )
    return _publish(order, committed)
