"""WP1a — ModeState stored SQLAlchemy model.

Autonomy-ladder mode as stored server-side state (the lever Discord pulls).
See docs/draft-discord-control-plane.md "Graduated autonomy — the ladder".
Modes: read_only, paper, review_live, auto_veto, auto_full.

Auto modes (auto_veto/auto_full) re-arm daily: armed_at/armed_until track the
current arming window (disarm at market close), armed_by records the operator
who armed it, reason is free-form context for the transition. (``armed_until`` is
the mode's own arm expiry — distinct from a rule's ``expires_at``.)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class ModeState(Base):
    __tablename__ = "mode_state"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # enum: read_only | paper | review_live | auto_veto | auto_full
    mode: Mapped[str] = mapped_column(String, index=True)
    armed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    armed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    armed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
