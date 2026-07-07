"""WP1a — MarketEvent stored model.

A normalized market event (news article, filing, calendar item, quote-move)
ingested from a source (Alpaca news WS, Finnhub, calendars) into the pipeline.

Authority:
- docs/cockpit-plan-original.md §4 (Minimum schemas): symbol, source,
  timestamp, event_type, headline/summary, raw_ref, confidence, freshness.
- docs/security-hardening-backlog.md SEC-13: bind the raw item's *content_hash*
  at ingest so grounding/reconstruction cannot be defeated below the LLM; the
  raw payload itself lives write-once in parquet, referenced by `raw_ref`.
- docs/build-scaffold-ultracode.md WP1a.

Field conventions (shared across contracts): ids are opaque `String`;
`symbol` is an uppercase US ticker; timestamps are `DateTime(timezone=True)`;
hashes are hex strings.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class MarketEvent(Base):
    __tablename__ = "market_event"

    # Opaque string primary key.
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Uppercase US ticker the event concerns.
    symbol: Mapped[str] = mapped_column(String, index=True)

    # Origin feed (e.g. "alpaca_news", "finnhub", "calendar").
    source: Mapped[str] = mapped_column(String)

    # Normalized event category (e.g. "news", "earnings", "filing", "quote_move").
    event_type: Mapped[str] = mapped_column(String)

    headline: Mapped[str] = mapped_column(String)

    # Optional longer body / abstract.
    summary: Mapped[str | None] = mapped_column(String, nullable=True)

    # Pointer into the write-once raw store (parquet key / article id).
    raw_ref: Mapped[str] = mapped_column(String)

    # SEC-13: hex content-hash of the raw item, bound at ingest.
    content_hash: Mapped[str] = mapped_column(String)

    # Model/heuristic confidence in the normalization (0..1); optional.
    confidence: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    # Freshness score / recency weight (0..1); optional.
    freshness: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    # When the event occurred at the source.
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # When this app ingested it.
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Row-creation time (present on every model).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
