"""WP7 — the ``Order`` SQLAlchemy model: the durable order-lifecycle row.

One row per order that reaches (or is refused at) the broker seam. The row is the
authoritative lifecycle record the ``brokers/order_state_machine`` FSM advances; every
transition is written together with its covering ``AuditEntry`` via
``AuditLog.record_state_change`` (Inv 6 / G7), so no state field ever moves without a
hash-chained record.

SEC-07: ``order_tuple_hash`` is the canonical hash of the resolved order tuple
``{symbol, side, qty, order_type, limit_price, tif}`` (see ``brokers.order_tuple``). The
gateway refuses any submit whose recomputed hash != the approving ``ApprovalRecord``'s
``order_tuple_hash`` — a Modify / re-price yields a different tuple, hence a different hash,
and the old approval can no longer authorize it.

Auto-registered on ``Base.metadata`` by ``schemas.models.load_all`` (pkgutil scan); nothing
edits ``schemas/models/__init__.py``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base

# The lifecycle states (kept here as the single source of truth the FSM imports).
ORDER_STATES = (
    "proposed",
    "approved",
    "submitted",
    "partially_filled",
    "filled",
    "cancelled",
    "expired",
    "rejected",
    "canary_breach",
)


class Order(Base):
    __tablename__ = "order_row"          # 'order' is a SQL reserved word

    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Provenance / SEC-07 binding chain: proposal -> risk decision -> approval.
    proposal_id: Mapped[str] = mapped_column(String, index=True)
    risk_decision_id: Mapped[str] = mapped_column(String, index=True)
    approval_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    account: Mapped[str] = mapped_column(String, index=True)

    # Resolved order tuple fields (canonicalized by brokers.order_tuple).
    symbol: Mapped[str] = mapped_column(String, index=True)
    side: Mapped[str] = mapped_column(String)                       # buy | sell
    qty: Mapped[float] = mapped_column(Numeric(28, 10))
    order_type: Mapped[str] = mapped_column(String)                 # market|limit|stop|stop_limit
    limit_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    tif: Mapped[str] = mapped_column(String)                        # day|gtc|ioc|fok|opg|cls
    notional: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)

    # SEC-07 approval<->submit binding.
    order_tuple_hash: Mapped[str] = mapped_column(String, index=True)

    # Lifecycle.
    state: Mapped[str] = mapped_column(String, index=True)          # one of ORDER_STATES
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    filled_qty: Mapped[float] = mapped_column(Numeric(28, 10), default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
