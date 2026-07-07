"""WP1a — ``AuditEntry`` SQLAlchemy model.

The hash-chained, tamper-evident ledger (build-scaffold WP2; Inv 6). Every
state-changing action appends one row whose ``entry_hash`` links to the prior
row's hash via ``prev_hash`` (genesis row has ``prev_hash = NULL``), and a
strictly-monotonic ``seq`` makes tail-truncation detectable (SEC-11). The
concrete integrity mechanism — HMAC keying (SEC-10), canonical serialization /
hash coverage (SEC-12), two-phase atomic write and external anchoring
(SEC-13/SEC-11) — lives in the WP2 storage/audit layer that operates on this
model; this module is only the shape of a ledger row.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class AuditEntry(Base):
    __tablename__ = "audit_entry"

    # Opaque string primary key (id convention).
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Strictly-monotonic sequence number, unique — covered by the chain HMAC so
    # a truncated tail (head seq < last anchor) is detectable (SEC-11).
    seq: Mapped[int] = mapped_column(Integer, unique=True, index=True)

    # Chain links (hex). prev_hash is NULL only on the genesis row.
    prev_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_hash: Mapped[str] = mapped_column(String)

    # What happened and who caused it. actor is nullable for system-originated
    # events (scheduler, breaker trips) with no human/agent actor.
    event_type: Mapped[str] = mapped_column(String, index=True)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)

    # Structured references (raw_ref / proposal_id / risk_decision_id / order_id
    # / interaction + message IDs, ...) and the load-bearing event body.
    refs: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
