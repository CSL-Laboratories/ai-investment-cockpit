"""WP8 — ProposalRecord stored model (contract Owner A).

One persisted TradeProposal draft (docs/build-scaffold-ultracode.md WP8 proposals).
``id`` equals the payload's ``proposal_id``; ``payload`` is the schema-validated
TradeProposal dict. ``status`` walks {draft, needs_human, flagged, posted, quarantined};
``flagged`` mirrors the SEC-14 grounding outcome (a flagged proposal is review-only and
never reaches the auto-veto path). Rows persist ONLY via
``st.audit.record_state_change(row, EVENT_PROPOSAL, ...)`` (SEC-13).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class ProposalRecord(Base):
    __tablename__ = "proposal_record"

    # Opaque string primary key == payload["proposal_id"].
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Uppercase US ticker the proposal concerns.
    symbol: Mapped[str] = mapped_column(String, index=True)

    # "buy" | "sell".
    side: Mapped[str] = mapped_column(String)

    # Lifecycle: draft | needs_human | flagged | posted | quarantined.
    status: Mapped[str] = mapped_column(String, default="draft")

    # Schema-validated TradeProposal output.
    payload: Mapped[dict] = mapped_column(JSON)

    # AgentJob id of the deep call that drafted it.
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # RiskDecision id once evaluated; Order id once (and only if) approved.
    risk_decision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # SEC-14: grounding flagged (fail-closed) — review-only, grounding never clears.
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
