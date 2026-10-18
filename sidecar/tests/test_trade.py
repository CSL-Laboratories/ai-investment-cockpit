from __future__ import annotations

import json
import sys
import types
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from sidecar.chainlog import ChainError, ChainLog
from sidecar.harness.alpaca import PaperReadClient
from sidecar.harness.paper_credentials import (
    SIDECAR_KEYRING_SERVICE,
    SidecarCredentialsMissing,
    load_sidecar_paper_credentials,
)
from sidecar.harness.runner import RunnerError, run_runner, session_phase
from sidecar.harness.trade import (
    FORBIDDEN_TRADING_HOST,
    PAPER_BASE_URL,
    LimitBreach,
    OrderIntent,
    PaperLimits,
    PaperTradeClient,
    PaperTradeError,
    assert_paper_account,
    assert_trade_url,
    check_limits,
    make_client_order_id,
)

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]


def test_sidecar_credentials_never_read_the_cockpit_pair(monkeypatch):
    source = (ROOT / "sidecar" / "harness" / "paper_credentials.py").read_text(encoding="utf-8")
    runner = (ROOT / "sidecar" / "harness" / "runner.py").read_text(encoding="utf-8")
    assert "alpaca_key" not in source
    assert "alpaca_secret" not in source
    assert "SIDECAR_ALPACA_KEY_ID" not in source
    assert "SIDECAR_ALPACA_SECRET" not in source
    assert "load_alpaca_credentials" not in runner
    assert SIDECAR_KEYRING_SERVICE != "ai-investment-cockpit"
    monkeypatch.setenv("SIDECAR_ALPACA_KEY_ID", "cockpit-key")
    monkeypatch.setenv("SIDECAR_ALPACA_SECRET", "cockpit-secret")
    monkeypatch.delenv("SIDECAR_PAPER_ALPACA_KEY_ID", raising=False)
    monkeypatch.delenv("SIDECAR_PAPER_ALPACA_SECRET", raising=False)
    calls = []
    fake = types.ModuleType("keyring")

    def get_password(service, name):
        calls.append((service, name))
        if service == "ai-investment-cockpit":
            return "cockpit-should-not-be-used"
        return None

    fake.get_password = get_password
    monkeypatch.setitem(sys.modules, "keyring", fake)
    with pytest.raises(SidecarCredentialsMissing) as caught:
        load_sidecar_paper_credentials()
    assert "cockpit-key" not in str(caught.value)
    assert calls
    assert {service for service, _name in calls} == {SIDECAR_KEYRING_SERVICE}


def test_env_pair_is_used_only_when_the_sidecar_keyring_is_empty(monkeypatch):
    fake = types.ModuleType("keyring")
    fake.get_password = lambda service, name: None
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setenv("SIDECAR_PAPER_ALPACA_KEY_ID", "paper-key")
    monkeypatch.setenv("SIDECAR_PAPER_ALPACA_SECRET", "paper-secret")
    assert load_sidecar_paper_credentials() == ("paper-key", "paper-secret")


def test_trade_url_rejects_anything_but_the_paper_host():
    with pytest.raises(PaperTradeError):
        assert_trade_url(FORBIDDEN_TRADING_HOST + "/v2/orders", "POST")
    with pytest.raises(PaperTradeError):
        assert_trade_url("https://paper-api.alpaca.markets.evil/v2/orders", "POST")
    with pytest.raises(PaperTradeError):
        assert_trade_url(PAPER_BASE_URL + "/v2/account", "POST")
    assert_trade_url(PAPER_BASE_URL + "/v2/orders", "POST")
    assert_trade_url(PAPER_BASE_URL + "/v2/account", "GET")


def test_account_must_be_active_paper_before_any_order():
    url = PAPER_BASE_URL + "/v2/account"
    with pytest.raises(PaperTradeError):
        assert_paper_account({"status": "ACTIVE", "equity": "1", "account_type": "live"}, requested_url=url)
    with pytest.raises(PaperTradeError):
        assert_paper_account({"status": "ACTIVE", "equity": "1", "trading_blocked": True}, requested_url=url)
    with pytest.raises(PaperTradeError):
        assert_paper_account({"status": "ACCOUNT_CLOSED", "equity": "1"}, requested_url=url)
    assert_paper_account({"status": "ACTIVE", "equity": "1"}, requested_url=url)


def test_limits_kill_switch_and_client_order_id(tmp_path: Path):
    kill = tmp_path / "KILL"
    intent = OrderIntent("SPY", "buy", Decimal("100.00"), None, "scid", "strategy")
    positions = [{"symbol": "SPY", "qty": "1", "market_value": "100"}]
    check_limits(
        intent,
        equity=Decimal("10000"),
        last_equity=Decimal("10000"),
        positions=positions,
        limits=PaperLimits(),
        kill_file=kill,
    )
    kill.write_text("stop", encoding="utf-8")
    with pytest.raises(LimitBreach) as killed:
        check_limits(
            intent,
            equity=Decimal("10000"),
            last_equity=None,
            positions=positions,
            limits=PaperLimits(),
            kill_file=kill,
        )
    assert killed.value.reason == "kill_switch"
    fat = OrderIntent("SPY", "buy", Decimal("5000"), None, "scid", "strategy")
    with pytest.raises(LimitBreach) as breach:
        check_limits(
            fat,
            equity=Decimal("10000"),
            last_equity=None,
            positions=[],
            limits=PaperLimits(max_notional_per_order=Decimal("2000")),
            kill_file=tmp_path / "absent",
        )
    assert breach.value.reason == "max_notional_per_order"
    with pytest.raises(LimitBreach) as stopped:
        check_limits(
            intent,
            equity=Decimal("9000"),
            last_equity=Decimal("10000"),
            positions=[],
            limits=PaperLimits(max_daily_loss=Decimal("500")),
            kill_file=tmp_path / "absent",
        )
    assert stopped.value.reason == "daily_loss_stop"
    first = make_client_order_id(day="2026-10-14", symbol="SPY", side="buy", notional=Decimal("10.009"))
    second = make_client_order_id(day="2026-10-14", symbol="SPY", side="buy", notional=Decimal("10.001"))
    assert first == second
    assert first.isalnum() and len(first) <= 48


def _client(handler) -> PaperTradeClient:
    return PaperTradeClient("key-id", "secret-value", transport=httpx.MockTransport(handler))


def test_place_order_is_idempotent_and_hides_the_secret():
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "paper-api.alpaca.markets"
        assert "secret-value" not in str(request.url)
        if request.url.path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "equity": "10000"})
        if request.url.path == "/v2/orders:by_client_order_id":
            if posted:
                return httpx.Response(200, json={"id": "ord-1", "client_order_id": posted[0]["client_order_id"]})
            return httpx.Response(404, json={"message": "missing"})
        if request.method == "POST" and request.url.path == "/v2/orders":
            body = json.loads(request.content.decode())
            posted.append(body)
            return httpx.Response(200, json={"id": "ord-1", "client_order_id": body["client_order_id"]})
        raise AssertionError(request.method + " " + request.url.path)

    client = _client(handler)
    assert "secret-value" not in repr(client)
    intent = OrderIntent("SPY", "buy", Decimal("25.00"), None, "sc20261014SPYB" + "a" * 8, "strategy")
    first = client.place_order(intent)
    second = client.place_order(intent)
    assert first["result"] == "submitted"
    assert second["result"] == "already_present"
    assert len(posted) == 1
    client.close()


def test_dry_run_does_not_construct_a_trade_client(tmp_path: Path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("dry-run constructed a trade client")

    monkeypatch.setattr(PaperTradeClient, "__init__", explode)
    def credentials_must_not_load():
        raise AssertionError("dry-run loaded credentials")

    monkeypatch.setattr(
        "sidecar.harness.runner.load_sidecar_paper_credentials",
        credentials_must_not_load,
    )
    now = datetime(2026, 10, 14, 12, 0, tzinfo=ET)
    report = run_runner(
        out=tmp_path / "report.json",
        ledger_path=tmp_path / "ledger.jsonl",
        order_log_path=tmp_path / "orders.jsonl",
        kill_file=tmp_path / "absent",
        when="open",
        execute=False,
        now=now,
        bootstrap_reps=60,
        refresh=False,
    )
    assert report["mode"] == "sidecar_paper_dry_run"
    assert report["orders_submitted"] == 0
    assert report["broker_writes"] is False
    assert report["experimental_order_path"] is True
    assert report["evalstats"]["threshold_status"] == "not_frozen"
    assert report["trial_ledger"]["distinct_configs"] >= 6
    log = ChainLog(tmp_path / "orders.jsonl")
    rows = log.entries()
    assert rows
    text = (tmp_path / "orders.jsonl").read_text(encoding="utf-8")
    (tmp_path / "orders.jsonl").write_text(text.replace(rows[-1]["entry_hash"], "0" * 64, 1), encoding="utf-8")
    with pytest.raises(ChainError):
        log.entries()


def test_execute_posts_once_and_the_kill_switch_cancels_instead(tmp_path: Path):
    posted = []
    deleted = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "paper-api.alpaca.markets"
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "equity": "100000", "trading_blocked": False})
        if path == "/v2/positions":
            return httpx.Response(200, json=[])
        if path == "/v2/account/portfolio/history":
            return httpx.Response(200, json={"timestamp": [], "equity": []})
        if path == "/v2/orders:by_client_order_id":
            client_id = request.url.params.get("client_order_id")
            match = next((row for row in posted if row["client_order_id"] == client_id), None)
            if match is None:
                return httpx.Response(404, json={"message": "missing"})
            return httpx.Response(200, json={"id": "ord", "client_order_id": client_id})
        if path == "/v2/orders" and request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "open-1", "client_order_id": "sc-open", "symbol": "SPY"}],
            )
        if path == "/v2/orders" and request.method == "POST":
            body = json.loads(request.content.decode())
            posted.append(body)
            return httpx.Response(200, json={"id": "ord", "client_order_id": body["client_order_id"]})
        if request.method == "DELETE":
            deleted.append(path)
            return httpx.Response(204)
        raise AssertionError(request.method + " " + path)

    now = datetime(2026, 10, 14, 15, 45, tzinfo=ET)
    client = _client(handler)
    report = run_runner(
        out=tmp_path / "report.json",
        ledger_path=tmp_path / "ledger.jsonl",
        order_log_path=tmp_path / "orders.jsonl",
        kill_file=tmp_path / "absent",
        when="close",
        execute=True,
        now=now,
        client=client,
        bootstrap_reps=60,
        refresh=False,
    )
    assert report["mode"] == "sidecar_paper_execute"
    assert report["orders_submitted"] == len(posted) > 0
    assert report["broker_writes"] is True
    again = run_runner(
        out=tmp_path / "report2.json",
        ledger_path=tmp_path / "ledger.jsonl",
        order_log_path=tmp_path / "orders2.jsonl",
        kill_file=tmp_path / "absent",
        when="close",
        execute=True,
        now=now,
        client=client,
        bootstrap_reps=60,
        refresh=False,
    )
    assert again["orders_submitted"] == 0
    assert len(posted) == report["orders_submitted"]
    kill = tmp_path / "KILL"
    kill.write_text("stop", encoding="utf-8")
    stopped = run_runner(
        out=tmp_path / "killed.json",
        ledger_path=tmp_path / "ledger.jsonl",
        order_log_path=tmp_path / "killed-orders.jsonl",
        kill_file=kill,
        when="close",
        execute=True,
        now=now,
        client=client,
        bootstrap_reps=60,
        refresh=False,
    )
    assert stopped["orders_submitted"] == 0
    assert stopped["kill_switch"] is True
    assert deleted
    client.close()


def _weekday_bars_ending(end: date, count: int) -> list[dict]:
    days = []
    day = end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    rows = []
    for index, session in enumerate(reversed(days)):
        price = 100 + index
        stamp = datetime(session.year, session.month, session.day, 4, 0, tzinfo=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows.append({"t": stamp, "o": price, "h": price + 1, "l": price - 0.5, "c": price + 0.25, "v": 1000 + index})
    return rows


def _bars_client(rows: list[dict]) -> PaperReadClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "data.alpaca.markets"
        assert request.url.path == "/v2/stocks/bars"
        assert request.url.params["adjustment"] == "all"
        symbols = request.url.params["symbols"].split(",")
        return httpx.Response(200, json={"bars": {symbol: rows for symbol in symbols}, "next_page_token": None})

    return PaperReadClient("key-id", "secret-value", transport=httpx.MockTransport(handler))


def test_runner_sizes_from_refreshed_bars_and_leaves_the_fixture(tmp_path: Path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("refresh constructed a trade client")

    monkeypatch.setattr(PaperTradeClient, "__init__", explode)
    rows = _weekday_bars_ending(date(2026, 10, 13), 40)
    client = _bars_client(rows)
    now = datetime(2026, 10, 14, 12, 0, tzinfo=ET)
    report = run_runner(
        out=tmp_path / "report.json",
        ledger_path=tmp_path / "ledger.jsonl",
        order_log_path=tmp_path / "orders.jsonl",
        kill_file=tmp_path / "absent",
        when="open",
        execute=False,
        now=now,
        bootstrap_reps=60,
        refresh=True,
        bars_client=client,
        bars_out=tmp_path / "daily_bars.csv",
    )
    client.close()
    assert report["orders_submitted"] == 0
    assert report["broker_writes"] is False
    assert report["primary_config"]["bars_source"] == "alpaca_iex_adjusted"
    text = (tmp_path / "daily_bars.csv").read_text(encoding="utf-8")
    assert text.startswith("date,symbol,open,high,low,close,adj_close,volume\n")
    assert "2026-10-13,SPY," in text or "2026-10-13,QQQ," in text
    assert "2021-10-18" not in text
    fixture = (ROOT / "fixtures" / "etf_daily.csv").read_text(encoding="utf-8")
    assert fixture.startswith("date,symbol,open,high,low,close,adj_close,volume\n")
    assert "2021-10-18,SPY," in fixture


def test_execute_refuses_orders_when_the_bar_refresh_fails(tmp_path: Path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("failed refresh constructed a trade client")

    monkeypatch.setattr(PaperTradeClient, "__init__", explode)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(503, json={"message": "down"})

    client = PaperReadClient("key-id", "secret-value", transport=httpx.MockTransport(handler))
    now = datetime(2026, 10, 14, 15, 45, tzinfo=ET)
    with pytest.raises(RunnerError, match="refusing to submit orders"):
        run_runner(
            out=tmp_path / "report.json",
            ledger_path=tmp_path / "ledger.jsonl",
            order_log_path=tmp_path / "orders.jsonl",
            kill_file=tmp_path / "absent",
            when="close",
            execute=True,
            now=now,
            bootstrap_reps=60,
            refresh=True,
            bars_client=client,
            bars_out=tmp_path / "daily_bars.csv",
        )
    client.close()
    assert not (tmp_path / "report.json").exists()


def test_dry_run_uses_the_local_file_when_refresh_credentials_are_missing(tmp_path: Path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("dry-run constructed a trade client")

    monkeypatch.setattr(PaperTradeClient, "__init__", explode)

    def missing():
        raise SidecarCredentialsMissing("missing sidecar paper credentials")

    monkeypatch.setattr("sidecar.harness.bars.load_sidecar_paper_credentials", missing)
    now = datetime(2026, 10, 14, 12, 0, tzinfo=ET)
    report = run_runner(
        out=tmp_path / "report.json",
        ledger_path=tmp_path / "ledger.jsonl",
        order_log_path=tmp_path / "orders.jsonl",
        kill_file=tmp_path / "absent",
        when="open",
        execute=False,
        now=now,
        bootstrap_reps=60,
        refresh=True,
    )
    assert report["orders_submitted"] == 0
    assert report["broker_writes"] is False
    assert report["primary_config"]["bars_source"] == "local_file"
    assert any("were not refreshed" in line for line in report["caveats"])


def test_execute_outside_the_window_is_refused(tmp_path: Path):
    now = datetime(2026, 10, 14, 12, 0, tzinfo=ET)
    assert session_phase(now) == "outside"
    with pytest.raises(RunnerError):
        run_runner(
            out=tmp_path / "report.json",
            ledger_path=tmp_path / "ledger.jsonl",
            order_log_path=tmp_path / "orders.jsonl",
            kill_file=tmp_path / "absent",
            when="auto",
            execute=True,
            now=now,
            bootstrap_reps=60,
        )
