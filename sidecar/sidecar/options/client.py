"""Paper-only options client.

Posts go to https://paper-api.alpaca.markets and only after that host returns an
ACTIVE account. Snapshots are read from the market-data host with feed=indicative.
The live trading host is refused. This client is not the cockpit order path.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from sidecar.harness.trade import (
    DATA_BASE_URL,
    FORBIDDEN_TRADING_HOST,
    PAPER_BASE_URL,
    PaperTradeError,
    assert_paper_account,
)
from sidecar.options.risk import OptionsRiskError, validate_mleg_body

_OPTION_LEVEL_FIELDS = (
    "option_trading_level",
    "options_trading_level",
    "option_approved_level",
    "options_approved_level",
)


class OptionsClientError(PaperTradeError):
    """An options request was refused before or during the broker call."""


def assert_options_url(url: str, method: str) -> None:
    if PAPER_BASE_URL != "https://paper-api.alpaca.markets":
        raise OptionsClientError("paper base URL constant was modified")
    if url.startswith(FORBIDDEN_TRADING_HOST) or "://api.alpaca.markets" in url:
        raise OptionsClientError("refusing the live trading host")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise OptionsClientError("refusing a non-https URL")
    method = method.upper()
    if parsed.netloc == "data.alpaca.markets":
        if method == "GET" and (
            parsed.path.startswith("/v1beta1/options/snapshots/")
            or parsed.path.startswith("/v2/stocks/bars")
        ):
            return
        raise OptionsClientError(f"refusing {method} {parsed.path}")
    if parsed.netloc != "paper-api.alpaca.markets":
        raise OptionsClientError("refusing a URL that is not the paper trading host")
    path = parsed.path
    if method == "GET" and path.startswith(
        ("/v2/account", "/v2/positions", "/v2/orders", "/v2/clock", "/v2/options/contracts")
    ):
        return
    if method == "POST" and path == "/v2/orders":
        return
    if method == "DELETE" and path.startswith("/v2/orders/"):
        return
    raise OptionsClientError(f"refusing {method} {path}")


def options_level_approved(account: dict) -> bool:
    """Spreads need an explicit approval of at least 3. A missing field is not approval."""
    seen = False
    for field in _OPTION_LEVEL_FIELDS:
        if field not in account or account.get(field) is None:
            continue
        seen = True
        try:
            level = int(str(account.get(field)))
        except (TypeError, ValueError):
            return False
        if level < 3:
            return False
    return seen


class OptionsPaperClient:
    """GET, plus a multi-leg limit POST, only after a paper account read."""

    def __init__(self, key_id: str, secret_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not key_id or not secret_key:
            raise OptionsClientError("missing sidecar paper credentials")
        assert_options_url(PAPER_BASE_URL + "/v2/account", "GET")
        self._key_id = key_id
        self._secret_key = secret_key
        self._client = httpx.Client(transport=transport, timeout=30.0)
        self._paper_attested = False

    def __repr__(self) -> str:
        return "OptionsPaperClient(base='https://paper-api.alpaca.markets')"

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, *, params=None, json_body=None, allow_404: bool = False):
        assert_options_url(url, method)
        response = self._client.request(
            method.upper(),
            url,
            params=params,
            json=json_body,
            headers={
                "APCA-API-KEY-ID": self._key_id,
                "APCA-API-SECRET-KEY": self._secret_key,
                "Accept": "application/json",
            },
        )
        if response.status_code == 404 and allow_404:
            return None
        if response.status_code >= 400:
            raise OptionsClientError(f"paper request failed with status {response.status_code}")
        if response.content:
            return response.json()
        return {}

    def _require_attestation(self) -> None:
        if not self._paper_attested:
            self.account()

    def account(self) -> dict:
        url = PAPER_BASE_URL + "/v2/account"
        body = self._request("GET", url)
        assert_paper_account(body, requested_url=url)
        self._paper_attested = True
        return body

    def positions(self) -> list:
        self._require_attestation()
        body = self._request("GET", PAPER_BASE_URL + "/v2/positions")
        if not isinstance(body, list):
            raise OptionsClientError("positions response was not a list")
        return body

    def open_orders(self) -> list:
        self._require_attestation()
        body = self._request(
            "GET",
            PAPER_BASE_URL + "/v2/orders",
            params={"status": "open", "limit": "100", "nested": "true"},
        )
        if not isinstance(body, list):
            raise OptionsClientError("orders response was not a list")
        return body

    def find_by_client_order_id(self, client_order_id: str) -> dict | None:
        self._require_attestation()
        return self._request(
            "GET",
            PAPER_BASE_URL + "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
            allow_404=True,
        )

    def contracts(
        self,
        underlying: str,
        *,
        expiry_gte: str,
        expiry_lte: str,
        right: str,
    ) -> list[dict]:
        self._require_attestation()
        if underlying not in {"SPY", "QQQ", "IWM"}:
            raise OptionsClientError("underlying")
        if right not in {"call", "put"}:
            raise OptionsClientError("right")
        rows: list[dict] = []
        token = None
        for _page in range(20):
            params = {
                "underlying_symbols": underlying,
                "status": "active",
                "expiration_date_gte": expiry_gte,
                "expiration_date_lte": expiry_lte,
                "type": right,
                "limit": "1000",
            }
            if token:
                params["page_token"] = token
            body = self._request("GET", PAPER_BASE_URL + "/v2/options/contracts", params=params)
            if not isinstance(body, dict):
                raise OptionsClientError("contracts response was not an object")
            page = body.get("option_contracts")
            if not isinstance(page, list):
                raise OptionsClientError("contracts response has no option_contracts list")
            rows.extend(page)
            token = body.get("next_page_token")
            if not token:
                return rows
        raise OptionsClientError("contracts pagination did not finish")

    def snapshots(self, underlying: str) -> dict[str, dict]:
        self._require_attestation()
        if underlying not in {"SPY", "QQQ", "IWM"}:
            raise OptionsClientError("underlying")
        merged: dict[str, dict] = {}
        token = None
        for _page in range(20):
            params = {"feed": "indicative", "limit": "1000"}
            if token:
                params["page_token"] = token
            body = self._request(
                "GET",
                f"{DATA_BASE_URL}/v1beta1/options/snapshots/{underlying}",
                params=params,
            )
            if not isinstance(body, dict) or not isinstance(body.get("snapshots"), dict):
                raise OptionsClientError("snapshots response was missing the snapshots object")
            merged.update(body["snapshots"])
            token = body.get("next_page_token")
            if not token:
                return merged
        raise OptionsClientError("snapshot pagination did not finish")

    def daily_bars(self, symbols: list[str], *, start: str) -> dict:
        self._require_attestation()
        merged: dict[str, list] = {symbol: [] for symbol in symbols}
        token = None
        seen: set[str] = set()
        for _page in range(40):
            params = {
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": start,
                "adjustment": "all",
                "feed": "iex",
                "limit": "10000",
            }
            if token:
                if token in seen:
                    raise OptionsClientError("daily bars page token repeated")
                seen.add(token)
                params["page_token"] = token
            body = self._request("GET", DATA_BASE_URL + "/v2/stocks/bars", params=params)
            if not isinstance(body, dict) or not isinstance(body.get("bars"), dict):
                raise OptionsClientError("daily bars response was not an object")
            for symbol, rows in body["bars"].items():
                if not isinstance(rows, list):
                    raise OptionsClientError("daily bars for a symbol were not a list")
                merged.setdefault(str(symbol).upper(), []).extend(rows)
            nxt = body.get("next_page_token")
            if not nxt:
                return {"bars": merged}
            token = str(nxt)
        raise OptionsClientError("daily bars pagination did not finish")

    def place_mleg(self, body: dict, *, execute: bool, opening: bool) -> dict:
        if not execute:
            raise OptionsClientError("dry-run refused to post")
        try:
            validate_mleg_body(body, opening=opening)
        except OptionsRiskError as exc:
            raise OptionsClientError(exc.reason) from exc
        self._require_attestation()
        existing = self.find_by_client_order_id(str(body["client_order_id"]))
        if existing:
            return {"result": "already_present", "order": existing}
        # The broker body must not carry the local close_reason annotation.
        payload = {key: value for key, value in body.items() if key != "close_reason"}
        order = self._request("POST", PAPER_BASE_URL + "/v2/orders", json_body=payload)
        return {"result": "submitted", "order": order}

    def cancel_sidecar_orders(self) -> list[str]:
        cancelled = []
        for order in self.open_orders():
            client_id = str(order.get("client_order_id") or "")
            if not client_id.startswith("sc"):
                continue
            order_id = str(order.get("id") or "")
            if not order_id:
                continue
            self._request("DELETE", PAPER_BASE_URL + f"/v2/orders/{order_id}")
            cancelled.append(client_id)
        return cancelled
