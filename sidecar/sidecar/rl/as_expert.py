"""Avellaneda-Stoikov quotes, reimplemented for the sidecar simulator.

The expression matches ``quotes`` in the cockpit market-maker: reservation
``mid - inventory * gamma * sigma^2 * tau``, half-spread
``0.5 * (variance_term + (2/gamma) * log1p(gamma/kappa))``, skew and half-spread
clamped, prices rounded outward. Gamma and kappa here are simulator units on a
$100 mid. The cockpit defaults are tick-unit parameters and are not copied
unscaled, because that would quote a half-spread the tick clamp would have cut.
This module does not import the cockpit tree.
"""

from __future__ import annotations

import math

GAMMA = 4.0
KAPPA = 80.0
MAX_SKEW = 0.50
MIN_HALF = 0.01
MAX_HALF = 0.25
QUOTE_SIZE = 1


def avellaneda_stoikov_quotes(
    *,
    mid: float,
    inventory: int,
    sigma: float,
    tau: float,
    gamma: float = GAMMA,
    kappa: float = KAPPA,
    max_skew: float = MAX_SKEW,
    min_half: float = MIN_HALF,
    max_half: float = MAX_HALF,
    max_inventory: int = 10,
    quote_size: int = QUOTE_SIZE,
) -> tuple[float, float, bool, bool] | None:
    if not math.isfinite(mid) or mid <= 0:
        return None
    if sigma is None or not math.isfinite(sigma) or sigma < 0 or not math.isfinite(gamma) or gamma <= 0:
        return None
    if not math.isfinite(kappa) or kappa <= 0:
        return None
    tau = max(0.0, min(1.0, tau))
    variance_term = gamma * sigma * sigma * tau
    skew = max(-max_skew, min(max_skew, inventory * variance_term))
    reservation = mid - skew
    half = 0.5 * (variance_term + (2.0 / gamma) * math.log1p(gamma / kappa))
    half = max(min_half, min(max_half, half))
    bid = math.floor((reservation - half) * 100.0) / 100.0
    ask = math.ceil((reservation + half) * 100.0) / 100.0
    if ask <= bid:
        ask = round(bid + max(0.01, min_half), 2)
    post_bid = inventory + quote_size <= max_inventory
    post_ask = inventory - quote_size >= -max_inventory
    return bid, ask, post_bid, post_ask
