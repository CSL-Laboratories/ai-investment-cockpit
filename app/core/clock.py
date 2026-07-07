"""WP0 — injectable clock (real + frozen), so time-dependent logic is deterministic in
tests and in the record/replay harness (docs/enhancements-and-additions E-02)."""
from __future__ import annotations

from datetime import datetime, timezone


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock(Clock):
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


default_clock = Clock()
