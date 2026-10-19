"""GET-only Alpaca options data client.

The free feed is `indicative`. `opra` is requested only when config says so, and a
rejected OPRA call is not retried on the other feed. The live trading host is refused.
This client has no write method.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from sidecar.flow.config import CONTRACT_PAGE_LIMIT, FEEDS, MAX_PAGES, TRADE_BATCH, WATCHLIST, FlowError
from sidecar.harness.paper_credentials import load_sidecar_paper_credentials

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
    def __init__(self, key_id: str, secret_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not key_id or not secret_key:
            raise FlowError("missing sidecar paper credentials")
        self._key_id = key_id
        self._secret_key = secret_key
        self._client = httpx.Client(transport=transport, timeout=30.0)

    @classmethod
    def from_sidecar_credentials(cls, *, transport: httpx.BaseTransport | None = None) -> FlowDataClient:
        key_id, secret_key = load_sidecar_paper_credentials()
        return cls(key_id, secret_key, transport=transport)

    def __repr__(self) -> str:
        return "FlowDataClient()"

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, params: dict[str, str]) -> dict:
        assert_flow_url(url, "GET")
        response = self._client.request(
            "GET",
            url,
            params=params,
            headers={
                "APCA-API-KEY-ID": self._key_id,
                "APCA-API-SECRET-KEY": self._secret_key,
                "Accept": "application/json",
            },
        )
        if response.status_code >= 400:
            raise FlowHttpError(response.status_code)
        body = response.json()
        if not isinstance(body, dict):
            raise FlowError("market data response was not an object")
        return body

    def _pages(self, url: str, params: dict[str, str], collection: str) -> dict:
        merged: dict = {collection: {} if collection != "option_contracts" else [], "pages": 0}
        token: str | None = None
        for page in range(MAX_PAGES):
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
        raise FlowError("pagination did not finish")

    def snapshots(self, underlying: str, feed: str) -> dict:
        if underlying not in WATCHLIST:
            raise FlowError("underlying is not on the flow watchlist")
        if feed not in FEEDS:
            raise FlowError("options feed must be 'indicative' or 'opra'")
        url = f"https://{DATA_HOST}/v1beta1/options/snapshots/{underlying}"
        return self._pages(url, {"feed": feed, "limit": "1000"}, "snapshots")

    def trades(self, symbols: list[str], *, start: str, end: str) -> dict:
        """Historical trades. This endpoint rejects a feed parameter.

        A 403 means the key has no OPRA agreement. The caller scores from snapshots.
        """
        if not symbols:
            return {"trades": {}, "pages": 0, "batches": 0, "available": True, "reason": None}
        url = f"https://{DATA_HOST}/v1beta1/options/trades"
        merged: dict[str, list] = {}
        pages = 0
        batches = 0
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
        except FlowHttpError as exc:
            if exc.status != 403:
                raise
            return {
                "trades": {},
                "pages": pages,
                "batches": batches,
                "available": False,
                "reason": (
                    "options trades returned 403. This key has no OPRA agreement. "
                    "Scoring uses each snapshot's latest trade only, so sweeps are not detected."
                ),
            }
        return {"trades": merged, "pages": pages, "batches": batches, "available": True, "reason": None}

    def contracts(self, underlying: str, *, expiration_gte: str, expiration_lte: str) -> dict:
        if underlying not in WATCHLIST:
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
        assert_flow_url(url, "GET")
        response = self._client.request(
            "GET",
            url,
            params={"start": start, "end": end},
            headers={
                "APCA-API-KEY-ID": self._key_id,
                "APCA-API-SECRET-KEY": self._secret_key,
                "Accept": "application/json",
            },
        )
        if response.status_code >= 400:
            raise FlowHttpError(response.status_code)
        body = response.json()
        if not isinstance(body, list):
            raise FlowError("calendar response was not a list")
        return body

    def stock_price(self, underlying: str) -> float | None:
        if underlying not in WATCHLIST:
            raise FlowError("underlying is not on the flow watchlist")
        url = f"https://{DATA_HOST}/v2/stocks/snapshots"
        body = self._get(url, {"symbols": underlying, "feed": "iex"})
        if isinstance(body.get("snapshots"), dict):
            body = body["snapshots"]
        row = body.get(underlying)
        if not isinstance(row, dict):
            return None
        trade = row.get("latestTrade") if isinstance(row.get("latestTrade"), dict) else {}
        price = trade.get("p")
        if isinstance(price, (int, float)) and not isinstance(price, bool):
            return float(price)
        daily = row.get("dailyBar") if isinstance(row.get("dailyBar"), dict) else {}
        close = daily.get("c")
        if isinstance(close, (int, float)) and not isinstance(close, bool):
            return float(close)
        return None
