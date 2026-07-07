"""WP1a — RiskDecision stored SQLAlchemy model.

Persisted output of the deterministic risk engine (WP4). One row per evaluated
TradeProposal. The risk engine is a pure function (invariant 5); this model is
the durable record of a single evaluation: the decision, per-check results, and
the notional ceiling the engine will permit. No order may be placed unless a
RiskDecision with ``decision == "approved"`` exists for the proposal
(invariant 1). See docs/cockpit-plan-original.md §4 and
docs/build-scaffold-ultracode.md WP1a / WP4.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class RiskDecision(Base):
    __tablename__ = "risk_decision"

    # opaque string ids
    id: Mapped[str] = mapped_column(String, primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String, index=True)

    # enum: approved / rejected / needs_human
    decision: Mapped[str] = mapped_column(String)

    # nested per-check structures -> JSON
    checks: Mapped[dict] = mapped_column(JSON, default=dict)
    failed_checks: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)

    # money -> Numeric(18, 2), nullable
    max_allowed_notional: Mapped[float | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )

    human_required: Mapped[bool] = mapped_column(Boolean, default=False)

    # timestamps -> DateTime(timezone=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
