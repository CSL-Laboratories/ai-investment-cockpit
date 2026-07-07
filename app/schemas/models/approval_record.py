"""WP1a — ``ApprovalRecord`` SQLAlchemy model.

Records a single human adjudication of a trade proposal via the Discord control
plane (draft-discord-control-plane §Discord surface): every Approve / Reject /
Modify / Interrogate(→veto) button press is verified against the operator's
Discord user ID and persisted here with the interaction/message/device metadata
and the moment the decision was made.

SEC-07 (security-hardening-backlog): the record carries a ``single_use_consumed``
flag and an ``order_tuple_hash`` — the hash of the fully-resolved order tuple
``{symbol, side, qty, order_type, limit_price, tif}`` the approval authorized —
so ``brokers/`` refuses any order whose tuple hash != the approved hash (a
Modify / re-price mints a NEW proposal + approval) and so a consumed, single-use
approval cannot be replayed.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class ApprovalRecord(Base):
    __tablename__ = "approval_record"

    # A Discord interaction id is single-use: a UNIQUE backstop makes the approval-idempotency
    # check-then-act atomic even across processes (SQLite treats NULLs as distinct, so
    # desktop-fallback approvals with no interaction_id are unconstrained). See
    # bridge/approval.record_decision (which also holds the in-process lock).
    __table_args__ = (UniqueConstraint("interaction_id", name="uq_approval_interaction_id"),)

    # Opaque string PK.
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # The proposal this decision adjudicates, and the exact RiskDecision the
    # approval is minted against (SEC-07 binds approval → RiskDecision).
    proposal_id: Mapped[str] = mapped_column(String, index=True)
    risk_decision_id: Mapped[str] = mapped_column(String, index=True)

    # Verified Discord operator user ID that pressed the button.
    actor_user_id: Mapped[str] = mapped_column(String, index=True)

    # approve / reject / modify / veto (approval user_decision enum).
    user_decision: Mapped[str] = mapped_column(String)

    # Where the decision came from (e.g. "discord", "modal", "desktop").
    surface: Mapped[str] = mapped_column(String)

    # Discord interaction / message provenance + originating device (nullable —
    # e.g. a desktop-fallback approval has no Discord interaction).
    interaction_id: Mapped[str | None] = mapped_column(String, nullable=True)
    message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    device: Mapped[str | None] = mapped_column(String, nullable=True)

    # SEC-07: hex hash of the fully-resolved order tuple the approval authorized.
    order_tuple_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    # SEC-07: single-use approval — set once the approval has been consumed by a
    # submit so it cannot be replayed.
    single_use_consumed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Free-text operator note (nullable).
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    # When the operator made the decision.
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Row creation timestamp (every model carries this).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
