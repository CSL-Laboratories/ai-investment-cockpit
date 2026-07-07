"""WP1a — UsageWindow stored SQLAlchemy model.

Per-vendor usage-window accounting for the agent runner (WP1b). Each row tracks
one vendor's rolling usage window: how much of the window has been consumed, the
estimated limit, and the derived state (ok/degraded/exhausted). The runner opens
a breaker and degrades to watch-only on exhaustion (Invariant 7:
usage-window exhaustion degrades to watch-only; it never trades on stale
analysis).

The `generation` field is a monotonic counter (SEC-23): recovery from any
degradation (watch-only, breaker, usage exhaustion) must fail closed to
`review_live` and requires an explicit daily re-arm — never a silent restore of
a prior auto mode. Bumping `generation` invalidates any queued proposal minted
against an earlier window so a blackout cannot become cover for auto-execution
after a usage-window reset.

Enum `state`: ok | degraded | exhausted.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class UsageWindow(Base):
    __tablename__ = "usage_window"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    vendor: Mapped[str] = mapped_column(String, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # A vendor window is a fixed span, so its end is known at creation (non-null),
    # matching the TriageResult.window_end agent-output contract.
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[int] = mapped_column(Integer, default=0)
    limit_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # monotonic counter (SEC-23) — bumped on every window reset / degradation
    generation: Mapped[int] = mapped_column(Integer, default=0)
    # enum: ok | degraded | exhausted
    state: Mapped[str] = mapped_column(String, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
