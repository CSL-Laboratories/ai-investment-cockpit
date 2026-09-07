"""Read outstanding sell quantities from one verified order-history snapshot."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from brokers.order_state_machine import TERMINAL_STATES
from brokers.order_tuple import FIELDS, order_tuple_hash
from orchestrator.state import EVENT_ORDER_FILL_PROGRESS, EVENT_ORDER_TRANSITION
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order


def pending_sell_reservations(state, *, exclude_order_id=None) -> tuple[dict, dict]:
    """Reserve unfilled shares, including a submit whose response was lost.

    The mutable rows supply tuple fields only after their hash matches signed history.
    A missing row, changed quantity/state/fill, or damaged history cannot release shares.
    No reservation is made for an ordinary unsubmitted draft. The gateway's existing
    state lock serializes this read with submission and reconciliation.
    """
    with state.session_factory() as session, session.begin():
        anchor = state.anchor_store.latest() if state.anchor_store is not None else None
        if state.anchor_store is not None and anchor is None:
            raise ValueError("sell reservations require the configured audit anchor")
        verified = (
            state.audit.verify_session_against_anchor(session, *anchor)
            if anchor is not None else state.audit.verify_session(session)
        )
        if not verified.ok:
            raise ValueError("sell reservation history is unverified")
        entries = session.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
        orders = {row.id: row for row in session.execute(select(Order)).scalars()}
        latest: dict[str, AuditEntry] = {}
        submitted: dict[str, int] = {}
        submit_results: dict[int, AuditEntry] = {}
        for entry in entries:
            payload, refs = entry.payload or {}, entry.refs or {}
            if entry.event_type in {EVENT_ORDER_TRANSITION, EVENT_ORDER_FILL_PROGRESS}:
                if refs.get("model") == Order.__tablename__ and refs.get("row_id"):
                    latest[refs["row_id"]] = entry
            if payload.get("_phase") == "intent" and payload.get("_op") == "order_submit":
                if refs.get("order_id"):
                    submitted[refs["order_id"]] = entry.seq
            if payload.get("_phase") == "result" and payload.get("_op") == "order_submit":
                submit_results[refs.get("intent_seq")] = entry
        result: dict[str, Decimal] = {}
        unsubmitted: dict[str, Decimal] = {}
        for order_id in set(latest) | set(orders) | set(submitted):
            row, entry = orders.get(order_id), latest.get(order_id)
            signed = entry.payload if entry is not None else None
            if signed is None:
                raise ValueError("order has no signed sell-reservation history")
            if signed.get("side") != "sell" and getattr(row, "side", None) != "sell":
                continue
            status = signed.get("to")
            # Local veto/expiry/canary is not a broker cancellation. After a submit
            # intent, retain the shares until the signed broker ACK vocabulary proves
            # termination. Poll transitions already carry this explicit actor/reason.
            intent_seq = submitted.get(order_id, 0)
            response = submit_results.get(intent_seq)
            response_refs = (response.refs or {}) if response is not None else {}
            response_payload = (response.payload or {}) if response is not None else {}
            response_binds = (
                response_refs.get("order_id") == order_id
                and bool(response_refs.get("broker_order_id"))
                and response.seq > intent_seq
            ) if response is not None else False
            terminal_ack = response_binds and response_payload.get("status") in {
                "canceled", "cancelled", "expired", "rejected",
            }
            if (entry.actor == "alpaca_paper_reconcile" and signed.get("broker_order_id")
                    and signed.get("reason") in {
                        "broker status poll reported 'canceled'", "broker status poll reported 'cancelled'",
                        "broker status poll reported 'expired'", "broker status poll reported 'rejected'",
                        "broker status poll reported 'filled'",
                    } and entry.seq > intent_seq):
                terminal_ack = True
            broker_confirmed = response_binds and response_payload.get("status") in {
                "accepted", "new", "pending_new", "accepted_for_bidding", "partially_filled",
                "pending_cancel", "pending_replace", "done_for_day", "filled",
            }
            if (entry.actor == "alpaca_paper_reconcile" and signed.get("broker_order_id")
                    and entry.seq > intent_seq and status in {"submitted", "partially_filled"}):
                broker_confirmed = True
            reserved = status in {"approved", "submitted", "partially_filled"} or (
                order_id in submitted and not terminal_ack
            )
            if row is None:
                if reserved or status not in TERMINAL_STATES:
                    raise ValueError("outstanding sell row is missing")
                continue
            if row.state != status or row.side != signed.get("side"):
                raise ValueError("sell row disagrees with signed state")
            if (order_id == exclude_order_id and order_id not in submitted
                    and status in {"proposed", "approved"}):
                continue
            if not reserved:
                if status not in TERMINAL_STATES and status != "proposed":
                    raise ValueError("unknown sell reservation state")
                continue
            if order_tuple_hash({field: getattr(row, field) for field in FIELDS}) != signed.get(
                "order_tuple_hash"
            ) or row.order_tuple_hash != signed.get("order_tuple_hash"):
                raise ValueError("sell quantity differs from signed order tuple")
            try:
                qty = Decimal(str(row.qty))
                filled = Decimal(str(signed.get("filled_qty")))
                mutable_filled = Decimal(str(row.filled_qty))
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise ValueError("sell fill quantity is unavailable") from exc
            if (not qty.is_finite() or not filled.is_finite()
                    or not mutable_filled.is_finite() or not 0 <= filled <= qty
                    or mutable_filled != filled or signed.get("symbol") != row.symbol):
                raise ValueError("sell fill quantity disagrees with signed history")
            result[row.symbol] = result.get(row.symbol, Decimal("0")) + qty - filled
            # A timed-out request may still be in flight and absent from the broker's
            # available quantity. Only a confirmed submission may overlap that figure.
            if not broker_confirmed:
                unsubmitted[row.symbol] = unsubmitted.get(row.symbol, Decimal("0")) + qty - filled
        return result, unsubmitted


def pending_sell_quantities(state, *, exclude_order_id=None) -> dict[str, Decimal]:
    """Total local reservations; callers needing broker overlap use both returned sets."""
    return pending_sell_reservations(state, exclude_order_id=exclude_order_id)[0]
