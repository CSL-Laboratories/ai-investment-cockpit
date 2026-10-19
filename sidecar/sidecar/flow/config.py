"""Predeclared flow thresholds.

The 0–2 DTE exclusion, open-interest floor, and within-chain percentiles were set
after the 2026-10-16 SPY scan flagged 892 of 12,630 contracts, mostly short-dated
names where volume over open interest is ordinary. No return in this tree has been
computed from the revised rule. Do not refit these numbers to the event study.
"""

from __future__ import annotations

import os

FEEDS = frozenset({"indicative", "opra"})
DEFAULT_FEED = "indicative"
FEED_ENV = "SIDECAR_OPTIONS_FEED"
WATCHLIST_ENV = "SIDECAR_FLOW_WATCHLIST"
INTERVAL_ENV = "SIDECAR_FLOW_MIN_INTERVAL"

# Sentiment still imports this tuple. The flow scan uses FLOW_UNIVERSE.
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

# Static research universe. It does not change what the options runner may send.
FLOW_UNIVERSE = (
    "SPY",
    "QQQ",
    "IWM",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "AMD",
    "AVGO",
    "NFLX",
    "JPM",
    "BAC",
    "XOM",
    "UNH",
    "LLY",
    "COIN",
    "PLTR",
    "MSTR",
    "SMCI",
    "BA",
    "DIS",
    "INTC",
    "MU",
    "UBER",
    "CRM",
    "ORCL",
    "QCOM",
    "HD",
    "COST",
    "WMT",
)

MIN_VOLUME = 100.0
MIN_OI = 100.0
VOL_OI_CAP = 3.0
Z_MIN = 2.0
Z_CAP = 3.0
BASELINE_WINDOW = 20
BASELINE_MIN = 20
PREMIUM_MIN = 50_000.0
SWEEP_SECONDS = 1.0
SWEEP_MIN_PRINTS = 2
# 0–2 DTE is excluded from vol/OI and from the short-dated flag. 3–14 still qualifies.
NEAR_DTE_MAX = 2
MAX_DTE = 14
OTM_PCT = 0.02
OI_MAX_AGE_DAYS = 5
SKEW_SHIFT_MIN = 0.02
SKEW_MIN_SIDE = 2
PERCENTILE_MIN_N = 20
PERCENTILE_FLOOR = 0.95
ALERT_SCORE = 3.0
# Until a contract has 20 prior volume sessions, z is unavailable and this bar applies.
ALERT_SCORE_WITHOUT_Z = 4.0
TOP_N = 15
MAX_PER_UNDERLYING = 3
HORIZONS = (1, 5)
PRIMARY_HORIZON = 1
# Alpaca's historical option trades endpoint allows at most 100 symbols per call.
TRADE_BATCH = 100
STOCK_BATCH = 50
CONTRACT_PAGE_LIMIT = 1000
MAX_PAGES = 40
REQUEST_RETRIES = 4
DEFAULT_REQUEST_INTERVAL = 0.3


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


def flow_watchlist(value: str | None = None) -> tuple[str, ...]:
    """Default scan list, or a comma-separated subset from the environment.

    Every name has to be on FLOW_UNIVERSE. The environment does not add symbols.
    """
    raw = os.environ.get(WATCHLIST_ENV, "") if value is None else value
    if not raw.strip():
        return FLOW_UNIVERSE
    chosen = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    unknown = [item for item in chosen if item not in FLOW_UNIVERSE]
    if unknown:
        raise FlowError(f"symbol not on the flow watchlist: {unknown[0]}")
    if len(chosen) != len(set(chosen)):
        raise FlowError("flow watchlist has a duplicate symbol")
    return chosen


def request_interval(value: str | None = None) -> float:
    """Seconds between live GETs. About 200 requests per minute at the default."""
    raw = os.environ.get(INTERVAL_ENV, str(DEFAULT_REQUEST_INTERVAL)) if value is None else value
    try:
        seconds = float(raw)
    except ValueError as exc:
        raise FlowError("SIDECAR_FLOW_MIN_INTERVAL must be a number of seconds") from exc
    if seconds < 0:
        raise FlowError("SIDECAR_FLOW_MIN_INTERVAL must be >= 0")
    return seconds
