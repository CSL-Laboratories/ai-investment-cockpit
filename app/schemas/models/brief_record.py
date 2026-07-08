"""WP8 — BriefRecord stored model (contract Owner A).

One persisted morning brief per trading day (docs/build-scaffold-ultracode.md WP8
morning_brew). ``payload`` is the schema-validated MorningBrief dict; ``degraded`` marks
briefs produced under usage degradation (SEC-23 watch-only) or with dropped foreign
source_refs. Rows persist ONLY via ``st.audit.record_state_change(row, EVENT_BRIEF, ...)``
(SEC-13 — never a bare Repo.add).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class BriefRecord(Base):
    __tablename__ = "brief_record"

    # Opaque string primary key.
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Brief date, YYYY-MM-DD — one brief per day.
    brief_date: Mapped[str] = mapped_column(String, unique=True, index=True)

    # Schema-validated MorningBrief output (all 8 keys present).
    payload: Mapped[dict] = mapped_column(JSON)

    # AgentJob id of the deep call that composed it; nullable for degraded/empty briefs.
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # True when produced under degradation (limit-hit / dropped foreign refs).
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)

    # Row-creation time (present on every model).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
