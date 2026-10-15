"""WP3 — token-bucket rate limiter. Pure + injectable time so the Finnhub 60/min cap can
never be exceeded (the runner passes monotonic ``now`` seconds)."""
from __future__ import annotations

import numbers

RATELIMIT_BACKEND = "python"
_NATIVE = None


def _load_native() -> None:
    """Use the Rust bucket step when the extension is installed.

    A missing module keeps the pure-Python path. Any other import failure is a
    broken extension and is not swallowed. Time stays caller-supplied.
    """
    global _NATIVE, RATELIMIT_BACKEND
    try:
        import cockpit_quant as native
    except ImportError:
        return
    if not hasattr(native, "token_bucket_allow"):
        return
    _NATIVE = native
    RATELIMIT_BACKEND = "rust"


def _real(value) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


class TokenBucket:
    def __init__(self, rate_per_sec: float = 1.0, capacity: float = 1.0, now: float = 0.0):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = 0.0            # start EMPTY (no birth burst) so the window count can't reach cap+rate*T
        self._last = now

    def allow(self, now: float) -> bool:
        native = _NATIVE
        if (
            native is not None
            and _real(self.rate)
            and _real(self.capacity)
            and _real(self.tokens)
            and _real(self._last)
            and _real(now)
        ):
            allowed, tokens, last = native.token_bucket_allow(
                float(self.rate), float(self.capacity), float(self.tokens), float(self._last), float(now),
            )
            self.tokens = tokens
            self._last = last
            return allowed
        return python_allow(self, now)


def python_allow(bucket: TokenBucket, now: float) -> bool:
    """The reference step. ``TokenBucket.allow`` uses this when Rust is absent."""
    elapsed = max(0.0, now - bucket._last)
    bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.rate)
    bucket._last = now
    if bucket.tokens >= 1.0:
        bucket.tokens -= 1.0
        return True
    return False


_load_native()
