"""Immutable W-23/W-24 merger evidence and prospective selection commitments.

These rows are derived control records, not raw filings or calibration cohorts.  Raw SEC bytes
remain in ``filings.RawStore`` and cohort/quote observations remain in write-once Parquet.  Every
row is hash-bound in the HMAC audit chain through ``AuditLog.record_state_change``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class MergerDealEvidence(Base):
    __tablename__ = "merger_deal_evidence"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    target_cik: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MergerCalibrationEvidence(Base):
    __tablename__ = "merger_calibration_evidence"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MergerSelectionCommitment(Base):
    """A server-witnessed, outcome-free evaluation universe committed prospectively."""

    __tablename__ = "merger_selection_commitment"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    witness_message_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    witnessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MergerEdgeEvidence(Base):
    __tablename__ = "merger_edge_evidence"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
