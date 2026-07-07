"""WP3 — token-bucket rate limiter. Pure + injectable time so the Finnhub 60/min cap can
never be exceeded (the runner passes monotonic ``now`` seconds)."""
from __future__ import annotations


class TokenBucket:
    def __init__(self, rate_per_sec: float = 1.0, capacity: float = 1.0, now: float = 0.0):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = 0.0            # start EMPTY (no birth burst) so the window count can't reach cap+rate*T
        self._last = now

    def allow(self, now: float) -> bool:
        elapsed = max(0.0, now - self._last)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self._last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False
