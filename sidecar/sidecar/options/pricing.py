"""Black-Scholes prices with zero rates, and close-to-close realized vol.

Rates and dividends are zero. That is a simulator choice, not a market quote.
The offline book treats a trailing realized vol as if it were implied vol. It is a
proxy. It is not an OPRA print.
"""

from __future__ import annotations

import math

from sidecar.normal import norm_cdf


def _d1_d2(spot: float, strike: float, t_years: float, sigma: float) -> tuple[float, float]:
    if spot <= 0 or strike <= 0 or t_years <= 0 or sigma <= 0:
        raise ValueError("Black-Scholes inputs must be positive")
    if not all(math.isfinite(value) for value in (spot, strike, t_years, sigma)):
        raise ValueError("Black-Scholes inputs must be finite")
    vol_time = sigma * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t_years) / vol_time
    return d1, d1 - vol_time


def bs_price(spot: float, strike: float, t_years: float, sigma: float, right: str) -> float:
    d1, d2 = _d1_d2(spot, strike, t_years, sigma)
    if right == "call":
        return spot * norm_cdf(d1) - strike * norm_cdf(d2)
    if right == "put":
        return strike * norm_cdf(-d2) - spot * norm_cdf(-d1)
    raise ValueError("right must be call or put")


def bs_delta(spot: float, strike: float, t_years: float, sigma: float, right: str) -> float:
    d1, _d2 = _d1_d2(spot, strike, t_years, sigma)
    if right == "call":
        return norm_cdf(d1)
    if right == "put":
        return norm_cdf(d1) - 1.0
    raise ValueError("right must be call or put")


def realized_vol(closes: list[float], index: int, window: int) -> float | None:
    """Annualized close-to-close sample vol ending at ``index`` inclusive."""
    if window < 2 or index < window or index >= len(closes):
        return None
    returns = []
    for cursor in range(index - window + 1, index + 1):
        previous = closes[cursor - 1]
        current = closes[cursor]
        if previous <= 0 or current <= 0:
            return None
        returns.append(math.log(current / previous))
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    if var <= 0.0 or not math.isfinite(var):
        return None
    annualized = math.sqrt(var * 252.0)
    if not math.isfinite(annualized):
        return None
    return annualized


def rolling_realized_vol(closes: list[float], window: int) -> list[float | None]:
    return [realized_vol(closes, index, window) for index in range(len(closes))]


def strike_for_delta(
    spot: float,
    t_years: float,
    sigma: float,
    *,
    right: str,
    target_abs_delta: float,
) -> int | None:
    """Integer strike whose Black-Scholes delta is closest to the target."""
    if spot <= 1 or not math.isfinite(target_abs_delta) or target_abs_delta <= 0:
        return None
    target = target_abs_delta if right == "call" else -target_abs_delta
    low = max(1, int(math.floor(spot * (0.50 if right == "put" else 1.0))))
    high = max(low, int(math.floor(spot * (0.999 if right == "put" else 1.50))))
    if right == "call":
        low = max(low, int(math.ceil(spot)))
    best: int | None = None
    best_error = 1e9
    left, right_edge = low, high
    while left <= right_edge:
        mid = (left + right_edge) // 2
        try:
            delta = bs_delta(spot, float(mid), t_years, sigma, right)
        except ValueError:
            return None
        error = abs(delta - target)
        if error < best_error:
            best_error = error
            best = mid
        # Put delta falls as the strike rises. Call delta falls as the strike rises too.
        if delta > target:
            left = mid + 1
        else:
            right_edge = mid - 1
    if best is None:
        return None
    for strike in range(max(low, best - 3), min(high, best + 3) + 1):
        try:
            delta = bs_delta(spot, float(strike), t_years, sigma, right)
        except ValueError:
            continue
        error = abs(delta - target)
        if error < best_error:
            best_error = error
            best = strike
    return best
