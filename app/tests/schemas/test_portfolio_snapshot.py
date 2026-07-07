"""WP1a — PortfolioSnapshot round-trip test.

Imports only its own model + the shared Base (never `schemas.models`, which would
pull sibling modules that may not exist yet during parallel authoring).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.portfolio_snapshot import PortfolioSnapshot


def test_portfolio_snapshot_roundtrip():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    now = datetime.now(timezone.utc)
    with Session(eng) as s:
        s.add(
            PortfolioSnapshot(
                id="ps_1",
                account="alpaca-paper-001",
                cash=Decimal("12500.00"),
                buying_power=Decimal("25000.00"),
                exposure=Decimal("8300.50"),
                holdings=[{"symbol": "AAPL", "qty": 10, "market_value": 1900.00}],
                open_orders=[{"order_id": "ord_1", "symbol": "MSFT", "side": "buy"}],
                ts=now,
                created_at=now,
            )
        )
        s.commit()

    with Session(eng) as s:
        got = s.get(PortfolioSnapshot, "ps_1")
        assert got is not None
        assert got.account == "alpaca-paper-001"
        assert got.cash == Decimal("12500.00")
        assert got.buying_power == Decimal("25000.00")
        assert got.exposure == Decimal("8300.50")
        assert got.holdings[0]["symbol"] == "AAPL"
        assert got.open_orders[0]["order_id"] == "ord_1"


def test_portfolio_snapshot_nullable_exposure():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    now = datetime.now(timezone.utc)
    with Session(eng) as s:
        s.add(
            PortfolioSnapshot(
                id="ps_2",
                account="alpaca-paper-001",
                cash=Decimal("0.00"),
                buying_power=Decimal("0.00"),
                ts=now,
                created_at=now,
            )
        )
        s.commit()
        got = s.get(PortfolioSnapshot, "ps_2")
        assert got.exposure is None
