"""QT-02 — online estimators shared by strategies and by the Tier-A verifier.

WHY THESE LIVE IN ONE PLACE
----------------------------
Volatility, order-flow imbalance and a rolling z-score get re-derived in every strategy that
needs them, and the re-derivations disagree in small ways that are invisible until two
strategies are compared. Worse, ``verify.py`` scores a proposal using the SAME notions — and
if the verifier's idea of volatility differs from the market maker's, the verifier is
grading a different trade than the one being placed.

ALL ONLINE, NONE LOOK-AHEAD
----------------------------
Every estimator here is incremental: it sees one observation at a time and can only ever
depend on the past. That is not a performance choice, it is the point. A vectorised
``pandas.rolling`` over the whole series is trivially easy to write and trivially easy to
accidentally centre, shift the wrong way, or fit on data the strategy could not have seen.
An online estimator makes look-ahead structurally impossible rather than merely discouraged
— you cannot feed it the future because you do not have it yet.

Pure stdlib, no numpy. These sit on the per-event hot path where numpy's per-call overhead
dominates its vectorisation benefit, and keeping them dependency-free means ``verify.py``
stays import-clean enough for ``risk/`` to consume under Invariant 5.
"""
from __future__ import annotations

import importlib.util
import logging
import math
import sys
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_I64_MIN = -2**63
_I64_MAX = 2**63 - 1
# CPython 3.12's ``sum`` is Neumaier. 3.11 adds floats left to right.
_COMPENSATED_SUM = sys.version_info >= (3, 12)
SIGNALS_BACKEND = "python"
_NATIVE = None


def _load_native() -> None:
    """Use the Rust estimators when the extension is installed.

    A missing module keeps the pure-Python path, so the hosted gates do not
    need a Rust toolchain. A present extension that fails to import is broken
    and the error propagates. A non-float observation, a non-float lambda, or
    an order-flow quantity that is not a signed 64-bit int leaves the extension
    and finishes on the Python path. A present module missing an estimator
    core, or ``EwmaCore.feed``, is a partial wheel. The extension is
    arithmetic only.
    """
    global _NATIVE, SIGNALS_BACKEND
    if importlib.util.find_spec("cockpit_quant") is None:
        log.info("SIGNALS_BACKEND=%s", SIGNALS_BACKEND)
        return
    import cockpit_quant as native

    required = ("EwmaCore", "ZScoreCore", "OfiCore", "BetaCore")
    missing = [name for name in required if not hasattr(native, name)]
    if not missing and not hasattr(native.EwmaCore, "feed"):
        missing.append("EwmaCore.feed")
    if missing:
        # The module was found. A partial wheel is a broken install, not a
        # reason to keep trading on the Python path without saying so.
        raise ImportError(
            "cockpit_quant is installed but does not provide " + ", ".join(missing)
        )
    _NATIVE = native
    SIGNALS_BACKEND = "rust"
    log.info("SIGNALS_BACKEND=%s", SIGNALS_BACKEND)


def _rust_window(window: int) -> bool:
    return isinstance(window, int) and not isinstance(window, bool) and 0 <= window <= _I64_MAX


@dataclass(slots=True)
class EwmaVolatility:
    """Exponentially-weighted volatility of log returns, in return units per observation.

    EWMA rather than a simple rolling window because market volatility is persistent and
    clustered: a window forgets a regime change all at once, on the arbitrary boundary where
    the shock leaves the window, producing a step in the estimate that a market maker will
    trade around. EWMA decays smoothly and has no boundary.

    ``lam`` is the decay. 0.94 is the RiskMetrics daily default and is retained here as a
    familiar starting point, but at event frequency the right value depends entirely on the
    event rate — it is a parameter to sweep, not a constant to trust. The constructor
    requires ``0 < lam < 1``, on this class and on the Rust core.
    """

    lam: float = 0.94
    _variance: float = field(default=0.0, repr=False)
    _last: float | None = field(default=None, repr=False)
    _count: int = field(default=0, repr=False)
    _core: object | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # Bool is an int, so it has to fail the range check too: ``True`` is 1.
        try:
            in_range = 0.0 < self.lam < 1.0
        except TypeError:
            in_range = False
        if not in_range:
            raise ValueError(f"lam must be in (0, 1), got {self.lam!r}")
        native = _NATIVE
        if (
            native is not None
            and type(self.lam) is float
            and self._variance == 0.0
            and self._last is None
            and self._count == 0
        ):
            self._core = native.EwmaCore(self.lam)

    def __eq__(self, other: object) -> bool:
        if type(other) is not EwmaVolatility:
            return NotImplemented
        return self.lam == other.lam and self._snapshot() == other._snapshot()

    def _snapshot(self) -> tuple[float, float | None, int]:
        core = self._core
        if core is None:
            return (self._variance, self._last, self._count)
        return (core.variance, core.last, core.count)

    def _leave_rust(self) -> None:
        core = self._core
        if core is None:
            return
        self._variance = core.variance
        self._last = core.last
        self._count = core.count
        self._core = None

    def update(self, price: float) -> float | None:
        """Feed one price; return the current volatility estimate, or None if not yet warm.

        An exact float stays on the extension. Anything else leaves it and
        finishes on the Python path. A finished series uses ``update_many``.
        """
        core = self._core
        if core is not None and (type(self.lam) is not float or self.lam != core.lam):
            self._leave_rust()
            core = None
        if core is not None and type(price) is float:
            return core.update(price)
        if core is not None:
            self._leave_rust()
        if price <= 0:
            return self.value
        if self._last is None:
            self._last = price
            return None
        ret = math.log(price / self._last)
        self._last = price
        self._count += 1
        self._variance = self.lam * self._variance + (1.0 - self.lam) * ret * ret
        return self.value

    def update_many(self, prices: Sequence[float]) -> list[float | None]:
        """Feed a finished series and leave this estimator at the end of it.

        Exact floats are one ``EwmaCore.feed`` call. An int or any other
        observation uses ``update``, which leaves the extension. A one-shot
        iterable is copied before that scan. NaN stays on the extension, where
        it matches ``math.log``. A non-finite ratio still raises, and the
        prices before it stay on the core.
        """
        prices = list(prices)
        core = self._core
        if core is not None and (type(self.lam) is not float or self.lam != core.lam):
            self._leave_rust()
            core = None
        if core is not None and all(type(price) is float for price in prices):
            return list(core.feed(prices))
        return [self.update(price) for price in prices]

    @property
    def value(self) -> float | None:
        """None until warm. Returning 0.0 while cold would be far worse than returning None:
        a market maker divides by volatility, and a zero estimate produces an infinitely
        confident quote at exactly the moment the model knows the least."""
        if self._core is not None:
            return self._core.value
        if self._count < 2:
            return None
        return math.sqrt(self._variance)

    @property
    def is_warm(self) -> bool:
        if self._core is not None:
            return self._core.is_warm
        return self._count >= 2


@dataclass(slots=True)
class RollingZScore:
    """Rolling mean/stdev and the z-score of the latest observation.

    Used by the pairs strategy on the spread. Kept as a bounded deque rather than a running
    sum because a naive running sum accumulates floating-point drift over millions of
    updates, and the drift shows up as a slowly-biased z-score — a signal that degrades
    rather than breaks, which is the hardest kind to notice.
    """

    window: int = 200
    _values: deque[float] = field(default_factory=deque, repr=False)
    _core: object | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.window < 2:
            raise ValueError(f"window must be >= 2, got {self.window}")
        self._values = deque(maxlen=self.window)
        native = _NATIVE
        if native is not None and _rust_window(self.window):
            self._core = native.ZScoreCore(self.window, _COMPENSATED_SUM)

    def __eq__(self, other: object) -> bool:
        if type(other) is not RollingZScore:
            return NotImplemented
        return self.window == other.window and self._snapshot() == other._snapshot()

    def _snapshot(self) -> tuple[float, ...]:
        core = self._core
        if core is None:
            return tuple(self._values)
        return tuple(core.samples())

    def _leave_rust(self) -> None:
        core = self._core
        if core is None:
            return
        # maxlen is fixed at construction. Assigning ``window`` later changes
        # only the warm check, so the exported deque keeps the core's window.
        self._values = deque(core.samples(), maxlen=core.window)
        self._core = None

    def _current_core(self) -> object | None:
        core = self._core
        if core is not None and core.window != self.window:
            self._leave_rust()
            return None
        return core

    def update(self, value: float) -> float | None:
        core = self._current_core()
        if core is not None and type(value) is float:
            try:
                return core.update(value)
            except OverflowError:
                # The sample is already stored. Python ``** 2`` raises.
                self._leave_rust()
                return self.value
        if core is not None:
            self._leave_rust()
        self._values.append(value)
        return self.value

    @property
    def is_warm(self) -> bool:
        if self._current_core() is not None:
            return self._core.is_warm
        return len(self._values) >= self.window

    @property
    def mean(self) -> float | None:
        if self._current_core() is not None:
            return self._core.mean
        if not self._values:
            return None
        return sum(self._values) / len(self._values)

    @property
    def stdev(self) -> float | None:
        if self._current_core() is not None:
            try:
                return self._core.stdev
            except OverflowError:
                self._leave_rust()
        n = len(self._values)
        if n < 2:
            return None
        mu = self.mean
        return math.sqrt(sum((v - mu) ** 2 for v in self._values) / (n - 1))

    @property
    def value(self) -> float | None:
        """The z-score of the most recent observation. None while cold or degenerate.

        A zero-variance window returns None rather than dividing by zero or clamping to a
        large number. A constant spread is not an infinitely strong signal — it is no signal,
        and a strategy must be able to tell the difference.
        """
        if self._current_core() is not None:
            try:
                return self._core.value
            except OverflowError:
                self._leave_rust()
        if not self.is_warm:
            return None
        sd = self.stdev
        if sd is None or sd <= 0:
            return None
        return (self._values[-1] - self.mean) / sd


@dataclass(slots=True)
class OrderFlowImbalance:
    """Signed traded volume over a rolling window of prints, normalised to [-1, 1].

    Signed by the AGGRESSOR — the side that removed liquidity. This is the whole content of
    the signal: a print at the ask means a buyer paid up, which is information about
    intention in a way that the price alone is not. ``events.Trade`` names the field
    ``aggressor`` rather than ``side`` precisely so this cannot be wired backwards, since a
    sign error here yields a signal that is exactly wrong rather than merely weak.
    """

    window: int = 50
    _flow: deque[int] = field(default_factory=deque, repr=False)
    _core: object | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError(f"window must be >= 1, got {self.window}")
        self._flow = deque(maxlen=self.window)
        native = _NATIVE
        if native is not None and _rust_window(self.window):
            self._core = native.OfiCore(self.window)

    def __eq__(self, other: object) -> bool:
        if type(other) is not OrderFlowImbalance:
            return NotImplemented
        return self.window == other.window and self._snapshot() == other._snapshot()

    def _snapshot(self) -> tuple[int, ...]:
        core = self._core
        if core is None:
            return tuple(self._flow)
        return tuple(core.quantities())

    def _leave_rust(self) -> None:
        core = self._core
        if core is None:
            return
        self._flow = deque(core.quantities(), maxlen=core.window)
        self._core = None

    def _current_core(self) -> object | None:
        core = self._core
        if core is not None and core.window != self.window:
            self._leave_rust()
            return None
        return core

    def update(self, *, signed_qty: int) -> float:
        core = self._current_core()
        if core is not None and type(signed_qty) is int and _I64_MIN <= signed_qty <= _I64_MAX:
            try:
                return core.update(signed_qty)
            except ValueError:
                # The running total does not fit in the widened accumulator.
                # Keep the prints and finish the ratio in Python.
                self._leave_rust()
        elif core is not None:
            self._leave_rust()
        self._flow.append(signed_qty)
        return self.value

    @property
    def value(self) -> float:
        """Net signed volume / gross volume. 0.0 on an empty window — genuinely no imbalance."""
        if self._current_core() is not None:
            return self._core.value
        gross = sum(abs(q) for q in self._flow)
        if gross == 0:
            return 0.0
        return sum(self._flow) / gross

    @property
    def is_warm(self) -> bool:
        if self._current_core() is not None:
            return self._core.is_warm
        return len(self._flow) >= self.window


@dataclass(slots=True)
class RollingBeta:
    """Online OLS slope and intercept of y on x, for the pairs hedge ratio.

    Maintained over a bounded window rather than since inception: a cointegrating
    relationship drifts, and a beta fitted over all history is fitted mostly to a regime
    that has ended. Recomputed from the retained points rather than by updating sufficient
    statistics incrementally — same reasoning as ``RollingZScore``: incremental sums of
    squares lose precision in a way that biases the estimate silently.
    """

    window: int = 200
    _xs: deque[float] = field(default_factory=deque, repr=False)
    _ys: deque[float] = field(default_factory=deque, repr=False)
    _core: object | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.window < 3:
            raise ValueError(f"window must be >= 3, got {self.window}")
        self._xs = deque(maxlen=self.window)
        self._ys = deque(maxlen=self.window)
        native = _NATIVE
        if native is not None and _rust_window(self.window):
            self._core = native.BetaCore(self.window, _COMPENSATED_SUM)

    def __eq__(self, other: object) -> bool:
        if type(other) is not RollingBeta:
            return NotImplemented
        return self.window == other.window and self._snapshot() == other._snapshot()

    def _snapshot(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        core = self._core
        if core is None:
            return (tuple(self._xs), tuple(self._ys))
        return (tuple(core.xs()), tuple(core.ys()))

    def _leave_rust(self) -> None:
        core = self._core
        if core is None:
            return
        self._xs = deque(core.xs(), maxlen=core.window)
        self._ys = deque(core.ys(), maxlen=core.window)
        self._core = None

    def _current_core(self) -> object | None:
        core = self._core
        if core is not None and core.window != self.window:
            self._leave_rust()
            return None
        return core

    def update(self, x: float, y: float) -> float | None:
        core = self._current_core()
        if core is not None and type(x) is float and type(y) is float:
            try:
                return core.update(x, y)
            except OverflowError:
                self._leave_rust()
                return self.beta
        if core is not None:
            self._leave_rust()
        self._xs.append(x)
        self._ys.append(y)
        return self.beta

    @property
    def is_warm(self) -> bool:
        if self._current_core() is not None:
            return self._core.is_warm
        return len(self._xs) >= self.window

    @property
    def beta(self) -> float | None:
        if self._current_core() is not None:
            try:
                return self._core.beta
            except OverflowError:
                self._leave_rust()
        n = len(self._xs)
        if n < 3:
            return None
        mx = sum(self._xs) / n
        my = sum(self._ys) / n
        var = sum((x - mx) ** 2 for x in self._xs)
        if var <= 0:
            return None
        cov = sum((x - mx) * (y - my) for x, y in zip(self._xs, self._ys))
        return cov / var

    @property
    def alpha(self) -> float | None:
        if self._current_core() is not None:
            try:
                return self._core.alpha
            except OverflowError:
                self._leave_rust()
        beta = self.beta
        if beta is None:
            return None
        n = len(self._xs)
        return sum(self._ys) / n - beta * (sum(self._xs) / n)

    def residual(self, x: float, y: float) -> float | None:
        """The spread: how far y sits from where beta says it should be, given x."""
        beta, alpha = self.beta, self.alpha
        if beta is None or alpha is None:
            return None
        return y - (alpha + beta * x)


_load_native()
