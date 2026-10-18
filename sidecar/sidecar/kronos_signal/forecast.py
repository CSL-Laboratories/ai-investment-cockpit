"""Probabilistic next-N-bar returns from KronosPredictor.

Each call asks the vendored predictor for one sample path. The mean and the
sample dispersion are computed here. The upstream ``predict`` averages paths
internally, so dispersion would be lost if ``sample_count`` were raised there.
Context rows are the only prices passed in. Future timestamps are calendar
features for the horizon, not future prices.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import torch


def _frame(bars) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [float(bar.open) for bar in bars],
            "high": [float(bar.high) for bar in bars],
            "low": [float(bar.low) for bar in bars],
            "close": [float(bar.close) for bar in bars],
            "volume": [float(bar.volume) for bar in bars],
            "amount": [float(bar.volume) * float(bar.close) for bar in bars],
        }
    )


def _stamps(days: list[date]) -> pd.Series:
    return pd.Series(pd.to_datetime([day.isoformat() for day in days]))


def forecast_returns(
    predictor,
    contexts: list,
    future_days: list[date],
    *,
    sample_count: int,
    temperature: float,
    top_p: float,
    seed: int,
) -> list[dict]:
    """One result per context. ``mean_return`` is the mean path's N-bar close return."""
    if sample_count < 1:
        raise ValueError("sample_count must be positive")
    if not contexts:
        return []
    if any(len(context) < 2 for context in contexts):
        raise ValueError("each context needs at least two bars")
    pred_len = len(future_days)
    if pred_len < 1:
        raise ValueError("the horizon needs at least one future bar")
    last_dates = [context[-1].day for context in contexts]
    if any(day <= last for day in future_days for last in last_dates):
        raise ValueError("future timestamps must be strictly after every context")
    frames = [_frame(context) for context in contexts]
    x_stamps = [_stamps([bar.day for bar in context]) for context in contexts]
    y_stamp = _stamps(future_days)
    y_stamps = [y_stamp for _ in contexts]
    path_returns = [[] for _ in contexts]
    for sample_index in range(sample_count):
        torch.manual_seed(seed + sample_index)
        predicted = predictor.predict_batch(
            df_list=frames,
            x_timestamp_list=x_stamps,
            y_timestamp_list=y_stamps,
            pred_len=pred_len,
            T=temperature,
            top_k=0,
            top_p=top_p,
            sample_count=1,
            verbose=False,
        )
        for series_index, frame in enumerate(predicted):
            last_close = float(contexts[series_index][-1].close)
            end_close = float(frame["close"].iloc[-1])
            if last_close <= 0 or end_close <= 0:
                raise ValueError("refusing a non-positive close in a Kronos path")
            path_returns[series_index].append(end_close / last_close - 1.0)
    results = []
    for paths in path_returns:
        array = np.asarray(paths, dtype=np.float64)
        results.append(
            {
                "path_returns": [float(value) for value in array],
                "mean_return": float(array.mean()),
                "dispersion": float(array.std(ddof=1)) if len(array) > 1 else 0.0,
                "p_up": float(np.mean(array > 0.0)),
            }
        )
    return results
