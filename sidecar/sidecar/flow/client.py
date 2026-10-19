"""GET-only Alpaca options data client.

The free feed is `indicative`. `opra` is requested only when config says so, and a
rejected OPRA call is not retried on the other feed. The live trading host is refused.
This client has no write method.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from sidecar.flow.config import (
    CONTRACT_PAGE_LIMIT,
    FEEDS,
    FLOW_UNIVERSE,
    REQUEST_RETRIES,
    STOCK_BATCH,
    TRADE_BATCH,
    TRADE_DELAY_MINUTES,
    FlowError,
    page_cap,
    request_interval,
)
from sidecar.harness.paper_credentials import load_sidecar_paper_credentials

logger = logging.getLogger("sidecar.flow")

DATA_HOST = "data.alpaca.markets"
PAPER_HOST = "paper-api.alpaca.markets"
LIVE_HOST = "api.alpaca.markets"
_DATA_PATHS = (
    "/v1beta1/options/snapshots/",
    "/v1beta1/options/trades",
    "/v2/stocks/snapshots",
)
_PAPER_PATHS = ("/v2/options/contracts", "/v2/calendar")


class FlowHttpError(FlowError):
    def __init__(self, status: int) -> None:
        super().__init__(f"market data request failed with status {status}")
        self.status = status


def _require_delayed_trade_window(start: str, end: str, now: datetime | None) -> None:
    """Reject a missing bound or an end inside the live OPRA window."""
    opened = _parse_trade_bound(start, "start")
    closing = _parse_trade_bound(end, "end")
    moment = datetime.now(timezone.utc) if now is None else now
    if moment.tzinfo is None:
        raise FlowError("options trades clock must be timezone-aware")
    latest = moment.astimezone(timezone.utc) - timedelta(minutes=TRADE_DELAY_MINUTES)
    if closing <= opened:
        raise FlowError("options trades end is not after the start")
    if closing.astimezone(timezone.utc) > latest:
        raise FlowError("options trades end is inside the real-time OPRA window")


def _parse_trade_bound(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FlowError(f"options trades require an explicit {label}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FlowError(f"options trades {label} is not a timestamp") from exc
    if parsed.tzinfo is None:
        raise FlowError(f"options trades {label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _next_token(body: dict) -> str | None:
    """Trading responses use next_page_token. Some contract payloads use page_token."""
    if "next_page_token" in body:
        token = body.get("next_page_token")
    elif "page_token" in body:
        token = body.get("page_token")
    else:
        token = None
    if not isinstance(token, str) or not token:
        return None
    return token


def assert_flow_url(url: str, method: str) -> None:
    if method.upper() != "GET":
        raise FlowError("flow data client refuses every method except GET")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise FlowError("refusing a non-https URL")
    if parsed.netloc == LIVE_HOST or "://api.alpaca.markets" in url:
        raise FlowError("refusing the live trading host")
    if parsed.netloc == DATA_HOST and parsed.path.startswith(_DATA_PATHS):
        return
    if parsed.netloc == PAPER_HOST and parsed.path.startswith(_PAPER_PATHS):
        return
    raise FlowError(f"refusing GET {parsed.netloc}{parsed.path}")


class FlowDataClient:
    def __init__(
        self,
        key_id: str,
        secret_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval: float | None = None,
        sleeper: Callable[[float], None] | None = None,
        max_pages: int | None = None,
    ) -> None:
        if not key_id or not secret_key:
            raise FlowError("missing sidecar paper credentials")
        self._key_id = key_id
        self._secret_key = secret_key
        chosen_pages = page_cap() if max_pages is None else max_pages
        if isinstance(chosen_pages, bool) or not isinstance(chosen_pages, int) or chosen_pages < 1:
            raise FlowError("max pages must be a positive integer")
        self.max_pages = chosen_pages
        self._min_interval = 0.0 if min_interval is None else min_interval
        if self._min_interval < 0:
            raise FlowError("request interval must be >= 0")
        self._sleep = time.sleep if sleeper is None else sleeper
        self._last_request: float | None = None
        self._client = httpx.Client(transport=transport, timeout=30.0)

    @classmethod
    def from_sidecar_credentials(
        cls,
        *,
        transport: httpx.BaseTransport | None = None,
        max_pages: int | None = None,
    ) -> FlowDataClient:
        key_id, secret_key = load_sidecar_paper_credentials()
        return cls(
            key_id,
            secret_key,
            transport=transport,
            min_interval=request_interval(),
            max_pages=max_pages,
        )

    def __repr__(self) -> str:
        return "FlowDataClient()"

    def close(self) -> None:
        self._client.close()

    def _pace(self) -> None:
        if self._min_interval <= 0 or self._last_request is None:
            self._last_request = time.monotonic()
            return
        wait = self._min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            self._sleep(wait)
        self._last_request = time.monotonic()

    def _backoff(self, response: httpx.Response, attempt: int) -> None:
        header = response.headers.get("Retry-After")
        try:
            pause = float(header) if header is not None else min(8.0, 2.0**attempt)
        except ValueError:
            pause = min(8.0, 2.0**attempt)
        if pause < 0:
            pause = 0.0
        if pause > 30:
            pause = 30.0
        if pause > 0:
            self._sleep(pause)

    def _request(self, url: str, params: dict[str, str]) -> httpx.Response:
        assert_flow_url(url, "GET")
        headers = {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Accept": "application/json",
        }
        response: httpx.Response | None = None
        for attempt in range(REQUEST_RETRIES):
            self._pace()
            response = self._client.request("GET", url, params=params, headers=headers)
            if response.status_code != 429 or attempt + 1 == REQUEST_RETRIES:
                return response
            self._backoff(response, attempt)
        assert response is not None
        return response

    def _get(self, url: str, params: dict[str, str]) -> dict:
        response = self._request(url, params)
        if response.status_code >= 400:
            raise FlowHttpError(response.status_code)
        body = response.json()
        if not isinstance(body, dict):
            raise FlowError("market data response was not an object")
        return body

    def _pages(self, url: str, params: dict[str, str], collection: str) -> dict:
        """Follow page tokens up to this client's cap.

        A token still present after the cap is a truncated result, not a failed
        scan. The rows already received are kept.
        """
        merged: dict = {
            collection: {} if collection != "option_contracts" else [],
            "pages": 0,
            "truncated": False,
        }
        token: str | None = None
        for page in range(self.max_pages):
            query = dict(params)
            if token:
                query["page_token"] = token
            body = self._get(url, query)
            merged["pages"] = page + 1
            chunk = body.get(collection)
            if collection == "option_contracts":
                if not isinstance(chunk, list):
                    raise FlowError("contracts response has no option_contracts list")
                merged[collection].extend(chunk)
            elif collection == "trades":
                if not isinstance(chunk, dict):
                    raise FlowError("trades response was not an object")
                for symbol, rows in chunk.items():
                    if isinstance(rows, list):
                        merged[collection].setdefault(symbol, []).extend(rows)
            else:
                if not isinstance(chunk, dict):
                    raise FlowError(f"{collection} response was not an object")
                merged[collection].update(chunk)
            token = _next_token(body)
            if not token:
                return merged
        merged["truncated"] = True
        logger.warning(
            "flow pagination stopped after %s pages for %s; the response is truncated",
            self.max_pages,
            url,
        )
        return merged

    def snapshots(self, underlying: str, feed: str) -> dict:
        if underlying not in FLOW_UNIVERSE:
            raise FlowError("underlying is not on the flow watchlist")
        if feed not in FEEDS:
            raise FlowError("options feed must be 'indicative' or 'opra'")
        url = f"https://{DATA_HOST}/v1beta1/options/snapshots/{underlying}"
        return self._pages(url, {"feed": feed, "limit": "1000"}, "snapshots")

    def trades(self, symbols: list[str], *, start: str, end: str, now: datetime | None = None) -> dict:
        """Historical trades. This endpoint rejects a feed parameter.

        `start` and `end` are required. `end` must be at least 16 minutes before
        `now`, because the latest 15 minutes are real-time OPRA. A 403 after
        that still falls back to snapshot scoring.
        """
        _require_delayed_trade_window(start, end, now)
        if not symbols:
            return {
                "trades": {},
                "pages": 0,
                "batches": 0,
                "available": True,
                "reason": None,
                "truncated": False,
                "truncated_symbols": [],
            }
        url = f"https://{DATA_HOST}/v1beta1/options/trades"
        merged: dict[str, list] = {}
        pages = 0
        batches = 0
        truncated_symbols: list[str] = []
        try:
            for offset in range(0, len(symbols), TRADE_BATCH):
                batch = symbols[offset : offset + TRADE_BATCH]
                batches += 1
                page = self._pages(
                    url,
                    {"symbols": ",".join(batch), "start": start, "end": end, "limit": "10000"},
                    "trades",
                )
                pages += int(page["pages"])
                for symbol, rows in page["trades"].items():
                    merged.setdefault(symbol, []).extend(rows)
                if page.get("truncated"):
                    truncated_symbols.extend(batch)
        except FlowHttpError as exc:
            if exc.status != 403:
                raise
            return {
                "trades": {},
                "pages": pages,
                "batches": batches,
                "available": False,
                "reason": (
                    "options trades returned 403. Free historical data excludes the latest "
                    "15 minutes of OPRA. Scoring uses each snapshot's latest trade only, "
                    "so sweeps are not detected."
                ),
                "truncated": False,
                "truncated_symbols": [],
            }
        return {
            "trades": merged,
            "pages": pages,
            "batches": batches,
            "available": True,
            "reason": None,
            "truncated": bool(truncated_symbols),
            "truncated_symbols": truncated_symbols,
        }

    def contracts(self, underlying: str, *, expiration_gte: str, expiration_lte: str) -> dict:
        if underlying not in FLOW_UNIVERSE:
            raise FlowError("underlying is not on the flow watchlist")
        if not expiration_gte or not expiration_lte:
            raise FlowError("contracts require an explicit expiration window")
        url = f"https://{PAPER_HOST}/v2/options/contracts"
        # Without both bounds Alpaca defaults expiration_date_lte to the next weekend
        # and the open-interest join misses the rest of the chain.
        return self._pages(
            url,
            {
                "underlying_symbols": underlying,
                "status": "active",
                "expiration_date_gte": expiration_gte,
                "expiration_date_lte": expiration_lte,
                "limit": str(CONTRACT_PAGE_LIMIT),
            },
            "option_contracts",
        )

    def calendar(self, start: str, end: str) -> list[dict]:
        url = f"https://{PAPER_HOST}/v2/calendar"
        response = self._request(url, {"start": start, "end": end})
        if response.status_code >= 400:
            raise FlowHttpError(response.status_code)
        body = response.json()
        if not isinstance(body, list):
            raise FlowError("calendar response was not a list")
        return body

    def _price_from_snapshot(self, row: dict) -> float | None:
        trade = row.get("latestTrade") if isinstance(row.get("latestTrade"), dict) else {}
        price = trade.get("p")
        if isinstance(price, (int, float)) and not isinstance(price, bool):
            return float(price)
        daily = row.get("dailyBar") if isinstance(row.get("dailyBar"), dict) else {}
        close = daily.get("c")
        if isinstance(close, (int, float)) and not isinstance(close, bool):
            return float(close)
        return None

    def stock_prices(self, symbols: list[str]) -> dict[str, float | None]:
        """One IEX snapshot call per STOCK_BATCH names, not one call per ticker."""
        if any(symbol not in FLOW_UNIVERSE for symbol in symbols):
            unknown = next(symbol for symbol in symbols if symbol not in FLOW_UNIVERSE)
            raise FlowError(f"underlying is not on the flow watchlist: {unknown}")
        found: dict[str, float | None] = {symbol: None for symbol in symbols}
        url = f"https://{DATA_HOST}/v2/stocks/snapshots"
        for offset in range(0, len(symbols), STOCK_BATCH):
            batch = symbols[offset : offset + STOCK_BATCH]
            if not batch:
                continue
            body = self._get(url, {"symbols": ",".join(batch), "feed": "iex"})
            if isinstance(body.get("snapshots"), dict):
                body = body["snapshots"]
            for symbol in batch:
                row = body.get(symbol)
                if isinstance(row, dict):
                    found[symbol] = self._price_from_snapshot(row)
        return found

    def stock_price(self, underlying: str) -> float | None:
        return self.stock_prices([underlying])[underlying]
