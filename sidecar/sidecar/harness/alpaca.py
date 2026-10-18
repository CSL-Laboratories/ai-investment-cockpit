"""Read-only client pinned to Alpaca's paper trading host.

The trading base URL is a constant. There is no constructor argument that can point this
client at ``https://api.alpaca.markets``. Requests are GET only.
"""

from __future__ import annotations

import httpx

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"
FORBIDDEN_TRADING_HOST = "https://api.alpaca.markets"

_PAPER_PREFIXES = ("/v2/account", "/v2/positions", "/v2/orders")
_DATA_PREFIXES = ("/v2/stocks/bars", "/v2/stocks/quotes")


class PaperEndpointError(RuntimeError):
    """The request was not a read against the paper or market-data host."""


def assert_read_url(url: str) -> None:
    if url.startswith(FORBIDDEN_TRADING_HOST):
        raise PaperEndpointError("refusing the live trading host")
    paper = url.startswith(PAPER_BASE_URL + "/") and any(
        url[len(PAPER_BASE_URL) :].startswith(prefix) for prefix in _PAPER_PREFIXES
    )
    data = url.startswith(DATA_BASE_URL + "/") and any(
        url[len(DATA_BASE_URL) :].startswith(prefix) for prefix in _DATA_PREFIXES
    )
    if not (paper or data):
        raise PaperEndpointError("refusing a URL that is not a paper read or a stock data read")
    if PAPER_BASE_URL != "https://paper-api.alpaca.markets":
        raise PaperEndpointError("paper base URL constant was modified")


class PaperReadClient:
    def __init__(self, key_id: str, secret_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not key_id or not secret_key:
            raise PaperEndpointError("missing paper credentials")
        self._key_id = key_id
        self._secret_key = secret_key
        assert_read_url(PAPER_BASE_URL + "/v2/account")
        self._client = httpx.Client(transport=transport, timeout=30.0)

    def __repr__(self) -> str:
        return "PaperReadClient(base='https://paper-api.alpaca.markets')"

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, *, params: dict | None = None):
        if method.upper() != "GET":
            raise PaperEndpointError("sidecar paper client is read-only")
        assert_read_url(url)
        response = self._client.get(
            url,
            params=params,
            headers={
                "APCA-API-KEY-ID": self._key_id,
                "APCA-API-SECRET-KEY": self._secret_key,
                "Accept": "application/json",
            },
        )
        if response.status_code >= 400:
            raise PaperEndpointError(f"paper read failed with status {response.status_code}")
        return response.json()

    def account(self) -> dict:
        return self._request("GET", PAPER_BASE_URL + "/v2/account")

    def portfolio_history(self, *, period: str = "1A", timeframe: str = "1D") -> dict:
        return self._request(
            "GET",
            PAPER_BASE_URL + "/v2/account/portfolio/history",
            params={"period": period, "timeframe": timeframe},
        )

    def positions(self) -> list:
        body = self._request("GET", PAPER_BASE_URL + "/v2/positions")
        if not isinstance(body, list):
            raise PaperEndpointError("positions response was not a list")
        return body

    def closed_orders(self, *, limit: int = 100) -> list:
        body = self._request(
            "GET",
            PAPER_BASE_URL + "/v2/orders",
            params={"status": "closed", "limit": str(limit), "direction": "desc"},
        )
        if not isinstance(body, list):
            raise PaperEndpointError("orders response was not a list")
        return body

    def daily_bars(
        self,
        symbols: list[str],
        *,
        start: str,
        end: str | None = None,
        page_token: str | None = None,
    ) -> dict:
        if not symbols:
            return {"bars": {}}
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start,
            "adjustment": "all",
            "feed": "iex",
            "limit": "10000",
        }
        if end:
            params["end"] = end
        if page_token:
            params["page_token"] = page_token
        return self._request("GET", DATA_BASE_URL + "/v2/stocks/bars", params=params)

    def adjusted_daily_bars(self, symbols: list[str], *, start: str) -> dict:
        """Follow ``next_page_token`` and merge adjusted IEX daily bars.

        ``adjustment=all`` is set on every page. This method only GETs the data host.
        """
        merged: dict[str, list] = {symbol: [] for symbol in symbols}
        token: str | None = None
        seen: set[str] = set()
        for _page in range(40):
            if token is not None:
                if token in seen:
                    raise PaperEndpointError("daily bars page token repeated")
                seen.add(token)
            page = self.daily_bars(symbols, start=start, page_token=token)
            if not isinstance(page, dict):
                raise PaperEndpointError("daily bars response was not an object")
            bars = page.get("bars") or {}
            if not isinstance(bars, dict):
                raise PaperEndpointError("daily bars payload was not a map")
            for symbol, rows in bars.items():
                if not isinstance(rows, list):
                    raise PaperEndpointError("daily bars for a symbol were not a list")
                merged.setdefault(str(symbol).upper(), []).extend(rows)
            nxt = page.get("next_page_token")
            if not nxt:
                return {"bars": merged}
            token = str(nxt)
        raise PaperEndpointError("daily bars pagination exceeded 40 pages")
