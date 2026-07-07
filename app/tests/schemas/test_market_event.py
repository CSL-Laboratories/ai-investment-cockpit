"""WP1a — round-trip test for the MarketEvent model.

Imports only this contract's own module + the shared Base (never
`schemas.models`, which would pull siblings that may not exist yet).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from schemas.models.base import Base
from schemas.models.market_event import MarketEvent


def test_market_event_roundtrip():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    now = datetime.now(timezone.utc)
    with Session(eng) as s:
        s.add(
            MarketEvent(
                id="me_1",
                symbol="AAPL",
                source="alpaca_news",
                event_type="news",
                headline="Apple beats on earnings",
                summary=None,
                raw_ref="parquet://2026-07-06/AAPL/0001",
                content_hash="a1b2c3d4",
                confidence=None,
                freshness=None,
                ts=now,
                ingested_at=now,
                created_at=now,
            )
        )
        s.commit()

    with Session(eng) as s:
        row = s.get(MarketEvent, "me_1")
        assert row is not None
        assert row.symbol == "AAPL"
        assert row.source == "alpaca_news"
        assert row.content_hash == "a1b2c3d4"
        assert row.summary is None
