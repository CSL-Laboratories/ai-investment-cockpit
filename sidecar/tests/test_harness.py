from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from sidecar.harness.alpaca import (
    FORBIDDEN_TRADING_HOST,
    PAPER_BASE_URL,
    PaperEndpointError,
    PaperReadClient,
    assert_read_url,
)
from sidecar.harness.backtest import Bar, _simulate, load_bars, run_backtest
from sidecar.harness.paper import build_paper_report
from sidecar.harness.secrets import CredentialsMissing, load_alpaca_credentials


def test_manifest_matches_the_bar_file():
    root = Path(__file__).resolve().parents[1]
    raw = (root / "fixtures" / "etf_daily.csv").read_bytes()
    note = json.loads((root / "fixtures" / "SOURCE.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(raw).hexdigest() == note["sha256"]
    assert note["not_a_point_in_time_tape"] is True


def test_paper_client_rejects_live_and_writes():
    with pytest.raises(PaperEndpointError):
        assert_read_url(FORBIDDEN_TRADING_HOST + "/v2/orders")
    with pytest.raises(PaperEndpointError):
        assert_read_url("https://paper-api.alpaca.markets.evil.example/v2/account")
    client = PaperReadClient("key-id", "secret-value", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    assert "secret-value" not in repr(client)
    with pytest.raises(PaperEndpointError):
        client._request("POST", PAPER_BASE_URL + "/v2/orders")
    client.close()


def test_missing_credentials_do_not_echo_a_partial_secret(monkeypatch):
    import sys
    import types

    monkeypatch.delenv("SIDECAR_ALPACA_KEY_ID", raising=False)
    monkeypatch.delenv("SIDECAR_ALPACA_SECRET", raising=False)
    monkeypatch.setenv("SIDECAR_ALPACA_KEY_ID", "only-the-key")
    fake = types.ModuleType("keyring")

    def explode(service, name):
        raise RuntimeError("no keyring")

    fake.get_password = explode
    monkeypatch.setitem(sys.modules, "keyring", fake)
    with pytest.raises(CredentialsMissing) as caught:
        load_alpaca_credentials()
    assert "only-the-key" not in str(caught.value)


def test_paper_report_is_read_only_and_omits_the_account_id():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        assert request.method == "GET"
        assert request.url.host in {"paper-api.alpaca.markets", "data.alpaca.markets"}
        if request.url.path == "/v2/account":
            return httpx.Response(
                200,
                json={"id": "ACCT-SECRET", "equity": "100000", "cash": "50000", "status": "ACTIVE"},
            )
        if request.url.path == "/v2/account/portfolio/history":
            stamps = [1_700_000_000 + index * 86400 for index in range(12)]
            equity = [100000 + index * 100 for index in range(12)]
            return httpx.Response(200, json={"timestamp": stamps, "equity": equity})
        if request.url.path == "/v2/orders":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"bars": {}})

    client = PaperReadClient("key-id", "secret-value", transport=httpx.MockTransport(handler))
    report = build_paper_report(client)
    client.close()
    assert report["orders_submitted"] == 0
    assert report["broker_writes"] is False
    assert report["paper_base_url"] == "https://paper-api.alpaca.markets"
    blob = json.dumps(report)
    assert "ACCT-SECRET" not in blob
    assert "secret-value" not in blob
    assert report["evalstats"]["status"] == "ok"
    assert all(method == "GET" for method, _url in seen)


def _series(symbol: str, n: int = 40, drift: float = 0.001) -> list[Bar]:
    rows = []
    price = Decimal("100")
    for index in range(n):
        nxt = price * (Decimal("1") + Decimal(str(drift)) + Decimal(index % 3 - 1) / Decimal(1000))
        rows.append(
            Bar(
                day=date(2024, 1, 1).fromordinal(date(2024, 1, 1).toordinal() + index),
                symbol=symbol,
                open=price,
                high=max(price, nxt),
                low=min(price, nxt),
                close=nxt,
                adj_close=nxt,
                volume=Decimal("1000000"),
            )
        )
        price = nxt
    return rows


def test_simulation_does_not_trade_the_last_close_and_stays_funded():
    grouped = {"AAA": _series("AAA"), "BBB": _series("BBB", drift=0.0005)}
    base = _simulate(
        grouped,
        lookback=5,
        rebalance_every=5,
        starting_equity=Decimal("100000"),
        cost_bps=Decimal("1"),
        target_vol=Decimal("0.01"),
        kelly_frac=Decimal("0.25"),
        collect_fills=True,
    )
    spiked = {
        "AAA": _series("AAA"),
        "BBB": _series("BBB", drift=0.0005),
    }
    spiked["AAA"][-1] = Bar(
        day=spiked["AAA"][-1].day,
        symbol="AAA",
        open=spiked["AAA"][-1].open,
        high=Decimal("500"),
        low=spiked["AAA"][-1].low,
        close=Decimal("500"),
        adj_close=Decimal("500"),
        volume=Decimal("1000000"),
    )
    other = _simulate(
        spiked,
        lookback=5,
        rebalance_every=5,
        starting_equity=Decimal("100000"),
        cost_bps=Decimal("1"),
        target_vol=Decimal("0.01"),
        kelly_frac=Decimal("0.25"),
        collect_fills=False,
    )
    assert [point[1] for point in base["curve"][:-1]] == [point[1] for point in other["curve"][:-1]]
    assert base["curve"][-1][1] != other["curve"][-1][1]
    assert all(point[1] > 0 for point in base["curve"])


def test_offline_backtest_on_the_fixture(tmp_path: Path):
    out = tmp_path / "report.json"
    report = run_backtest(
        out=out,
        ledger_path=tmp_path / "ledger.jsonl",
        bootstrap_reps=60,
        n_slices=8,
    )
    assert report["orders_submitted"] == 0
    assert report["broker_writes"] is False
    assert report["mode"] == "offline_backtest"
    assert report["trial_ledger"]["distinct_configs"] == 4
    assert report["evalstats"]["threshold_status"] == "not_frozen"
    assert report["evalstats"]["n_trials"] == 4
    assert len(report["bars_sha256"]) == 64
    bars = load_bars(Path(__file__).resolve().parents[1] / "fixtures" / "etf_daily.csv")
    assert set(bars) == {"SPY", "QQQ", "IWM"}
    assert out.exists()


def test_package_does_not_import_the_order_path():
    root = Path(__file__).resolve().parents[1] / "sidecar"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "import brokers" not in text
    assert "from app" not in text
    assert "OrderGateway" not in text
    assert "submit_order" not in text
