"""WP7 — the Alpaca paper-trading broker adapter (SEC-03).

``AlpacaPaperBroker`` is the concrete broker seam the ``OrderGateway`` holds PRIVATELY. Its
HTTP client is INJECTABLE (``client=`` in ``__init__``); the default is a small in-process
``FakeAlpaca`` simulator so unit tests need no network and no credentials. Production injects
``AlpacaPaperHTTPClient``, a deliberately small client pinned to Alpaca's PAPER hostname.

``submit_order(canonical_tuple)`` accepts ONLY the canonical resolved-order tuple from
``brokers.order_tuple`` (the gateway computes it), so the adapter can never be handed an
un-validated order. ``get_account`` / ``get_positions`` produce a ``PortfolioSnapshot``; when
an ``OrchestratorState`` is supplied they persist it through the audited state-change path
(Inv 6).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

import httpx

from core.clock import default_clock
from core.ids import new_id
from ingest.market_data import MarketDataError, parse_exchange_time
from quant.events import to_ns
from schemas.models.portfolio_snapshot import PortfolioSnapshot

EVENT_PORTFOLIO_SNAPSHOT = "portfolio_snapshot"
PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"
_ORDER_LOOKUP_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class AlpacaPaperError(RuntimeError):
    """A sanitized Alpaca paper-API failure. Credentials and response bodies stay private."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 request_id: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


def paper_fill_price(side: str, quote: dict) -> Decimal:
    """Conservative deterministic fill for hermetic paper/replay tests.

    Real Alpaca paper orders consume Alpaca's reported fills. Synthetic fills cross the
    spread: buys pay ask and sells receive bid. Midpoint is never a fill authority.
    """
    clean_side = str(side).strip().lower()
    field = "ask" if clean_side == "buy" else "bid" if clean_side == "sell" else None
    if field is None:
        raise AlpacaPaperError("paper fill side must be buy or sell")
    price = Decimal(str((quote or {}).get(field) or "0"))
    if not price.is_finite() or price <= 0:
        raise AlpacaPaperError(f"paper fill requires a positive {field}")
    return price


def _decimal_text(value: Any) -> str:
    """JSON-safe decimal text without float conversion."""
    try:
        return str(value if isinstance(value, Decimal) else Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise AlpacaPaperError("invalid numeric value in paper order") from exc


def _clean_order_lookup_id(value: Any, *, field: str) -> str:
    """Allow only the identifier alphabet emitted by Alpaca and this cockpit.

    Broker ids enter a URL path and client ids enter a query parameter.  Keeping both to the
    same deliberately small alphabet prevents path/query confusion and makes the response
    identity checks below exact rather than best-effort.
    """
    clean = str(value or "").strip()
    if _ORDER_LOOKUP_ID.fullmatch(clean) is None:
        raise AlpacaPaperError(f"invalid {field} for paper-order lookup")
    return clean


def _normalize_order_body(body: Any) -> dict:
    """Reduce an untrusted Alpaca order object to the gateway's single ACK contract."""
    if not isinstance(body, dict):
        raise AlpacaPaperError("Alpaca paper order response was not an object")
    broker_order_id = body.get("id")
    status = body.get("status")
    if not broker_order_id or not status:
        raise AlpacaPaperError("Alpaca paper order response omitted id or status")
    clean_broker_order_id = _clean_order_lookup_id(
        broker_order_id, field="broker_order_id"
    )
    try:
        filled_qty = Decimal(str(body.get("filled_qty") or "0"))
        avg_fill_price = (
            None
            if body.get("filled_avg_price") is None
            else Decimal(str(body["filled_avg_price"]))
        )
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise AlpacaPaperError("Alpaca paper order response had invalid fill data") from exc
    if not filled_qty.is_finite() or filled_qty < 0 or (
        avg_fill_price is not None and not avg_fill_price.is_finite()
    ):
        raise AlpacaPaperError("Alpaca paper order response had invalid fill data")
    if avg_fill_price is not None and avg_fill_price <= 0:
        raise AlpacaPaperError("Alpaca paper order response had non-positive fill price")
    return {
        "broker_order_id": clean_broker_order_id,
        "status": str(status),
        "filled_qty": filled_qty,
        "avg_fill_price": avg_fill_price,
    }


class AlpacaPaperHTTPClient:
    """Minimal synchronous client for Alpaca's fixed PAPER trading endpoint.

    The base URL is intentionally not configurable: accepting an environment-provided URL here
    could silently redirect a paper-mode process to Alpaca live trading or to an attacker host.
    ``http_client`` is injectable only for hermetic tests; production constructs the default
    ``httpx.Client`` and sends credentials solely in request headers.
    """

    def __init__(self, key_id: str, secret_key: str, *, http_client=None,
                 timeout_seconds: float = 10.0, clock=None) -> None:
        if not str(key_id).strip() or not str(secret_key).strip():
            raise AlpacaPaperError("both Alpaca paper credentials are required")
        self._headers = {
            "APCA-API-KEY-ID": str(key_id).strip(),
            "APCA-API-SECRET-KEY": str(secret_key).strip(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return "AlpacaPaperHTTPClient(base_url='https://paper-api.alpaca.markets')"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, base_url: str, path: str, *, json=None, params=None):
        try:
            response = self._client.request(
                method,
                f"{base_url}{path}",
                headers=self._headers,
                json=json,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise AlpacaPaperError("Alpaca paper API network failure") from exc
        request_id = response.headers.get("X-Request-ID")
        if response.status_code >= 400:
            raise AlpacaPaperError(
                f"Alpaca paper API rejected the request (HTTP {response.status_code})",
                status_code=response.status_code,
                request_id=request_id,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise AlpacaPaperError(
                "Alpaca paper API returned invalid JSON",
                status_code=response.status_code,
                request_id=request_id,
            ) from exc

    def submit_order(self, canonical: dict) -> dict:
        payload = {
            "symbol": canonical["symbol"],
            "qty": _decimal_text(canonical["qty"]),
            "side": canonical["side"],
            "type": canonical["order_type"],
            "time_in_force": canonical["tif"],
        }
        if canonical.get("limit_price") is not None:
            payload["limit_price"] = _decimal_text(canonical["limit_price"])
        # Generated by OrderGateway from the durable local order id. Alpaca enforces uniqueness,
        # providing a broker-side replay backstop without changing the approved tuple hash.
        if canonical.get("client_order_id"):
            payload["client_order_id"] = str(canonical["client_order_id"])[:128]
        body = self._request("POST", PAPER_BASE_URL, "/v2/orders", json=payload)
        return _normalize_order_body(body)

    def order_info(self, *, broker_order_id: str | None = None,
                   client_order_id: str | None = None) -> dict:
        """Read one paper order, identity-bound to exactly one lookup key.

        The broker-id path is used for ordinary submitted/resting orders.  The client-id path
        is the lost-response seam: ``OrderGateway`` deterministically sends
        ``cockpit-{local_order_id}``, so a timeout after Alpaca accepted an order remains
        discoverable without submitting it again.
        """
        if (broker_order_id is None) == (client_order_id is None):
            raise AlpacaPaperError("exactly one paper-order lookup id is required")
        if broker_order_id is not None:
            lookup = _clean_order_lookup_id(broker_order_id, field="broker_order_id")
            body = self._request("GET", PAPER_BASE_URL, f"/v2/orders/{lookup}")
            if not isinstance(body, dict) or str(body.get("id") or "") != lookup:
                raise AlpacaPaperError("Alpaca paper order response did not match broker id")
        else:
            lookup = _clean_order_lookup_id(client_order_id, field="client_order_id")
            body = self._request(
                "GET",
                PAPER_BASE_URL,
                "/v2/orders:by_client_order_id",
                params={"client_order_id": lookup},
            )
            if not isinstance(body, dict) or str(body.get("client_order_id") or "") != lookup:
                raise AlpacaPaperError("Alpaca paper order response did not match client id")
        return _normalize_order_body(body)

    def account_info(self) -> dict:
        body = self._request("GET", PAPER_BASE_URL, "/v2/account")
        return {
            # Do not persist/log Alpaca's account number or account UUID.
            "account": "alpaca-paper",
            "equity": body.get("equity"),
            "cash": body.get("cash", "0"),
            "buying_power": body.get("buying_power", "0"),
            "exposure": body.get("long_market_value", "0"),
            "holdings": [],
            "open_orders": [],
        }

    def positions_info(self) -> list:
        rows = self._request("GET", PAPER_BASE_URL, "/v2/positions")
        if not isinstance(rows, list):
            raise AlpacaPaperError("Alpaca paper positions response was not a list")
        return [
            {
                "symbol": str(row.get("symbol", "")).upper(),
                "qty": row.get("qty", "0"),
                "market_value": row.get("market_value", "0"),
                "avg_entry_price": row.get("avg_entry_price"),
                "side": row.get("side"),
            }
            for row in rows
            if row.get("symbol")
        ]

    def latest_quote(self, symbol: str) -> dict:
        clean = str(symbol).strip().upper()
        if not clean or not clean.replace(".", "").replace("-", "").isalnum():
            raise AlpacaPaperError("invalid equity symbol for quote request")
        body = self._request(
            "GET", DATA_BASE_URL, f"/v2/stocks/{clean}/quotes/latest", params={"feed": "iex"}
        )
        quote = body.get("quote") or {}
        bid = Decimal(str(quote.get("bp") or "0"))
        ask = Decimal(str(quote.get("ap") or "0"))
        positive = [p for p in (bid, ask) if p > 0]
        if not positive:
            raise AlpacaPaperError("Alpaca latest quote had no positive bid or ask")
        price = sum(positive, Decimal("0")) / Decimal(len(positive))
        spread_pct = Decimal("0")
        if bid > 0 and ask > 0 and ask >= bid:
            spread_pct = ((ask - bid) / price) * Decimal("100")
        bid_size = Decimal(str(quote.get("bs") or "0"))
        ask_size = Decimal(str(quote.get("as") or "0"))
        ts = quote.get("t")
        age_s = float("inf")
        ts_ns = None
        if ts:
            try:
                parsed = parse_exchange_time(ts)
                now = self._clock()
                if now.tzinfo is None or now.utcoffset() is None or parsed > now:
                    raise MarketDataError("quote timestamp is not current")
                age_s = (now.astimezone(timezone.utc) - parsed).total_seconds()
                ts_ns = to_ns(parsed)
            except (MarketDataError, TypeError, ValueError):
                pass
        return {
            "price": price,
            "age_s": age_s,
            "spread_pct": spread_pct,
            "ts": ts,
            "ts_ns": ts_ns,
            "bid": bid,
            "ask": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "feed": "iex",
        }

    def daily_bars(self, symbol: str, *, limit: int = 60) -> list[dict]:
        """Fetch recent IEX daily bars. Entitlement/coverage remains RW-14's online gate."""
        clean = str(symbol).strip().upper()
        if not clean or not clean.replace(".", "").replace("-", "").isalnum():
            raise AlpacaPaperError("invalid equity symbol for bars request")
        if not 3 <= int(limit) <= 1000:
            raise AlpacaPaperError("daily bar limit must be between 3 and 1000")
        body = self._request(
            "GET",
            DATA_BASE_URL,
            "/v2/stocks/bars",
            params={
                "symbols": clean,
                "timeframe": "1Day",
                "feed": "iex",
                "limit": int(limit),
                "sort": "desc",
            },
        )
        bars = body.get("bars") if isinstance(body, dict) else None
        rows = bars.get(clean) if isinstance(bars, dict) else None
        if not isinstance(rows, list):
            raise AlpacaPaperError("Alpaca daily-bars response omitted the symbol list")
        return rows


class FakeAlpaca:
    """Deterministic in-process stand-in for the Alpaca paper REST API. No network.

    Orders are ACKNOWLEDGED (``accepted``, unfilled) rather than instantly filled, so the
    order-lifecycle FSM (submitted -> partially_filled/filled) is exercised by explicit
    downstream fill events rather than hidden inside the adapter. Account/positions return a
    fixed, seedable snapshot."""

    def __init__(self, account: str = "paper", cash="100000.00", buying_power="200000.00",
                 equity=None, holdings=None, positions=None) -> None:
        self.account = account
        self.cash = cash
        self.buying_power = buying_power
        self.equity = cash if equity is None else equity
        self.holdings = holdings if holdings is not None else []
        self.positions = positions if positions is not None else []
        self.submitted: list[dict] = []
        self._orders_by_broker: dict[str, dict] = {}
        self._orders_by_client: dict[str, dict] = {}

    def submit_order(self, canonical: dict) -> dict:
        oid = new_id("alp")
        response = {
            "broker_order_id": oid,
            "status": "accepted",
            "filled_qty": Decimal("0"),
            "avg_fill_price": None,
        }
        record = {**response, **canonical}
        self.submitted.append(record)
        self._orders_by_broker[oid] = record
        client_order_id = canonical.get("client_order_id")
        if client_order_id:
            self._orders_by_client[str(client_order_id)] = record
        return dict(response)

    def order_info(self, *, broker_order_id: str | None = None,
                   client_order_id: str | None = None) -> dict:
        if (broker_order_id is None) == (client_order_id is None):
            raise AlpacaPaperError("exactly one paper-order lookup id is required")
        record = (
            self._orders_by_broker.get(str(broker_order_id))
            if broker_order_id is not None
            else self._orders_by_client.get(str(client_order_id))
        )
        if record is None:
            raise AlpacaPaperError("paper order was not found", status_code=404)
        return {
            key: record.get(key)
            for key in ("broker_order_id", "status", "filled_qty", "avg_fill_price")
        }

    def set_order_status(self, broker_order_id: str, *, status: str, filled_qty="0",
                         avg_fill_price=None) -> None:
        """Deterministic test seam; it performs no clock or hidden fill simulation."""
        record = self._orders_by_broker[str(broker_order_id)]
        record.update(
            status=str(status),
            filled_qty=Decimal(str(filled_qty)),
            avg_fill_price=(
                None if avg_fill_price is None else Decimal(str(avg_fill_price))
            ),
        )

    def fill_order_at_quote(self, broker_order_id: str, quote: dict, *, filled_qty=None) -> None:
        """Hermetic fill seam using bid/ask, never midpoint."""
        record = self._orders_by_broker[str(broker_order_id)]
        qty = record.get("qty") if filled_qty is None else filled_qty
        price = paper_fill_price(str(record.get("side")), quote)
        status = "filled" if Decimal(str(qty)) == Decimal(str(record.get("qty"))) \
            else "partially_filled"
        self.set_order_status(
            broker_order_id, status=status, filled_qty=qty, avg_fill_price=price
        )

    def account_info(self) -> dict:
        return {"account": self.account, "equity": self.equity, "cash": self.cash,
                "buying_power": self.buying_power, "holdings": list(self.holdings)}

    def positions_info(self) -> list:
        return list(self.positions)


class AlpacaPaperBroker:
    def __init__(self, client=None) -> None:
        # Injectable seam; default a no-network simulator (SEC-03: a private, in-process seam).
        self._client = client if client is not None else FakeAlpaca()

    def submit_order(self, canonical_tuple: dict) -> dict:
        """Submit the canonical resolved-order tuple. Returns
        ``{broker_order_id, status, filled_qty, avg_fill_price}``."""
        return self._client.submit_order(canonical_tuple)

    def find_order(self, *, broker_order_id: str | None,
                   client_order_id: str) -> dict | None:
        """Read by durable broker id, falling back to the gateway's deterministic client id."""
        if broker_order_id:
            try:
                return self._client.order_info(broker_order_id=broker_order_id)
            except AlpacaPaperError as exc:
                if exc.status_code != 404:
                    raise
        try:
            return self._client.order_info(client_order_id=client_order_id)
        except AlpacaPaperError as exc:
            if exc.status_code == 404:
                return None
            raise

    def _snapshot(self, *, clock) -> PortfolioSnapshot:
        info = self._client.account_info()
        now = (clock or default_clock).now()
        return PortfolioSnapshot(
            id=new_id("snap"),
            account=info.get("account", "paper"),
            equity=info.get("equity"),
            cash=info.get("cash", "0"),
            buying_power=info.get("buying_power", "0"),
            exposure=info.get("exposure"),
            holdings=info.get("holdings", []),
            open_orders=info.get("open_orders", []),
            ts=now,
            created_at=now,
        )

    def get_account(self, *, st=None, clock=None) -> PortfolioSnapshot:
        """Fetch the account snapshot. When ``st`` is supplied, persist it through the audited
        state-change path (Inv 6); otherwise return an unpersisted row."""
        snap = self._snapshot(clock=clock)
        if st is not None:
            st.audit.record_state_change(
                snap, EVENT_PORTFOLIO_SNAPSHOT, actor="alpaca_paper",
                payload={"account": snap.account,
                         "equity": None if snap.equity is None else str(snap.equity)},
            )
        return snap

    def get_positions(self, *, st=None, clock=None) -> PortfolioSnapshot:
        """Fetch positions as a snapshot (holdings populated from the client). Persisted
        through the audited path when ``st`` is supplied."""
        snap = self._snapshot(clock=clock)
        snap.holdings = self._client.positions_info()
        if st is not None:
            st.audit.record_state_change(
                snap, EVENT_PORTFOLIO_SNAPSHOT, actor="alpaca_paper",
                payload={"account": snap.account, "kind": "positions",
                         "equity": None if snap.equity is None else str(snap.equity)},
            )
        return snap
