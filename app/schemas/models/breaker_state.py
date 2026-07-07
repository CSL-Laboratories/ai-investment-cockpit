"""WP1a — BreakerState SQLAlchemy model.

Circuit-breaker state (draft-discord-control-plane §"Graduated autonomy — the
ladder, the gate, the breakers"). Breakers are deterministic; any trip drops the
system to `review_live`. This row records a named breaker's tripped/cleared
lifecycle for audit and status reporting (`GET /status`).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class BreakerState(Base):
    __tablename__ = "breaker_state"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    breaker_name: Mapped[str] = mapped_column(String, index=True)
    tripped: Mapped[bool] = mapped_column(Boolean, default=False)
    tripped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
