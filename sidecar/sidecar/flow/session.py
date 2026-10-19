"""Trading-session and expiration bounds for the flow scan.

Alpaca's contract list defaults to expirations through the next weekend. The
scan passes the expirations that actually appear on the snapshot chain.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sidecar.flow.config import FlowError
from sidecar.flow.symbols import parse_occ

NY = ZoneInfo("America/New_York")


def last_trading_session(rows: list[dict], today: date) -> dict:
    """Latest calendar row whose date is on or before `today`."""
    chosen: dict | None = None
    chosen_day: date | None = None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            continue
        day = date.fromisoformat(row["date"][:10])
        if day <= today and (chosen_day is None or day > chosen_day):
            chosen = row
            chosen_day = day
    if chosen is None or chosen_day is None:
        raise FlowError("calendar has no trading session on or before the scan date")
    return chosen


def session_window(row: dict) -> tuple[datetime, datetime]:
    day = date.fromisoformat(str(row["date"])[:10])
    open_text = str(row.get("open") or "09:30")
    close_text = str(row.get("close") or "16:00")
    start = datetime.combine(day, time.fromisoformat(open_text), tzinfo=NY).astimezone(timezone.utc)
    end = datetime.combine(day, time.fromisoformat(close_text), tzinfo=NY).astimezone(timezone.utc)
    if end <= start:
        raise FlowError("calendar session close is not after the open")
    return start, end


def expiration_bounds(symbols: list[str], session: date) -> tuple[str, str]:
    """Cover every expiration on the snapshot chain, not the API's weekend default."""
    found: list[date] = []
    for symbol in symbols:
        try:
            _root, expiration, _right, _strike = parse_occ(symbol)
        except FlowError:
            continue
        found.append(expiration)
    if not found:
        return session.isoformat(), (session + timedelta(days=730)).isoformat()
    start = min(session, min(found))
    end = max(found)
    return start.isoformat(), end.isoformat()
