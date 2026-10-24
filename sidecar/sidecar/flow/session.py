"""Trading-session and expiration bounds for the flow scan.

Alpaca's contract list defaults to expirations through the next weekend. The
scan passes the expirations that actually appear on the snapshot chain.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sidecar.flow.config import TRADE_DELAY_MINUTES, FlowError
from sidecar.flow.symbols import parse_occ

NY = ZoneInfo("America/New_York")


def completed_trading_session(rows: list[dict], now: datetime) -> dict:
    """Latest calendar session whose close is at or before `now`.

    A session that has not opened, or that has opened and not closed, is not
    used. The previous completed session is used instead.
    """
    if now.tzinfo is None:
        raise FlowError("scan clock must be timezone-aware")
    moment = now.astimezone(NY)
    chosen: dict | None = None
    chosen_close: datetime | None = None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            continue
        _start, end = session_window(row)
        if end <= moment and (chosen_close is None or end > chosen_close):
            chosen = row
            chosen_close = end
    if chosen is None:
        raise FlowError("calendar has no completed trading session at or before the scan time")
    return chosen


def last_trading_session(rows: list[dict], today: date) -> dict:
    """Completed session as of the end of `today` in America/New_York."""
    moment = datetime.combine(today, time(23, 59), tzinfo=NY)
    return completed_trading_session(rows, moment)


def historical_trade_window(start: datetime, close: datetime, now: datetime) -> tuple[datetime, datetime]:
    """Start and end for a free historical options-trades request.

    End is the session close when that close is already outside the live OPRA
    window. Otherwise end is `now` minus 16 minutes, and never later than the
    close. The latest 15 minutes are real-time OPRA.
    """
    if now.tzinfo is None or start.tzinfo is None or close.tzinfo is None:
        raise FlowError("trade window requires timezone-aware clocks")
    opened = start.astimezone(timezone.utc)
    session_close = close.astimezone(timezone.utc)
    delayed = now.astimezone(timezone.utc) - timedelta(minutes=TRADE_DELAY_MINUTES)
    trade_end = min(session_close, delayed)
    if trade_end <= opened:
        raise FlowError("historical trade window ends at or before the session open")
    return opened, trade_end


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
