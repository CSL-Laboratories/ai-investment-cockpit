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

import math
from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class EwmaVolatility:
    """Exponentially-weighted volatility of log returns, in return units per observation.

    EWMA rather than a simple rolling window because market volatility is persistent and
    clustered: a window forgets a regime change all at once, on the arbitrary boundary where
    the shock leaves the window, producing a step in the estimate that a market maker will
    trade around. EWMA decays smoothly and has no boundary.

    ``lam`` is the decay. 0.94 is the RiskMetrics daily default and is retained here as a
    familiar starting point, but at event frequency the right value depends entirely on the
    event rate — it is a parameter to sweep, not a constant to trust.
    """

    lam: float = 0.94
    _variance: float = field(default=0.0, repr=False)
    _last: float | None = field(default=None, repr=False)
    _count: int = field(default=0, repr=False)

    def update(self, price: float) -> float | None:
        """Feed one price; return the current volatility estimate, or None if not yet warm."""
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

    @property
    def value(self) -> float | None:
        """None until warm. Returning 0.0 while cold would be far worse than returning None:
        a market maker divides by volatility, and a zero estimate produces an infinitely
        confident quote at exactly the moment the model knows the least."""
        if self._count < 2:
            return None
        return math.sqrt(self._variance)

    @property
    def is_warm(self) -> bool:
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

    def __post_init__(self) -> None:
        if self.window < 2:
            raise ValueError(f"window must be >= 2, got {self.window}")
        self._values = deque(maxlen=self.window)

    def update(self, value: float) -> float | None:
        self._values.append(value)
        return self.value

    @property
    def is_warm(self) -> bool:
        return len(self._values) >= self.window

    @property
    def mean(self) -> float | None:
        if not self._values:
            return None
        return sum(self._values) / len(self._values)

    @property
    def stdev(self) -> float | None:
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

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError(f"window must be >= 1, got {self.window}")
        self._flow = deque(maxlen=self.window)

    def update(self, *, signed_qty: int) -> float:
        self._flow.append(signed_qty)
        return self.value

    @property
    def value(self) -> float:
        """Net signed volume / gross volume. 0.0 on an empty window — genuinely no imbalance."""
        gross = sum(abs(q) for q in self._flow)
        if gross == 0:
            return 0.0
        return sum(self._flow) / gross

    @property
    def is_warm(self) -> bool:
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

    def __post_init__(self) -> None:
        if self.window < 3:
            raise ValueError(f"window must be >= 3, got {self.window}")
        self._xs = deque(maxlen=self.window)
        self._ys = deque(maxlen=self.window)

    def update(self, x: float, y: float) -> float | None:
        self._xs.append(x)
        self._ys.append(y)
        return self.beta

    @property
    def is_warm(self) -> bool:
        return len(self._xs) >= self.window

    @property
    def beta(self) -> float | None:
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
