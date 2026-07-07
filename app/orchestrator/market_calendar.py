"""WP5 / OPS-04 — minimal US-equity (NYSE) session calendar.

Pure functions over a tz-aware ``datetime``. Backs the intraday market-hours guard and
the ``armed_until`` (disarm-at-close) computation for the auto-mode ladder.

Self-contained on purpose: the Windows build host has no IANA ``tzdata``
(``zoneinfo.ZoneInfo('America/New_York')`` raises ``ZoneInfoNotFoundError``), so US
Eastern is implemented here with the canonical post-2007 DST rules — EST = UTC-5, EDT =
UTC-4, DST from the 2nd Sunday of March 02:00 to the 1st Sunday of November 02:00 — rather
than depending on a system tz database. Market hours are 09:30–16:00 ET regular, 09:30–13:00
ET on half-days.

The holiday + early-close sets are small, real, and **dated** — NYSE 2025 and 2026 only.
They MUST be refreshed each year (see ``_HOLIDAYS`` / ``_EARLY_CLOSES``); an out-of-range
date falls back to the weekday/hours rules without a holiday, which is the fail-safe
direction for a guard (it will not wrongly report a real holiday as open beyond the coded
years — refresh before trading a new year).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo

ZERO = timedelta(0)
HOUR = timedelta(hours=1)

# DST transition templates (year filled in at query time). Second Sunday in March is the
# first Sunday on/after Mar 8; first Sunday in November is the first Sunday on/after Nov 1.
_DSTSTART = datetime(1, 3, 8, 2)
_DSTEND = datetime(1, 11, 1, 2)


def _first_sunday_on_or_after(dt: datetime) -> datetime:
    days_to_go = 6 - dt.weekday()
    if days_to_go:
        dt += timedelta(days=days_to_go)
    return dt


class _USEastern(tzinfo):
    """US Eastern with the standard federal DST rule (no tzdata dependency)."""

    _std = timedelta(hours=-5)

    def utcoffset(self, dt):  # noqa: D401
        return self._std + self.dst(dt)

    def dst(self, dt):
        if dt is None or dt.tzinfo is None:
            return ZERO
        start = _first_sunday_on_or_after(_DSTSTART.replace(year=dt.year))
        end = _first_sunday_on_or_after(_DSTEND.replace(year=dt.year))
        naive = dt.replace(tzinfo=None)
        return HOUR if start <= naive < end else ZERO

    def tzname(self, dt):
        return "EDT" if self.dst(dt) else "EST"


EASTERN = _USEastern()

MARKET_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# NYSE full-day closes — DATED, refresh yearly (2025, 2026).
_HOLIDAYS: frozenset[date] = frozenset({
    # 2025
    date(2025, 1, 1),    # New Year's Day
    date(2025, 1, 20),   # MLK Jr. Day
    date(2025, 2, 17),   # Washington's Birthday
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 26),   # Memorial Day
    date(2025, 6, 19),   # Juneteenth
    date(2025, 7, 4),    # Independence Day
    date(2025, 9, 1),    # Labor Day
    date(2025, 11, 27),  # Thanksgiving
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Jr. Day
    date(2026, 2, 16),   # Washington's Birthday
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed; Jul 4 is a Saturday)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
})

# NYSE 1:00 p.m. ET early closes — DATED, refresh yearly (2025, 2026).
_EARLY_CLOSES: frozenset[date] = frozenset({
    # 2025
    date(2025, 7, 3),    # day before Independence Day
    date(2025, 11, 28),  # day after Thanksgiving
    date(2025, 12, 24),  # Christmas Eve
    # 2026 (Jul 3 is a full holiday this year, so no early close around July 4)
    date(2026, 11, 27),  # day after Thanksgiving
    date(2026, 12, 24),  # Christmas Eve
})

# Coded-years bound so callers can detect stale data (refresh before trading a new year).
COVERED_YEARS: frozenset[int] = frozenset({2025, 2026})


def _require_aware(dt: datetime) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("market_calendar requires a tz-aware datetime")


def to_eastern(dt: datetime) -> datetime:
    """Convert any tz-aware datetime to Eastern wall time."""
    _require_aware(dt)
    return dt.astimezone(EASTERN)


def is_holiday(d: date) -> bool:
    return d in _HOLIDAYS


def is_early_close(d: date) -> bool:
    return d in _EARLY_CLOSES


def is_trading_day(d: date) -> bool:
    """Weekday and not a full-day holiday (early-close days ARE trading days)."""
    return d.weekday() < 5 and d not in _HOLIDAYS


def close_time_for(d: date) -> time:
    return EARLY_CLOSE if d in _EARLY_CLOSES else REGULAR_CLOSE


def is_market_open(dt: datetime) -> bool:
    """True iff ``dt`` (tz-aware) falls inside a regular/early NYSE session."""
    et = to_eastern(dt)
    d = et.date()
    if not is_trading_day(d):
        return False
    return MARKET_OPEN <= et.time() < close_time_for(d)


def session_close(dt: datetime) -> datetime:
    """The next NYSE session close at or after ``dt`` (Eastern-aware).

    Used for ``armed_until`` (auto modes disarm at market close). If ``dt`` is inside a
    session, returns that session's close; otherwise the next trading day's close.
    """
    et = to_eastern(dt)
    d = et.date()
    for _ in range(370):  # bounded scan across any holiday/weekend gap
        if is_trading_day(d):
            ct = close_time_for(d)
            close = datetime(d.year, d.month, d.day, ct.hour, ct.minute, tzinfo=EASTERN)
            if close > et:
                return close
        d = d + timedelta(days=1)
    raise RuntimeError("session_close: no trading day found within a year")
