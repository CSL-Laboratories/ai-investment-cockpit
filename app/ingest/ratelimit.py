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
    fallback. Time stays caller-supplied. Per-call ``TokenBucket.allow`` stays
    on Python; ``token_bucket_replay`` is the batch path that uses the extension.
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


def _all_float(values) -> bool:
    for value in values:
        if type(value) is not float:
            return False
    return True


class TokenBucket:
    def __init__(self, rate_per_sec: float = 1.0, capacity: float = 1.0, now: float = 0.0):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = 0.0            # start EMPTY (no birth burst) so the window count can't reach cap+rate*T
        self._last = now

    def allow(self, now: float) -> bool:
        """Admit one timestamp.

        Pollers call this once per item. Crossing into the extension for one
        step is slower than the arithmetic, so the hot path stays here.
        ``token_bucket_replay`` is the batch path.
        """
        elapsed = max(0.0, now - self._last)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self._last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


def python_allow(bucket: TokenBucket, now: float) -> bool:
    """The reference step. ``TokenBucket.allow`` is this step."""
    return bucket.allow(now)


def token_bucket_replay(rate, capacity, tokens, last, times):
    """Replay ``times`` from one bucket state.

    Floats, and ints that convert to ``f64`` without rounding, use the
    extension in one call. Anything else, including ``10**400``, runs
    ``python_allow`` so the result matches ``TokenBucket.allow``.

    ``times`` is scanned and then replayed, so a one-shot iterable is copied
    first. Otherwise the scan would leave the replay empty.
    """
    times = list(times)
    native = _NATIVE
    if (
        native is not None
        and _all_float((rate, capacity, tokens, last))
        and _all_float(times)
    ):
        return native.token_bucket_replay(rate, capacity, tokens, last, times)
    if native is not None:
        head = (
            _exact_f64(rate),
            _exact_f64(capacity),
            _exact_f64(tokens),
            _exact_f64(last),
        )
        if None not in head:
            steps = []
            for now in times:
                step = _exact_f64(now)
                if step is None:
                    steps = None
                    break
                steps.append(step)
            if steps is not None:
                return native.token_bucket_replay(*head, steps)
    bucket = TokenBucket(rate_per_sec=rate, capacity=capacity, now=last)
    bucket.tokens = tokens
    allowed = [python_allow(bucket, now) for now in times]
    return allowed, bucket.tokens, bucket._last


_load_native()
