"""Fixed-window moments for the tape. Online updates are O(1); a batch path uses a stride view."""

from __future__ import annotations

import math
from collections import deque

import numpy as np


class RollingMoments:
    """Sample mean and standard deviation of the last `window` observations.

    The sums are updated incrementally. The window is short, and the stored
    values are the same ones the batch stride view would see.
    """

    def __init__(self, window: int, *, min_count: int) -> None:
        if window < 2 or min_count < 1 or min_count > window:
            raise ValueError("rolling window")
        self.window = window
        self.min_count = min_count
        self._values: deque[float] = deque()
        self._total = 0.0
        self._sq = 0.0

    def __len__(self) -> int:
        return len(self._values)

    def push(self, value: float) -> None:
        self._values.append(value)
        self._total += value
        self._sq += value * value
        if len(self._values) > self.window:
            old = self._values.popleft()
            self._total -= old
            self._sq -= old * old

    def mean(self) -> float | None:
        count = len(self._values)
        if count < self.min_count:
            return None
        return self._total / count

    def std(self) -> float | None:
        count = len(self._values)
        if count < max(self.min_count, 2):
            return None
        mean = self._total / count
        var = (self._sq - count * mean * mean) / (count - 1)
        if var <= 1e-18 or not math.isfinite(var):
            return None
        return math.sqrt(var)


def rolling_std_strided(values: np.ndarray, window: int, *, min_count: int) -> np.ndarray:
    """Sample standard deviation at each index, using a stride view of `values`.

    Indexes before `min_count - 1` are NaN. After `window` observations the
    view is exactly the last `window` rows; before that it is the prefix.
    """
    series = np.asarray(values, dtype=np.float64)
    out = np.full(series.shape[0], np.nan, dtype=np.float64)
    if series.shape[0] < min_count or window < 2:
        return out
    for index in range(min_count - 1, min(window - 1, series.shape[0])):
        prefix = series[: index + 1]
        out[index] = float(np.std(prefix, ddof=1))
    if series.shape[0] >= window:
        view = np.lib.stride_tricks.sliding_window_view(series, window)
        centered = view - view.mean(axis=1, keepdims=True)
        var = np.sum(centered * centered, axis=1) / (window - 1)
        std = np.sqrt(np.maximum(var, 0.0))
        out[window - 1 :] = std
    return out
