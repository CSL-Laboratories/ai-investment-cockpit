"""WP3 tests — ``edgar/cik_map.py``: SEC company_tickers.json -> ticker -> zero-padded CIK.

Offline: the one network read is served through an injected transport (zero live calls).
"""
from __future__ import annotations

import json

import pytest

from filings.edgar.cik_map import (
    UnknownTickerError,
    build_ticker_index,
    fetch_cik_map,
    map_watchlist,
)
from filings.edgar.fetch import SEC_COMPANY_TICKERS_URL, SecFetcher
from tests.filings._sec_replay import FixtureTransport, VirtualTime
from tests.filings._sec_replay import SEC_TEST_USER_AGENT as UA

# The real SEC company_tickers.json shape: a dict keyed by stringified row index, each row
# {cik_str: int (leading zeros dropped), ticker, title}.
COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "2": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


def test_build_ticker_index_zero_pads_cik():
    idx = build_ticker_index(COMPANY_TICKERS)
    assert idx["AAPL"] == "0000320193"  # padded back to 10 digits
    assert idx["NVDA"] == "0001045810"
    assert idx["MSFT"] == "0000789019"


def test_build_ticker_index_skips_malformed_rows():
    raw = dict(COMPANY_TICKERS)
    raw["9"] = {"ticker": "BAD"}  # missing cik_str
    raw["10"] = {"cik_str": "not-a-number", "ticker": "NAN"}  # non-numeric cik
    raw["11"] = "totally malformed row"
    idx = build_ticker_index(raw)
    assert "BAD" not in idx
    assert "NAN" not in idx
    assert idx["AAPL"] == "0000320193"  # the good rows still map


def test_map_watchlist_resolves_case_insensitively():
    got = map_watchlist(COMPANY_TICKERS, ["aapl", "NVDA"])
    assert got == {"AAPL": "0000320193", "NVDA": "0001045810"}


def test_map_watchlist_strict_raises_on_unknown_ticker():
    with pytest.raises(UnknownTickerError):
        map_watchlist(COMPANY_TICKERS, ["AAPL", "ZZZZ"])


def test_map_watchlist_non_strict_omits_unknown():
    got = map_watchlist(COMPANY_TICKERS, ["AAPL", "ZZZZ"], strict=False)
    assert got == {"AAPL": "0000320193"}


def test_fetch_cik_map_routes_through_the_fetcher():
    registry = {SEC_COMPANY_TICKERS_URL: (200, json.dumps(COMPANY_TICKERS).encode())}
    transport = FixtureTransport(registry=registry)
    vt = VirtualTime()
    fetcher = SecFetcher(
        user_agent=UA, rate_per_sec=2.0, transport=transport,
        monotonic=vt.monotonic, sleep=vt.sleep,
    )
    got = fetch_cik_map(fetcher, ["AAPL", "NVDA"])
    assert got == {"AAPL": "0000320193", "NVDA": "0001045810"}
    # the request went through the UA-enforcing client
    assert transport.requests[0][1] == SEC_COMPANY_TICKERS_URL
    assert transport.requests[0][2]["User-Agent"] == UA
