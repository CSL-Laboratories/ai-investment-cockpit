"""Implied-vol surface used by the stress options check.

ATM vol is trailing realized vol times one plus a structural premium. The put wing
is richer. There is no OPRA tape in this tree, so none of these numbers is a quote.
``bs_price`` is the sidecar pricer; the Monte Carlo sleeve is a variance swap, not
``simulate_book``.
"""

from __future__ import annotations

import math

from sidecar.options.pricing import bs_price

PREMIUM = {"bull": 0.12, "sideways": 0.18, "bear": 0.30, "crisis": 0.75}
SKEW = {"bull": 0.08, "sideways": 0.12, "bear": 0.18, "crisis": 0.32}


def atm_iv(realized_ann: float, regime: str) -> float:
    if realized_ann <= 0 or not math.isfinite(realized_ann):
        raise ValueError("realized vol must be positive")
    return realized_ann * (1.0 + PREMIUM[regime])


def wing_iv(atm: float, log_moneyness: float, regime: str) -> float:
    """``log_moneyness`` is log(strike / spot). Negative moneyness is the put wing."""
    if atm <= 0:
        raise ValueError("atm vol must be positive")
    return atm * math.exp(-SKEW[regime] * log_moneyness)


def rolling_rv(returns: list[float], window: int = 20) -> list[float | None]:
    """Annualized close-to-close vol known at the close of each return, or None early."""
    out: list[float | None] = []
    for index in range(len(returns)):
        if index + 1 < window:
            out.append(None)
            continue
        sample = returns[index + 1 - window : index + 1]
        mean = sum(sample) / len(sample)
        var = sum((value - mean) ** 2 for value in sample) / (len(sample) - 1)
        out.append(math.sqrt(var * 252.0) if var > 0 else None)
    return out


def iv_path(returns: list[float], regimes: list[str], fallback: float) -> list[float]:
    """ATM IV at each session, including the initial session before any return.

    ``regimes`` has one entry per session. ``returns`` has one fewer entry. The IV
    at the close of the session a return lands on uses that session's regime.
    """
    realized = rolling_rv(returns)
    path = [atm_iv(fallback, regimes[0])]
    for index, rv in enumerate(realized):
        regime = regimes[index + 1]
        base = rv if rv is not None else fallback
        path.append(atm_iv(base, regime))
    return path


def snapshot(realized_ann: float, regime: str, *, spot: float = 100.0) -> dict:
    """One 30-day smile and the Black-Scholes prices at those vols."""
    atm = atm_iv(realized_ann, regime)
    put_k = spot * 0.95
    call_k = spot * 1.05
    put_iv = wing_iv(atm, math.log(put_k / spot), regime)
    call_iv = wing_iv(atm, math.log(call_k / spot), regime)
    tenor = 30.0 / 365.0
    return {
        "regime": regime,
        "realized_ann": realized_ann,
        "premium": PREMIUM[regime],
        "skew": SKEW[regime],
        "atm_iv": atm,
        "put_iv_95": put_iv,
        "call_iv_105": call_iv,
        "put_price_skew": bs_price(spot, put_k, tenor, put_iv, "put"),
        "put_price_atm_vol": bs_price(spot, put_k, tenor, atm, "put"),
        "call_price_skew": bs_price(spot, call_k, tenor, call_iv, "call"),
        "put_richer_than_flat_vol": put_iv > atm,
    }


def variance_swap_pnl(returns: list[float], atm: list[float], notional: float = 1250.0) -> float:
    """Short variance marked from the previous close's ATM IV.

    This is not an option order and not ``simulate_book``. A jump that the trailing
    IV has not caught loses money.
    """
    pnl = 0.0
    for index, realized in enumerate(returns):
        iv = atm[index]
        if iv <= 0 or not math.isfinite(iv):
            continue
        pnl += notional * ((iv * iv) / 252.0 - realized * realized)
    return pnl
