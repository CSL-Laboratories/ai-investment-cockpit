"""WP7 unit tests — the Alpaca paper broker adapter (injectable seam, no network)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os

import httpx
import pytest
from sqlalchemy import select

from brokers.alpaca_paper import (
    AlpacaPaperBroker,
    AlpacaPaperError,
    AlpacaPaperHTTPClient,
    FakeAlpaca,
)
from brokers.order_tuple import canonical_order_tuple
from config.settings import Settings
from core.clock import FrozenClock
from orchestrator.state import OrchestratorState
from schemas.models.audit_entry import AuditEntry
from schemas.models.portfolio_snapshot import PortfolioSnapshot
from storage.engine import create_all, make_engine, make_session_factory

CLK = FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'alp.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=b"alp", settings=Settings(), clock=CLK)


def test_submit_order_returns_ack_shape():
    broker = AlpacaPaperBroker()   # default in-process FakeAlpaca; no network
    canon = canonical_order_tuple(symbol="AAPL", side="buy", qty="3", order_type="market",
                                  tif="day")
    res = broker.submit_order(canon)
    assert set(res) == {"broker_order_id", "status", "filled_qty", "avg_fill_price"}
    assert res["broker_order_id"] and res["status"] == "accepted"


def test_client_is_injectable():
    fake = FakeAlpaca(account="paper", cash="42.00")
    broker = AlpacaPaperBroker(client=fake)
    broker.submit_order(canonical_order_tuple(symbol="MSFT", side="buy", qty="1",
                                              order_type="market", tif="day"))
    assert len(fake.submitted) == 1 and fake.submitted[0]["symbol"] == "MSFT"


def test_get_account_persists_snapshot_when_st_given(st):
    broker = AlpacaPaperBroker(client=FakeAlpaca(cash="123.45", buying_power="200.00"))
    snap = broker.get_account(st=st, clock=CLK)
    with st.session_factory() as s:
        row = s.get(PortfolioSnapshot, snap.id)
        assert row is not None and Decimal(str(row.cash)) == Decimal("123.45")
        # covered by an audited state change (Inv 6); the chain verifies.
        entries = s.execute(select(AuditEntry)).scalars().all()
    assert any((e.refs or {}).get("row_id") == snap.id for e in entries)
    assert st.audit.verify_chain().ok


def test_get_account_without_st_does_not_persist(st):
    broker = AlpacaPaperBroker(client=FakeAlpaca())
    snap = broker.get_account(clock=CLK)   # no st -> unpersisted
    with st.session_factory() as s:
        assert s.get(PortfolioSnapshot, snap.id) is None


def test_get_positions_populates_holdings(st):
    fake = FakeAlpaca(positions=[{"symbol": "AAPL", "qty": "5"}])
    broker = AlpacaPaperBroker(client=fake)
    snap = broker.get_positions(st=st, clock=CLK)
    assert snap.holdings == [{"symbol": "AAPL", "qty": "5"}]


def _http_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _credentials():
    # Generated at runtime so no credential-shaped literal enters the source tree.
    return os.urandom(8).hex(), os.urandom(16).hex()


def test_http_client_posts_only_to_paper_endpoint_and_normalizes_ack():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(
            200,
            json={"id": "paper-order-1", "status": "accepted", "filled_qty": "0",
                  "filled_avg_price": None},
        )

    key_id, secret_key = _credentials()
    client = AlpacaPaperHTTPClient(
        key_id, secret_key, http_client=_http_client(handler), clock=CLK.now
    )
    result = client.submit_order({
        "symbol": "AAPL", "side": "buy", "qty": "1", "order_type": "limit",
        "limit_price": "150.25", "tif": "day", "client_order_id": "cockpit-ord-1",
    })

    request = seen["request"]
    assert str(request.url) == "https://paper-api.alpaca.markets/v2/orders"
    assert request.headers["APCA-API-KEY-ID"] == key_id
    assert request.headers["APCA-API-SECRET-KEY"] == secret_key
    assert request.read()
    assert result == {
        "broker_order_id": "paper-order-1", "status": "accepted",
        "filled_qty": Decimal("0"), "avg_fill_price": None,
    }
    assert key_id not in repr(client) and secret_key not in repr(client)


def test_http_client_account_positions_and_quote_are_sanitized():
    now = CLK.now()

    def handler(request):
        if request.url.path == "/v2/account":
            return httpx.Response(200, json={
                "id": "private-account-id", "account_number": "private-number",
                "cash": "100000", "buying_power": "200000", "long_market_value": "500",
            })
        if request.url.path == "/v2/positions":
            return httpx.Response(200, json=[{
                "symbol": "aapl", "qty": "2", "market_value": "500",
                "avg_entry_price": "200", "side": "long",
            }])
        if request.url.path == "/v2/stocks/AAPL/quotes/latest":
            return httpx.Response(200, json={
                "quote": {"bp": "199", "ap": "201", "bs": 10, "as": 12,
                          "t": now.isoformat()}
            })
        if request.url.path == "/v2/stocks/bars":
            return httpx.Response(200, json={"bars": {"AAPL": [
                {"t": "2026-07-01T04:00:00Z", "c": 100, "v": 1000},
            ]}})
        return httpx.Response(404)

    key_id, secret_key = _credentials()
    client = AlpacaPaperHTTPClient(
        key_id, secret_key, http_client=_http_client(handler), clock=CLK.now
    )
    account = client.account_info()
    positions = client.positions_info()
    quote = client.latest_quote("aapl")

    assert account["account"] == "alpaca-paper"
    assert "private-account-id" not in str(account) and "private-number" not in str(account)
    assert positions == [{
        "symbol": "AAPL", "qty": "2", "market_value": "500",
        "avg_entry_price": "200", "side": "long",
    }]
    assert quote["price"] == Decimal("200")
    assert quote["spread_pct"] == Decimal("1")
    assert quote["age_s"] == 0
    assert quote["ts_ns"] is not None and quote["bid_size"] == 10
    assert client.daily_bars("AAPL") == [
        {"t": "2026-07-01T04:00:00Z", "c": 100, "v": 1000}
    ]


def test_http_client_order_lookup_is_read_only_and_identity_bound():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/v2/orders/paper-order-1":
            return httpx.Response(200, json={
                "id": "paper-order-1", "client_order_id": "cockpit-ord-1",
                "status": "partially_filled", "filled_qty": "0.5",
                "filled_avg_price": "149.25",
            })
        if request.url.path == "/v2/orders:by_client_order_id":
            return httpx.Response(200, json={
                "id": "paper-order-1", "client_order_id": "cockpit-ord-1",
                "status": "filled", "filled_qty": "1", "filled_avg_price": "149.50",
            })
        return httpx.Response(404)

    key_id, secret_key = _credentials()
    client = AlpacaPaperHTTPClient(
        key_id, secret_key, http_client=_http_client(handler), clock=CLK.now
    )
    by_broker = client.order_info(broker_order_id="paper-order-1")
    by_client = client.order_info(client_order_id="cockpit-ord-1")

    assert by_broker["status"] == "partially_filled"
    assert by_broker["filled_qty"] == Decimal("0.5")
    assert by_client["status"] == "filled"
    assert all(request.method == "GET" for request in seen)
    assert seen[1].url.params["client_order_id"] == "cockpit-ord-1"


def test_http_client_order_lookup_rejects_identity_mismatch():
    def handler(_request):
        return httpx.Response(200, json={
            "id": "different-order", "status": "filled", "filled_qty": "1",
            "filled_avg_price": "10",
        })

    key_id, secret_key = _credentials()
    client = AlpacaPaperHTTPClient(
        key_id, secret_key, http_client=_http_client(handler), clock=CLK.now
    )
    with pytest.raises(AlpacaPaperError, match="did not match broker id"):
        client.order_info(broker_order_id="paper-order-1")


def test_http_client_order_lookup_rejects_negative_fill_data():
    def handler(_request):
        return httpx.Response(200, json={
            "id": "paper-order-1", "status": "canceled", "filled_qty": "-1",
            "filled_avg_price": None,
        })

    key_id, secret_key = _credentials()
    client = AlpacaPaperHTTPClient(
        key_id, secret_key, http_client=_http_client(handler), clock=CLK.now
    )
    with pytest.raises(AlpacaPaperError, match="invalid fill data"):
        client.order_info(broker_order_id="paper-order-1")


def test_http_client_future_or_naive_quote_timestamp_never_reads_fresh():
    stamps = iter(["2026-07-07T14:05:00Z", "2026-07-07T14:00:00"])

    def handler(_request):
        return httpx.Response(200, json={"quote": {
            "bp": "99", "ap": "101", "bs": 10, "as": 10, "t": next(stamps),
        }})

    key_id, secret_key = _credentials()
    client = AlpacaPaperHTTPClient(
        key_id, secret_key, http_client=_http_client(handler), clock=CLK.now
    )
    for _ in range(2):
        quote = client.latest_quote("AAPL")
        assert quote["age_s"] == float("inf") and quote["ts_ns"] is None


def test_http_client_rejects_without_leaking_response_body():
    marker = "do-not-echo-private-body"

    def handler(_request):
        return httpx.Response(403, headers={"X-Request-ID": "req-1"},
                              json={"message": marker})

    key_id, secret_key = _credentials()
    client = AlpacaPaperHTTPClient(key_id, secret_key, http_client=_http_client(handler))
    with pytest.raises(AlpacaPaperError) as exc:
        client.account_info()
    assert exc.value.status_code == 403 and exc.value.request_id == "req-1"
    assert marker not in str(exc.value)
