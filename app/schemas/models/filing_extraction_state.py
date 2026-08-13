"""RW-11 durable retry state for deterministic filing-to-Delta extraction.

SEC polling persists filing metadata and write-once raw bytes before delta computation. If
that later computation fails transiently, the submissions cursor quite correctly will not
download the filing again. This row remembers the incomplete derived step so the scheduled
pipeline retries it from the already-stored raw evidence instead of losing it permanently.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.ids import new_id
from schemas.models.base import Base


class FilingExtractionState(Base):
    __tablename__ = "filing_extraction_state"

    id: Mapped[str] = mapped_column(
        String(48), primary_key=True, default=lambda: new_id("extract")
    )
    accession_no: Mapped[str] = mapped_column(String(25), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
