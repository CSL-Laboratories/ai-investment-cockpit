"""WP3 — ``cik_map.py``: SEC ``company_tickers.json`` -> watchlist ticker -> zero-padded CIK.

The dossier data flow opens with "Watchlist tickers map to CIKs", and the
submissions/companyfacts APIs are addressed by CIK — so the first ingest step is turning
the human watchlist (tickers) into the 10-digit CIKs everything downstream keys on.

SEC publishes the mapping at ``company_tickers.json`` as a dict keyed by stringified row
index::

    {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
     "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}, ...}

``cik_str`` is an integer with the leading zeros dropped; we zero-pad it back to the
canonical 10 digits (:func:`filings.edgar.fetch.pad_cik`) so the ``Company.cik`` /
``Filing.cik`` / ``PollCursor.cik`` columns all agree (the WP1a barrier shared key).

This module never opens a connection or names a sec.gov host: :func:`fetch_cik_map` routes
the one network read through :class:`filings.edgar.fetch.SecFetcher` (Invariant 4).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from filings.edgar.fetch import SEC_COMPANY_TICKERS_URL, SecFetcher, pad_cik


class UnknownTickerError(KeyError):
    """A watchlist ticker was not found in the SEC company_tickers table."""


def build_ticker_index(company_tickers: Mapping[str, Any]) -> dict[str, str]:
    """Turn the raw ``company_tickers.json`` structure into ``{TICKER: cik10}``.

    Tickers are upper-cased (the SEC table is already upper-case, but the watchlist may not
    be) and CIKs zero-padded to 10 digits. A malformed row (missing ``ticker``/``cik_str``)
    is skipped rather than aborting the whole map — per-item isolation, as in the poller.
    """
    index: dict[str, str] = {}
    for row in company_tickers.values():
        if not isinstance(row, Mapping):
            continue
        ticker = row.get("ticker")
        cik = row.get("cik_str")
        if ticker is None or cik is None:
            continue
        try:
            index[str(ticker).strip().upper()] = pad_cik(cik)
        except ValueError:
            continue  # a non-numeric cik_str is malformed — skip it, never crash the map
    return index


def map_watchlist(
    company_tickers: Mapping[str, Any],
    tickers: Iterable[str],
    *,
    strict: bool = True,
) -> dict[str, str]:
    """Resolve exactly the watchlist ``tickers`` to zero-padded CIKs.

    :param strict: if True (default), a ticker missing from the SEC table raises
        :class:`UnknownTickerError` (fail-closed — we do not silently drop a watchlist
        company). If False, missing tickers are omitted from the result.
    """
    index = build_ticker_index(company_tickers)
    resolved: dict[str, str] = {}
    for raw in tickers:
        t = str(raw).strip().upper()
        if t in index:
            resolved[t] = index[t]
        elif strict:
            raise UnknownTickerError(
                f"watchlist ticker {t!r} not found in SEC company_tickers.json"
            )
    return resolved


def fetch_cik_map(
    fetcher: SecFetcher,
    tickers: Iterable[str],
    *,
    strict: bool = True,
) -> dict[str, str]:
    """Fetch ``company_tickers.json`` via the SEC fetcher and resolve the watchlist.

    The ONE network read here goes through :class:`SecFetcher` (rate-limited, UA-enforcing,
    distinct-error-surfacing) — this module opens no connection of its own (Invariant 4).
    """
    company_tickers = fetcher.get_json(SEC_COMPANY_TICKERS_URL)
    return map_watchlist(company_tickers, tickers, strict=strict)
