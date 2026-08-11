"""RW-15 (minimal, paper lane) — the ORDER-STATUS RECONCILE TICK that drains resting orders.

WHY THIS EXISTS (the SEC-07 exhaustion trap). ``brokers.gateway`` consumes the broker's ACK at
submit time, but only for statuses that are DEFINITIVE about the order's fate
(``_BROKER_STATUS_TO_STATE``). A paper LIMIT order that does not cross comes back ``accepted``
— absent from that map by design, because "accepted" says nothing about the fill — so the row
stays ``submitted``, which is an OPEN state for ``OrderGateway._pending_ok``. Nothing else in
the tree ever moved it. RW-16 prices its copy orders as LIMIT at the gate quote, so a RESTING
order is the NORMAL path, not an edge case: the pending-exposure sum then ratchets up
monotonically until the SEC-07 cap is full, after which the gate refuses EVERY subsequent order
forever. Under RW-17 there is no human in the queue to notice, so the lane simply looks like a
quiet day. The gateway's own BUG-052 note names this module's absence as the open end of that
loop ("indeterminate ones stay 'submitted' for the (future) order-status poll to resolve"), and
``orchestrator/reconcile.py`` defers to "a future status poll" in the same words.

SCOPE — deliberately the MINIMUM RW-15 the paper lane needs, and no more. The register's RW-15
row is broader (real-broker reconciliation, healing ``ambiguous_aborts``, retiring
``main._no_broker_history``); those stay OPEN and are not attempted here. DEC-04 (counting only
the REMAINING notional after a partial fill) needs a NEW signed payload field and is likewise
untouched: a partial reported here advances the FSM but never re-sizes the signed notional.

THIS IS NOT A SECOND ORDER PATH (Inv 1). Nothing here reaches a broker with an ORDER: the only
broker interaction is a READ of an order's status, injected as ``status_fn`` exactly the way
``orchestrator.reconcile`` injects ``broker_history_fn``. No token is minted or consumed, no
approval is read or consumed, no risk verdict is re-run, and ``broker.submit_order`` is never
called. The state moves it can make are pinned to ``SETTLE_STATES`` — every one of them DRAINS
exposure — and a module-level assertion refuses to let ``approved``/``submitted`` into that set,
so this tick can never advance an order TOWARD a broker. It reuses the gateway's EXISTING ACK
machinery verbatim (``_sanitize_broker_response`` -> ``_BROKER_STATUS_TO_STATE`` ->
``_ack_consistent`` -> ``order_state_machine.transition``) rather than re-deciding any of it;
those three are imported private-by-name on purpose, so a change to the submit-time ACK rules
changes the poll's rules in the same edit.

FAIL-CLOSED, IN THE SAME DIRECTION AS ``_pending_ok`` (which is NOT weakened, or touched):
leaving an order OPEN over-counts exposure and can only ever refuse more trading, while
settling one that is still live at the broker under-counts it and could let a duplicate through.
So every ambiguity leaves the order open and is returned as a NAMED outcome:

  * ``status_fn`` raises / returns a non-dict     -> ``read_failed``   (order stays open)
  * a reply whose ``broker_order_id`` is not the one we asked about -> ``id_mismatch``
  * a status absent from ``_BROKER_STATUS_TO_STATE`` (``accepted``, ``new``, ``pending_new``)
                                                  -> ``indeterminate``
  * fill data inconsistent with the claimed status -> ``ack_ignored`` (audited, as at submit)
  * an unverified audit chain                      -> the WHOLE tick is skipped
  * a transient DB failure on the write            -> ``transition_failed``

A fill is NEVER invented, and a status string alone never drains the cap — ``_ack_consistent``
still requires ``filled_qty == qty`` for ``filled`` and ``0 < filled_qty < qty`` for a partial.

One sick order never costs the others: every order is isolated, and the broker READ happens
OUTSIDE ``st.lock`` so a hung network call cannot block the global orchestrator lock (the
gateway's submitted path takes the same care).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

# The gateway's ACK machinery, reused rather than re-decided — see the module docstring.
from brokers.gateway import (
    _BROKER_STATUS_TO_STATE,
    _ack_consistent,
    _sanitize_broker_response,
)
from brokers.order_state_machine import legal_order_transition, transition
from schemas.models.order import Order

log = logging.getLogger("cockpit.order_status_poll")

#: The order states this tick will ASK the broker about: post-submit and still open. Anything
#: earlier has not reached a broker (asking about it would be a category error) and anything
#: terminal is settled.
POLLABLE_STATES: frozenset[str] = frozenset({"submitted", "partially_filled"})

#: The ONLY states this tick will move an order INTO. Every one drains (or reduces) pending
#: exposure; none of them advances an order toward a broker.
SETTLE_STATES: frozenset[str] = frozenset(
    {"filled", "partially_filled", "cancelled", "expired", "rejected"}
)

# Inv 1 pin, asserted at import: the poll can only ever reach states the gateway's OWN ACK map
# already recognises, and can NEVER reach a pre-broker state. A future edit that tried to let
# this module advance an order to 'approved'/'submitted' — i.e. build a second order path —
# fails here, at import, not in review.
assert SETTLE_STATES <= set(_BROKER_STATUS_TO_STATE.values())
assert not (SETTLE_STATES & {"proposed", "approved", "submitted"})

#: Audit event for an ACK whose fill data contradicts its status. Spelled exactly as the
#: gateway's submit-time F3 branch spells it, so both land in one query.
EVENT_ACK_IGNORED = "order_ack_ignored"

DEFAULT_ACTOR = "order_status_poll"

#: Every named per-order outcome. Exhaustive by construction (a test pins it).
ALL_POLL_OUTCOMES: frozenset[str] = frozenset({
    "settled",              # the broker was definitive and the FSM advanced
    "indeterminate",        # still resting: no definitive broker status (the 'accepted' case)
    "ack_ignored",          # definitive status, fill data contradicts it -> stays open, audited
    "no_change",            # the mapped state is not a legal move from here (e.g. partial->partial)
    "read_failed",          # status_fn raised / returned an unusable shape
    "id_mismatch",          # the reply is about a DIFFERENT broker order
    "not_open",             # the row moved (or vanished) between the read and the write
    "transition_failed",    # transient DB failure on the write; retried next tick
})


@dataclass(frozen=True)
class OrderStatusOutcome:
    """What the tick learned about ONE order. ``settled_to`` is None unless the FSM moved."""

    order_id: str
    broker_order_id: str | None
    broker_status: str | None
    outcome: str
    settled_to: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class OrderStatusReport:
    """The outcome of one reconcile tick (frozen — a settled verdict).

    ``ok`` is the CLEAN-TICK verdict, not "nothing was open": a tick that read every open order
    and settled none of them is ok (they are genuinely still resting). ``ok`` goes False when the
    tick could not do its job — a skipped tick, a failed broker read, or a failed write — because
    those are precisely the conditions under which the SEC-07 cap keeps ratcheting.
    """

    outcomes: tuple[OrderStatusOutcome, ...] = ()
    skipped: str | None = None

    @property
    def polled(self) -> int:
        return len(self.outcomes)

    @property
    def settled(self) -> tuple[OrderStatusOutcome, ...]:
        return tuple(o for o in self.outcomes if o.outcome == "settled")

    @property
    def still_open(self) -> tuple[OrderStatusOutcome, ...]:
        return tuple(o for o in self.outcomes if o.outcome != "settled")

    @property
    def ok(self) -> bool:
        if self.skipped is not None:
            return False
        return not any(
            o.outcome in ("read_failed", "transition_failed") for o in self.outcomes
        )


def _open_candidates(st, accounts) -> list[tuple[str, str]]:
    """``(order_id, broker_order_id)`` for every persisted order that is post-submit, still
    open, and carries a broker id. Read under the lock, returned as plain tuples so nothing
    detached is dereferenced later."""
    with st.lock, st.session_factory() as s:
        rows = s.execute(select(Order).where(Order.state.in_(sorted(POLLABLE_STATES)))).scalars().all()
        return [
            (row.id, str(row.broker_order_id))
            for row in rows
            if row.broker_order_id and (accounts is None or row.account in accounts)
        ]


def _apply(st, order_id, broker_order_id, result, *, actor, clock) -> OrderStatusOutcome:
    """Apply ONE sanitized broker reply under ``st.lock``, re-reading the row first (it may
    have moved since the read phase — the gateway makes the same re-read before submitting)."""
    status = result["status"]
    with st.lock:
        with st.session_factory() as s:
            row = s.get(Order, order_id)
            if row is not None:
                s.expunge(row)
        if row is None or row.state not in POLLABLE_STATES:
            return OrderStatusOutcome(order_id, broker_order_id, status, "not_open",
                                      detail=f"state {getattr(row, 'state', None)!r}")

        ack_state = _BROKER_STATUS_TO_STATE.get((status or "").lower())
        if ack_state is None or ack_state not in SETTLE_STATES:
            # 'accepted' / 'new' / 'pending_new' / anything unknown: the order is still resting
            # and stays OPEN. Never invent a fate the broker did not report.
            return OrderStatusOutcome(order_id, broker_order_id, status, "indeterminate")
        if not legal_order_transition(row.state, ack_state):
            return OrderStatusOutcome(order_id, broker_order_id, status, "no_change",
                                      detail=f"{row.state!r} -> {ack_state!r} is not a legal move")
        if not _ack_consistent(ack_state, result, row):
            # Identical rule and identical audit event as the gateway's submit-time F3 branch:
            # a bare status string must not manufacture cap headroom.
            st.audit.append(
                EVENT_ACK_IGNORED, actor=actor, refs={"order_id": order_id},
                payload={"status": status,
                         "filled_qty": (None if result["filled_qty"] is None
                                        else str(result["filled_qty"])),
                         "qty": str(row.qty),
                         "source": "order_status_poll",
                         "reason": "fill data inconsistent with claimed status (F3)"},
            )
            return OrderStatusOutcome(order_id, broker_order_id, status, "ack_ignored")
        try:
            transition(
                st, row, ack_state, actor=actor,
                reason=f"order-status poll: broker reported {status!r} (RW-15)",
                clock=clock,
                broker_order_id=broker_order_id,
                filled_qty=result["filled_qty"],
                avg_fill_price=result["avg_fill_price"],
            )
        except (IntegrityError, OperationalError) as exc:
            # Fail-safe: the order stays open (over-counted) and the next tick retries.
            log.warning("order %s: poll transition to %r failed transiently (%s); staying open",
                        order_id, ack_state, exc)
            return OrderStatusOutcome(order_id, broker_order_id, status, "transition_failed",
                                      detail=str(exc)[:200])
        return OrderStatusOutcome(order_id, broker_order_id, status, "settled",
                                  settled_to=ack_state)


def reconcile_order_status(
    st,
    *,
    status_fn,
    actor: str = DEFAULT_ACTOR,
    clock=None,
    accounts=None,
) -> OrderStatusReport:
    """One RW-15 reconcile tick: settle every resting/partial order the broker is DEFINITIVE
    about, through the gateway's existing ACK machinery.

    ``status_fn(broker_order_id) -> dict`` is INJECTED (the ``orchestrator.reconcile`` posture),
    so this module imports no concrete broker and needs no network in tests. It must return the
    same shape ``broker.submit_order`` does — ``{broker_order_id, status, filled_qty,
    avg_fill_price}`` — and is validated through the gateway's own
    ``_sanitize_broker_response`` before anything dereferences it.

    ``accounts`` optionally restricts the tick to given account names; pass the account served by
    THIS ``status_fn`` when more than one broker is composed, so a reply is never applied to an
    order the reader knows nothing about.

    Returns an ``OrderStatusReport``. NEVER raises for an ordinary per-order fault: one
    unreadable order must not strand every other resting order behind it (that is the same
    ratchet this module exists to break).
    """
    clock = clock or st.clock

    # Fail closed BEFORE writing into a chain that does not verify. Costs nothing operationally:
    # ``_pending_ok`` already refuses every order while the chain is unhealed, so there is no cap
    # headroom to win here — and appending settlements onto a tampered chain would only make the
    # damage harder to read.
    if not st.audit.verify_chain().ok:
        return OrderStatusReport(skipped="chain_unverified")

    candidates = _open_candidates(st, accounts)

    outcomes: list[OrderStatusOutcome] = []
    for order_id, broker_order_id in candidates:
        # The broker READ happens OUTSIDE st.lock: a hung status call must not block the global
        # orchestrator lock (and with it the human order path and every audit write).
        try:
            result = _sanitize_broker_response(status_fn(broker_order_id))
        except Exception as exc:                     # noqa: BLE001 - fail closed: stays open
            log.warning("order %s: broker status read failed (%s); order stays open",
                        order_id, exc)
            outcomes.append(OrderStatusOutcome(order_id, broker_order_id, None, "read_failed",
                                               detail=str(exc)[:200]))
            continue
        reply_id = result["broker_order_id"]
        if reply_id is not None and reply_id != broker_order_id:
            # A record about a DIFFERENT order is not evidence about this one (the same
            # fail-closed rule ``orchestrator.reconcile._order_landed`` applies to healing).
            outcomes.append(OrderStatusOutcome(order_id, broker_order_id, result["status"],
                                               "id_mismatch", detail=str(reply_id)[:128]))
            continue
        try:
            outcomes.append(_apply(st, order_id, broker_order_id, result,
                                   actor=actor, clock=clock))
        except Exception as exc:                     # noqa: BLE001 - one order never kills a tick
            log.exception("order %s: order-status reconcile failed: %r", order_id, exc)
            outcomes.append(OrderStatusOutcome(order_id, broker_order_id, result["status"],
                                               "transition_failed", detail=repr(exc)[:200]))
    return OrderStatusReport(outcomes=tuple(outcomes))


def run_order_status_poll(state, clock=None, *, status_fn=None, accounts=None):
    """Scheduler-shaped entry point (the ``orchestrator.scheduler`` job convention:
    ``(state, clock)`` plus injected collaborators, never raising).

    Registered by the integrator as the ``order_status_poll`` job — see the note in
    ``brokers/order_status_poll`` docs and the RW-15 row in ``docs/REGISTER.md``. With no
    ``status_fn`` composed it is a named no-op, so a deployment that wired no status reader gets
    a legible skip rather than a silent one.
    """
    clock = clock or state.clock
    now = clock.now()
    if status_fn is None:
        return {"skipped": "no_status_fn", "at": now.isoformat()}
    try:
        report = reconcile_order_status(state, status_fn=status_fn, clock=clock,
                                        accounts=accounts)
    except Exception as exc:                         # noqa: BLE001 - a bad tick never wedges the job
        log.exception("RW-15 order-status poll FAILED: %r", exc)
        return {"error": repr(exc), "at": now.isoformat()}
    return {
        "polled": report.polled,
        "settled": len(report.settled),
        "ok": report.ok,
        "skipped": report.skipped,
        "outcomes": [o.outcome for o in report.outcomes],
        "at": now.isoformat(),
    }
