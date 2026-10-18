"""Post records shared by Reddit and the X inbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
CLOSE = time(16, 0)


class SentimentError(ValueError):
    """The sentiment snapshot refused an input."""


@dataclass(frozen=True)
class Post:
    source: str
    id: str
    created_at: datetime
    author: str
    text: str
    metrics: dict


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SentimentError("created_at is missing")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise SentimentError("created_at must be timezone-aware")
    return moment


def decision_session(moment: datetime) -> date:
    """Posts after the 16:00 New York close belong to the next calendar date."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise SentimentError("created_at must be timezone-aware")
    local = moment.astimezone(NY)
    if local.time() <= CLOSE:
        return local.date()
    return local.date() + timedelta(days=1)


def session_cutoff(session: date) -> datetime:
    return datetime.combine(session, CLOSE, tzinfo=NY)
