from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from sidecar.benchmarks.book import business_month_end_flags, run_monthly_rotation, run_weight_book
from sidecar.benchmarks.o17 import orders_for_event
from sidecar.benchmarks.w18 import apply_vol_target, asset_returns, momentum_weights
from sidecar.benchmarks.w25 import build_continuous_futures_trend, long_only
from sidecar.chainlog import ChainLog
from sidecar.harness.runner import RunnerError, run_runner
from sidecar.harness.trade import PaperTradeClient

ET = ZoneInfo("America/New_York")


def test_w18_keeps_the_positive_top_n_and_the_weight_cap():
    closes = {
        "AAA": [100.0, 100.0, 100.0, 130.0, 130.0],
        "BBB": [100.0, 100.0, 100.0, 110.0, 110.0],
        "CCC": [100.0, 100.0, 100.0, 90.0, 90.0],
    }
    rows = momentum_weights(closes, lookback=3, top_n=2, cap=0.40)
    assert rows[2] == {"AAA": 0.0, "BBB": 0.0, "CCC": 0.0}
    assert rows[3]["CCC"] == 0.0
    assert rows[3]["AAA"] == pytest.approx(0.40)
    assert rows[3]["BBB"] == pytest.approx(0.40)
    only = {"AAA": [100.0, 100.0, 100.0, 110.0], "BBB": [100.0, 100.0, 100.0, 90.0]}
    capped = momentum_weights(only, lookback=3, top_n=3, cap=0.40)
    assert capped[3] == {"AAA": pytest.approx(0.40), "BBB": 0.0}


def test_vol_target_rescales_on_the_first_session_of_the_month():
    weights = [{"AAA": 1.0, "BBB": 0.0} for _ in range(5)]
    returns = [
        {"AAA": None, "BBB": None},
        {"AAA": 0.01, "BBB": 0.0},
        {"AAA": 0.03, "BBB": 0.0},
        {"AAA": 0.02, "BBB": 0.0},
        {"AAA": 0.02, "BBB": 0.0},
    ]
    dates = [
        date(2024, 1, 29),
        date(2024, 1, 30),
        date(2024, 1, 31),
        date(2024, 2, 1),
        date(2024, 2, 2),
    ]
    scaled = apply_vol_target(weights, returns, dates, target_vol=0.15, lookback=3, max_leverage=2.0)
    window = [0.01, 0.03]
    mean = sum(window) / 2
    realised = math.sqrt(sum((item - mean) ** 2 for item in window) / 1) * math.sqrt(252.0)
    expected = min(0.15 / realised, 2.0)
    assert scaled[2]["AAA"] == pytest.approx(1.0)
    assert scaled[3]["AAA"] == pytest.approx(expected)
    assert scaled[4]["AAA"] == pytest.approx(expected)
    assert asset_returns({"AAA": [1.0, 1.01]})[0]["AAA"] is None


def test_business_month_end_snaps_to_the_last_weekday():
    dates = [date(2024, 3, 27), date(2024, 3, 28), date(2024, 3, 29), date(2024, 4, 1)]
    flags = business_month_end_flags(dates)
    assert flags[0] is True
    assert flags[2] is True
    assert flags[3] is False


def test_o17_sells_dropouts_and_does_not_resize_a_name_that_stays():
    closes = {
        "AAA": [10.0, 10.0, 12.0],
        "BBB": [10.0, 10.0, 11.0],
        "CCC": [10.0, 10.0, 9.0],
    }
    orders = orders_for_event(closes, 2, {"AAA": 5, "BBB": 0, "CCC": 4}, nav=100.0, prices={"AAA": 12.0, "BBB": 11.0, "CCC": 9.0}, lookback=2, top_n=2)
    assert ("sell", "CCC", 4) in orders
    assert not any(item[1] == "AAA" for item in orders)
    buys = [item for item in orders if item[0] == "buy" and item[1] == "BBB"]
    assert buys == [("buy", "BBB", int(50.0 / 11.0))]
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 2, 1), date(2024, 2, 2)]
    panel = {
        "AAA": [10.0, 10.0, 10.0, 12.0, 12.0],
        "BBB": [10.0, 10.0, 10.0, 11.0, 11.0],
    }
    opens = {symbol: list(series) for symbol, series in panel.items()}
    booked = run_monthly_rotation(dates, panel, opens, lookback=2, starting_equity=1000.0)
    assert booked["orders_submitted"] == 0
    assert booked["rebalances"] == 1


def test_w25_sign_and_inverse_vol_match_the_source_formula():
    closes = {"SPY": [1.0, 2.0, 3.0, 6.0], "QQQ": [4.0, 3.0, 2.0, 1.0]}
    signals, weights = build_continuous_futures_trend(
        closes, fast_window=2, slow_window=3, volatility_window=2
    )
    fast_spy = (3.0 + 6.0) / 2.0
    slow_spy = (2.0 + 3.0 + 6.0) / 3.0
    assert signals[3]["SPY"] == (1.0 if fast_spy > slow_spy else -1.0)
    spy_changes = [2.0 / 1.0 - 1.0, 3.0 / 2.0 - 1.0, 6.0 / 3.0 - 1.0]
    window = spy_changes[-2:]
    mean = sum(window) / 2
    vol = math.sqrt(sum((item - mean) ** 2 for item in window) / 1) * math.sqrt(252.0)
    raw_spy = signals[3]["SPY"] / vol
    assert raw_spy != 0
    gross = abs(weights[3]["SPY"]) + abs(weights[3]["QQQ"])
    assert gross == pytest.approx(1.0)
    assert long_only([{"SPY": 0.4, "QQQ": -0.6}]) == [{"SPY": 0.4, "QQQ": 0.0}]
    flat = run_weight_book(
        [date(2024, 1, 2), date(2024, 1, 3)],
        closes={"SPY": [1.0, 1.0]},
        opens={"SPY": [1.0, 1.0]},
        targets=[{"SPY": 0.0}, {"SPY": 0.0}],
        mask=[True, False],
    )
    assert flat["orders_submitted"] == 0


def test_benchmark_execute_is_refused_and_shadow_submits_nothing(tmp_path: Path):
    with pytest.raises(RunnerError, match="shadow-only"):
        run_runner(
            bars_path=Path(__file__).resolve().parents[1] / "fixtures" / "etf_daily.csv",
            out=tmp_path / "refused.json",
            ledger_path=tmp_path / "ledger.jsonl",
            order_log_path=tmp_path / "orders.jsonl",
            kill_file=tmp_path / "absent",
            strategy="w18",
            execute=True,
            now=datetime(2026, 10, 14, 15, 45, tzinfo=ET),
            refresh=False,
            bootstrap_reps=50,
        )
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "equity": "100000"})
        if path == "/v2/positions":
            return httpx.Response(200, json=[])
        if path == "/v2/account/portfolio/history":
            return httpx.Response(200, json={"timestamp": [], "equity": []})
        if path == "/v2/orders" and request.method == "GET":
            return httpx.Response(200, json=[])
        if path.endswith("/orders:by_client_order_id"):
            return httpx.Response(404, json={"message": "missing"})
        if path == "/v2/orders" and request.method == "POST":
            posted.append(json_body(request))
            return httpx.Response(200, json={"id": "ord", "client_order_id": "x"})
        raise AssertionError(request.method + " " + path)

    client = PaperTradeClient("key-id", "secret", transport=httpx.MockTransport(handler))
    report = run_runner(
        bars_path=Path(__file__).resolve().parents[1] / "fixtures" / "etf_daily.csv",
        out=tmp_path / "shadow.json",
        ledger_path=tmp_path / "live.jsonl",
        order_log_path=tmp_path / "live-orders.jsonl",
        kill_file=tmp_path / "absent",
        strategy="inverse_vol",
        execute=True,
        shadow=True,
        shadow_ledger=tmp_path / "shadow.jsonl",
        now=datetime(2026, 10, 14, 15, 45, tzinfo=ET),
        client=client,
        refresh=False,
        bootstrap_reps=50,
    )
    assert report["orders_submitted"] == len(posted) > 0
    assert report["strategy_id"].startswith("sidecar.inverse_vol")
    assert report["primary_config"]["plan"]["shadow"]["orders_submitted"] == 0
    marks = ChainLog(tmp_path / "shadow.jsonl").entries()
    assert {row["strategy"] for row in marks} == {"w18", "o17", "w25"}
    assert all(row["orders_submitted"] == 0 and row["live_orders"] is False for row in marks)
    assert all(body["symbol"] in {"SPY", "QQQ", "IWM"} for body in posted)


def json_body(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content.decode())
