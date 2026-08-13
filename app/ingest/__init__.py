"""WP3 — market ingestor: Alpaca news WS, Finnhub poller, calendars -> MarketEvent.

W20 added two evidence feeds: EDGAR Delta events through the hardened ``edgar_brew`` poller,
and FINRA short-interest/Reg SHO enrichment. RW-11 runs the deterministic EDGAR extraction
pipeline in-process before that poller reads the cockpit's own Delta rows; the foreign-brew
reader remains only as a fallback until RW-12's live-day/precision retirement gate is met.
"""
from ingest.alpaca_news import AlpacaNewsConsumer
from ingest.calendars import pull_calendar
# THE ONE clock-contract error type + validator, shared by BOTH W20 connectors. Imported from its
# own home, never from either connector: one contract, one error (ingest/clock_contract.py).
from ingest.clock_contract import ClockContractError, resolve_wall_clock
from ingest.dedup import Dedup
from ingest.edgar_brew import (
    BrewFeedStatus,
    BrewRunOutcome,
    EdgarBrewPoller,
    make_brew_fetch,
    read_brew_run_outcome,
)
from ingest.finnhub_poll import FinnhubPoller
from ingest.finra_short_interest import FinraShortInterestPoller, make_finra_fetch
from ingest.normalize import escape_untrusted, to_market_event
from ingest.ratelimit import TokenBucket

__all__ = ["AlpacaNewsConsumer", "FinnhubPoller", "pull_calendar", "Dedup",
           "TokenBucket", "to_market_event", "escape_untrusted",
           "EdgarBrewPoller", "make_brew_fetch", "read_brew_run_outcome",
           "BrewFeedStatus", "BrewRunOutcome", "ClockContractError", "resolve_wall_clock",
           "FinraShortInterestPoller", "make_finra_fetch"]
