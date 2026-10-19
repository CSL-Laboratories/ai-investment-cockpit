"""Predeclared flow thresholds.

These numbers were fixed before any return in this tree was computed from them.
Do not edit them after looking at an event study.
"""

from __future__ import annotations

import os

FEEDS = frozenset({"indicative", "opra"})
DEFAULT_FEED = "indicative"
FEED_ENV = "SIDECAR_OPTIONS_FEED"

# Static research watchlist. It does not change what the options runner is allowed to send.
WATCHLIST = (
    "SPY",
    "QQQ",
    "IWM",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
    "AMD",
    "AVGO",
)

MIN_VOLUME = 100.0
VOL_OI_MIN = 2.0
VOL_OI_CAP = 3.0
Z_MIN = 2.0
Z_CAP = 3.0
BASELINE_WINDOW = 20
BASELINE_MIN = 20
PREMIUM_MIN = 50_000.0
SWEEP_SECONDS = 1.0
SWEEP_MIN_PRINTS = 2
MAX_DTE = 14
OTM_PCT = 0.02
OI_MAX_AGE_DAYS = 5
SKEW_SHIFT_MIN = 0.02
SKEW_MIN_SIDE = 2
ALERT_SCORE = 3.0
HORIZONS = (1, 5)
PRIMARY_HORIZON = 1
# Alpaca's historical option trades endpoint allows at most 100 symbols per call.
TRADE_BATCH = 100
CONTRACT_PAGE_LIMIT = 1000
MAX_PAGES = 40


class FlowError(ValueError):
    """The flow scan refused an input or a response."""


def resolve_feed(value: str | None = None) -> str:
    """Indicative is the free default. OPRA is used only when asked for by name."""
    chosen = DEFAULT_FEED if value is None else value
    if value is None:
        chosen = os.environ.get(FEED_ENV, DEFAULT_FEED)
    if chosen not in FEEDS:
        raise FlowError("options feed must be 'indicative' or 'opra'")
    return chosen
