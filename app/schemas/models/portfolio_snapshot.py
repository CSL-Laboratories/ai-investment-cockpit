"""WP1a — PortfolioSnapshot stored model.

A point-in-time capture of broker/account state: cash, buying power, exposure,
holdings, and open orders. Sourced from the broker connector (draft 1 §4:
`PortfolioSnapshot`: timestamp, account, holdings, cash, buying_power, exposure,
open_orders). Raw broker state is stored here as JSON so downstream risk checks
and the audit trail can trace exposure/concentration back to a concrete snapshot.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshot"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account: Mapped[str] = mapped_column(String, index=True)
    cash: Mapped[float] = mapped_column(Numeric(18, 2))
    buying_power: Mapped[float] = mapped_column(Numeric(18, 2))
    exposure: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    holdings: Mapped[list] = mapped_column(JSON, default=list)
    open_orders: Mapped[list] = mapped_column(JSON, default=list)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
