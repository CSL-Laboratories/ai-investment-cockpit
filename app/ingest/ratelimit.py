"""WP3 — token-bucket rate limiter. Pure + injectable time so the Finnhub 60/min cap can
never be exceeded (the runner passes monotonic ``now`` seconds)."""
from __future__ import annotations

import importlib.util
import logging
import numbers

log = logging.getLogger(__name__)

RATELIMIT_BACKEND = "python"
_NATIVE = None


def _load_native() -> None:
    """Use the Rust bucket step when the extension is installed.

    A missing module keeps the pure-Python path. A present extension that
    fails to import is broken and the error propagates. A present module
    missing ``token_bucket_allow`` is a partial wheel, not a silent Python
    fallback. Time stays caller-supplied.
    """
    global _NATIVE, RATELIMIT_BACKEND
    if importlib.util.find_spec("cockpit_quant") is None:
        log.info("RATELIMIT_BACKEND=%s", RATELIMIT_BACKEND)
        return
    import cockpit_quant as native

    if not hasattr(native, "token_bucket_allow"):
        raise ImportError(
            "cockpit_quant is installed but does not provide token_bucket_allow"
        )
    _NATIVE = native
    RATELIMIT_BACKEND = "rust"
    log.info("RATELIMIT_BACKEND=%s", RATELIMIT_BACKEND)


def _real(value) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _exact_f64(value):
    """A binary64 with the same value, or None when the Rust step would differ.

    A ``float`` is already binary64. An ``int`` converts only when every bit
    survives: ``10**400`` raises ``OverflowError``, and ``2**53 + 1`` rounds.
    Any other ``numbers.Real`` stays on the Python step.
    """
    if not _real(value):
        return None
    if type(value) is float:
        return value
    if type(value) is int:
        try:
            converted = float(value)
        except OverflowError:
            return None
        if int(converted) != value:
            return None
        return converted
    return None


class TokenBucket:
    def __init__(self, rate_per_sec: float = 1.0, capacity: float = 1.0, now: float = 0.0):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = 0.0            # start EMPTY (no birth burst) so the window count can't reach cap+rate*T
        self._last = now

    def allow(self, now: float) -> bool:
        native = _NATIVE
        if native is not None:
            converted = [
                _exact_f64(value)
                for value in (self.rate, self.capacity, self.tokens, self._last, now)
            ]
            if all(value is not None for value in converted):
                allowed, tokens, last = native.token_bucket_allow(*converted)
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
