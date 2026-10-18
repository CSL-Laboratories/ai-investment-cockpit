"""Sidecar-only Alpaca paper execution.

This is an experimental broker path. It is not the cockpit's single order path
(risk, then mode, then approval, then the gateway), and it must not be treated as one.
Orders go only to https://paper-api.alpaca.markets, only after that host returns an
ACTIVE account with no live-mode marker, and only when the caller passed execute=True.

The read-only observer in ``alpaca.py`` is a different client and a different credential
loader. This module does not call it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

import httpx

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"
FORBIDDEN_TRADING_HOST = "https://api.alpaca.markets"
_CLIENT_ID = re.compile(r"^[A-Za-z0-9]{1,48}$")


class PaperTradeError(RuntimeError):
    """A paper-trade request was refused before or during the broker call."""


class LimitBreach(PaperTradeError):
    """A pre-trade limit or the kill switch refused the order."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PaperLimits:
    max_notional_per_order: Decimal = Decimal("2000")
    max_gross_exposure: Decimal = Decimal("20000")
    max_positions: int = 3
    max_daily_loss: Decimal = Decimal("500")
    allowlist: tuple[str, ...] = ("SPY", "QQQ", "IWM")
    min_order_notional: Decimal = Decimal("1")


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    notional: Decimal
    qty: Decimal | None
    client_order_id: str
    strategy_id: str


def make_client_order_id(*, day: str, symbol: str, side: str, notional: Decimal) -> str:
    """Stable id for one intent on one New York session.

    Re-running the same rebalance reuses the id so the broker call is idempotent.
    """
    cents = notional.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    payload = f"{day}|{symbol.upper()}|{side}|{format(cents, 'f')}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    text = f"sc{day.replace('-', '')}{symbol.upper()}{side[:1].upper()}{digest}"
    text = re.sub(r"[^A-Za-z0-9]", "", text)[:48]
    if _CLIENT_ID.fullmatch(text) is None:
        raise PaperTradeError("client order id failed its own alphabet check")
    return text


def assert_trade_url(url: str, method: str) -> None:
    if PAPER_BASE_URL != "https://paper-api.alpaca.markets":
        raise PaperTradeError("paper base URL constant was modified")
    if url.startswith(FORBIDDEN_TRADING_HOST):
        raise PaperTradeError("refusing the live trading host")
    method = method.upper()
    if method == "GET" and (
        url.startswith(DATA_BASE_URL + "/v2/stocks/bars")
        or url.startswith(DATA_BASE_URL + "/v2/stocks/quotes")
    ):
        return
    if not url.startswith(PAPER_BASE_URL + "/"):
        raise PaperTradeError("refusing a URL that is not the paper trading host")
    path = url[len(PAPER_BASE_URL) :]
    if method == "GET" and path.startswith(("/v2/account", "/v2/positions", "/v2/orders", "/v2/clock")):
        return
    if method == "POST" and path == "/v2/orders":
        return
    if method == "DELETE" and path.startswith("/v2/orders/"):
        return
    raise PaperTradeError(f"refusing {method} {path}")


def assert_paper_account(account: dict, *, requested_url: str) -> None:
    """Require a successful read of /v2/account on the paper host.

    Alpaca's trading payload has no dedicated paper boolean. A 200 from the pinned paper
    host is the broker's attestation that the key belongs to a paper account. Any explicit
    mode field must still say paper, and a blocked or non-ACTIVE account is refused.
    """
    if requested_url != PAPER_BASE_URL + "/v2/account":
        raise PaperTradeError("account was not read from the paper host")
    if not isinstance(account, dict):
        raise PaperTradeError("account payload was not an object")
    if str(account.get("status", "")).upper() != "ACTIVE":
        raise PaperTradeError("refusing to trade an account that is not ACTIVE")
    if account.get("trading_blocked") is True or account.get("account_blocked") is True:
        raise PaperTradeError("account is blocked")
    for field in ("account_type", "trading_mode", "environment"):
        value = account.get(field)
        if value is None:
            continue
        text = str(value).strip().lower()
        if text not in {"paper", "paper_trading"}:
            raise PaperTradeError(f"account field {field} did not report a paper account")
    equity = account.get("equity")
    if equity is None:
        raise PaperTradeError("account payload has no equity")


def _dec(value, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise PaperTradeError(f"{field} is not a decimal") from exc
    if not number.is_finite():
        raise PaperTradeError(f"{field} is not finite")
    return number


def check_limits(
    intent: OrderIntent,
    *,
    equity: Decimal,
    last_equity: Decimal | None,
    positions: list[dict],
    limits: PaperLimits,
    kill_file: Path,
) -> None:
    if kill_file.exists():
        raise LimitBreach("kill_switch")
    if intent.side not in {"buy", "sell"}:
        raise LimitBreach("side")
    if intent.symbol not in limits.allowlist:
        raise LimitBreach("symbol_not_allowlisted")
    if intent.notional < limits.min_order_notional:
        raise LimitBreach("below_min_notional")
    if intent.notional > limits.max_notional_per_order:
        raise LimitBreach("max_notional_per_order")
    if last_equity is not None and equity <= last_equity - limits.max_daily_loss:
        raise LimitBreach("daily_loss_stop")
    gross = Decimal("0")
    held: set[str] = set()
    for row in positions:
        qty = _dec(row.get("qty", "0"), "qty")
        value = abs(_dec(row.get("market_value", "0"), "market_value"))
        gross += value
        if qty != 0:
            held.add(str(row.get("symbol", "")).upper())
    if intent.side == "buy" and gross + intent.notional > limits.max_gross_exposure:
        raise LimitBreach("max_gross_exposure")
    if intent.side == "buy" and intent.symbol not in held and len(held) >= limits.max_positions:
        raise LimitBreach("max_positions")
    if intent.side == "sell":
        match = next((row for row in positions if str(row.get("symbol", "")).upper() == intent.symbol), None)
        if match is None:
            raise LimitBreach("sell_without_position")
        held_qty = _dec(match.get("qty", "0"), "qty")
        if intent.qty is None or intent.qty <= 0 or intent.qty > held_qty:
            raise LimitBreach("sell_qty")


class PaperTradeClient:
    """POST/DELETE only against the paper host, and only after a paper account read."""

    def __init__(self, key_id: str, secret_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not key_id or not secret_key:
            raise PaperTradeError("missing sidecar paper credentials")
        assert_trade_url(PAPER_BASE_URL + "/v2/account", "GET")
        self._key_id = key_id
        self._secret_key = secret_key
        self._client = httpx.Client(transport=transport, timeout=30.0)
        self._paper_attested = False

    def __repr__(self) -> str:
        return "PaperTradeClient(base='https://paper-api.alpaca.markets')"

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, *, params=None, json_body=None, allow_404: bool = False):
        assert_trade_url(url, method)
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
            raise PaperTradeError(f"paper request failed with status {response.status_code}")
        if response.content:
            return response.json()
        return {}

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
            raise PaperTradeError("positions response was not a list")
        return body

    def portfolio_history(self, *, period: str = "1A") -> dict:
        self._require_attestation()
        return self._request(
            "GET",
            PAPER_BASE_URL + "/v2/account/portfolio/history",
            params={"period": period, "timeframe": "1D"},
        )

    def closed_orders(self, *, limit: int = 100) -> list:
        self._require_attestation()
        body = self._request(
            "GET",
            PAPER_BASE_URL + "/v2/orders",
            params={"status": "closed", "limit": str(limit), "direction": "desc"},
        )
        if not isinstance(body, list):
            raise PaperTradeError("orders response was not a list")
        return body

    def open_orders(self) -> list:
        self._require_attestation()
        body = self._request(
            "GET",
            PAPER_BASE_URL + "/v2/orders",
            params={"status": "open", "limit": "50"},
        )
        if not isinstance(body, list):
            raise PaperTradeError("orders response was not a list")
        return body

    def find_by_client_order_id(self, client_order_id: str) -> dict | None:
        self._require_attestation()
        return self._request(
            "GET",
            PAPER_BASE_URL + "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
            allow_404=True,
        )

    def daily_bars(self, symbols: list[str], *, start: str) -> dict:
        self._require_attestation()
        if not symbols:
            return {"bars": {}}
        return self._request(
            "GET",
            DATA_BASE_URL + "/v2/stocks/bars",
            params={
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": start,
                "adjustment": "all",
                "feed": "iex",
                "limit": "10000",
            },
        )

    def place_order(self, intent: OrderIntent) -> dict:
        self._require_attestation()
        existing = self.find_by_client_order_id(intent.client_order_id)
        if existing:
            return {"result": "already_present", "order": existing}
        body: dict = {
            "symbol": intent.symbol,
            "side": intent.side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": intent.client_order_id,
        }
        if intent.side == "buy":
            body["notional"] = format(intent.notional.quantize(Decimal("0.01"), rounding=ROUND_DOWN), "f")
        else:
            if intent.qty is None:
                raise PaperTradeError("sell order has no quantity")
            body["qty"] = format(intent.qty, "f")
        order = self._request("POST", PAPER_BASE_URL + "/v2/orders", json_body=body)
        return {"result": "submitted", "order": order}

    def cancel_sidecar_orders(self) -> list[str]:
        """Cancel open orders whose client id starts with ``sc``. Used when the kill file appears."""
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

    def _require_attestation(self) -> None:
        if not self._paper_attested:
            self.account()
