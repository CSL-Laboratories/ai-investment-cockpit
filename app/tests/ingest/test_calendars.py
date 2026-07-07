"""WP3 — calendar pull normalizes to provenance-tagged MarketEvents."""
from __future__ import annotations

from ingest.calendars import pull_calendar


def test_pull_calendar():
    entries = [
        {"symbol": "AAPL", "date": "2026-07-30", "note": "Q3 earnings"},
        {"symbol": "MSFT", "date": "2026-07-29", "note": "Q4 earnings"},
    ]
    events = pull_calendar(lambda: entries, kind="earnings")
    assert len(events) == 2
    assert events[0]["event_type"] == "calendar:earnings"
    assert events[0]["source"] == "calendar" and events[0]["symbols"] == ["AAPL"]
    assert "AAPL" in events[0]["headline"]
