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

    # QT-06 — the execution verdict (quant/verify.py) this decision was evaluated against.
    #
    # It lives HERE, on the deterministic risk record, rather than only in the audit payload,
    # for one structural reason: SEC-17 says no risk value an operator reads may originate
    # from a model-emitted field. The operator embed is allowed to render the RiskDecision and
    # nothing else, so putting the verdict on this row is what makes it renderable AT ALL —
    # anywhere else and the embed would have to accept it from a caller who might have taken
    # it off the model-authored proposal.
    #
    # NULLABLE for uncomposed/test callers. Production paper composition persists W-21's
    # L1/ADV/volatility verdict; its edge state remains explicitly "unavailable" until a
    # deterministic strategy source is separately selected. Null is never "evaluated and fine".
    execution_verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)

    # timestamps -> DateTime(timezone=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
