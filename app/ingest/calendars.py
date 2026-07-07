"""WP3 — earnings/macro calendar pull (injectable ``fetch() -> list[dict]``) -> MarketEvent."""
from __future__ import annotations

from ingest.normalize import to_market_event


def pull_calendar(fetch, kind: str = "earnings") -> list[dict]:
    events = []
    for e in fetch():
        sym = e.get("symbol")
        events.append(to_market_event(
            source="calendar",
            article_id=e.get("id") or f"{kind}:{sym}:{e.get('date')}",
            symbols=[sym] if sym else [],
            headline=f"{kind}: {sym or ''} {e.get('date', '')}".strip(),
            summary=e.get("note", ""),
            ts=e.get("date"),
            event_type=f"calendar:{kind}",
            raw=e,
        ))
    return events
