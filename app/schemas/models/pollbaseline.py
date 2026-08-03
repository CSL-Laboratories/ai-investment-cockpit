"""Durable first-run baseline marker for a monitored SEC registrant.

``PollCursor`` records normal ingestion progress. A baseline is deliberately a
separate record: it marks the newest valid accession that already existed when a
watchlist CIK was first enabled, without pretending that the historic filing was
downloaded or processed. Keeping that boundary apart prevents a first daily run from
turning the SEC ``recent`` feed into a historical backfill, while still allowing the
normal poller to retry a newly discovered filing that has a transient per-item error.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class PollBaseline(Base):
    """One forward-only SEC feed boundary per CIK.

    ``cutoff_accession_no`` is nullable only when a first baseline saw no valid SEC
    ``recent`` row. It never contains filing bytes or a synthetic Filing row.
    """

    __tablename__ = "poll_baseline"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    cik: Mapped[str] = mapped_column(String, unique=True, index=True)
    cutoff_accession_no: Mapped[str | None] = mapped_column(String, nullable=True)
    established_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
