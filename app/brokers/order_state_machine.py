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

from decimal import Decimal, InvalidOperation

from core.ids import new_id
from orchestrator.state import EVENT_ORDER_TRANSITION, OrchestratorState
from schemas.models.order import Order

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
    """An order-state move outside the pinned legal adjacency (or out of a terminal state)."""


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
    payload = {
        "from": None,
        "to": "proposed",
        "reason": "order created",
        "proposal_id": proposal_id,
        "risk_decision_id": risk_decision_id,
        "order_tuple_hash": order_tuple_hash,
        # WP8 SEC-07 "Sharpened" closure: the SIGNED birth payload carries the notional +
        # symbol so pending exposure is derivable from the ledger, not mutable columns.
        "notional": _payload_notional(order),
        "symbol": symbol,
        # RW-17: the SIDE, so a reader can tell an opening trade from a closing one from the
        # signed ledger alone. ``copytrade/budget.py``'s open-position rail needs it to release
        # a slot when a position is closed OUTSIDE the auto lane (by the operator, by hand);
        # without it the rail replayed only its own adjudications and ratcheted toward a
        # permanent floor it could never come back from. Entries written before this field
        # existed simply carry no ``side`` and are ignored by that reader.
        "side": side,
    }
    st.audit.record_state_change(order, EVENT_ORDER_TRANSITION, actor=actor, payload=payload)
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
    if not legal_order_transition(from_state, to_state):
        raise IllegalOrderTransition(
            f"illegal order transition {from_state!r} -> {to_state!r} (order {order.id})"
        )
    order.state = to_state
    order.updated_at = clock.now()
    if reason is not None:
        order.reason = reason
    if broker_order_id is not None:
        order.broker_order_id = broker_order_id
    if filled_qty is not None:
        order.filled_qty = filled_qty
    if avg_fill_price is not None:
        order.avg_fill_price = avg_fill_price
    payload = {
        "from": from_state,
        "to": to_state,
        "reason": reason,
        "proposal_id": proposal_id if proposal_id is not None else order.proposal_id,
        "risk_decision_id": (
            risk_decision_id if risk_decision_id is not None else order.risk_decision_id
        ),
        "order_tuple_hash": (
            order_tuple_hash if order_tuple_hash is not None else order.order_tuple_hash
        ),
        # WP8 SEC-07 "Sharpened" closure: every SIGNED transition payload carries notional +
        # symbol so pending exposure is derivable from the ledger, not mutable columns.
        "notional": _payload_notional(order),
        "symbol": order.symbol,
        # RW-17: the SIDE — see ``create_order`` for why the open-position rail needs it.
        "side": order.side,
    }
    # record_state_change UPDATEs the existing row AND appends its audit entry in ONE txn.
    st.audit.record_state_change(order, EVENT_ORDER_TRANSITION, actor=actor, payload=payload)
    return order
