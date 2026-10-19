"""Intraday maker: queue, latency, markouts, parquet, and no orders."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from sidecar.intraday_mm.feed import FeedError, IEX_URL, assert_data_url, parse_stream_message, subscribe_message
from sidecar.intraday_mm.fills import (
    Book,
    Fill,
    fill_probability,
    intrinsic_value,
    markout,
    queue_decrement_probability,
)
from sidecar.intraday_mm.session import build_queue_report, build_report, main, synthetic_tape
from sidecar.intraday_mm.stats import RollingMoments, rolling_std_strided
from sidecar.intraday_mm.tape import read_tape, write_tape


def test_queue_latency_and_sweep():
    book = Book(latency_ms=0)
    book.on_quote(0, 100.0, 100.02, 50, 50)
    book.decide(0, 100.0, 100.02, True, True)
    book.on_trade(1, 100.0, 30)
    assert book.inventory == 0
    book.on_trade(2, 100.0, 30)
    assert book.inventory == 1

    late = Book(latency_ms=100)
    late.on_quote(0, 100.0, 100.02, 10, 10)
    late.decide(0, 100.01, 100.01, True, False)
    late.on_trade(50, 100.01, 5)
    assert late.inventory == 0
    late.on_trade(100, 100.01, 1)
    assert late.inventory == 1

    swept = Book(latency_ms=0)
    swept.on_quote(0, 100.0, 100.02, 50, 50)
    swept.decide(0, 100.0, 100.02, True, False)
    swept.on_trade(1, 99.90, 1)
    assert swept.inventory == 1

    capped = Book(latency_ms=0)
    capped.inventory = 10
    capped.on_quote(0, 100.0, 100.02, 10, 10)
    capped.decide(0, 100.05, 100.10, True, True)
    capped.on_quote(1, 100.0, 100.02, 10, 10)
    assert capped.bid is None
    assert capped.ask is not None


def test_markouts_are_missing_when_the_tape_ends():
    fill = Fill(0, "buy", 100.0, 1.0, 100.0)
    mids = [(0, 100.0), (1000, 101.0), (5000, 99.0), (30000, 98.0)]
    assert markout(fill, mids, 1000) == pytest.approx(1.0)
    assert markout(fill, mids, 5000) == pytest.approx(-1.0)
    assert markout(fill, mids, 30000) == pytest.approx(-2.0)
    assert markout(fill, [(0, 100.0), (1000, 101.0)], 5000) is None
    sell = Fill(0, "sell", 100.0, 1.0, 100.0)
    assert markout(sell, mids, 1000) == pytest.approx(-1.0)


def test_parquet_roundtrip(tmp_path):
    events = synthetic_tape(1, seed=1)
    path = tmp_path / "tape.parquet"
    write_tape(path, events)
    loaded = read_tape(path)
    assert len(loaded) == len(events)
    assert loaded[0].kind == "quote"
    assert loaded[0].symbol == "SPY"


def test_feed_rejects_sip_and_the_live_host():
    with pytest.raises(FeedError):
        assert_data_url("wss://stream.data.alpaca.markets/v2/sip")
    with pytest.raises(FeedError):
        assert_data_url("wss://api.alpaca.markets/stream")
    with pytest.raises(FeedError):
        assert_data_url("wss://paper-api.alpaca.markets/stream")
    assert_data_url(IEX_URL)
    message = subscribe_message(["SPY"])
    assert "orders" not in message
    with pytest.raises(FeedError):
        parse_stream_message([{"action": "order", "symbol": "SPY"}])


def test_session_report_has_both_policies_and_no_orders(tmp_path):
    events = synthetic_tape(12, seed=0)
    report = build_report(events, tape="synthetic", symbol="SPY", ledger_path=tmp_path / "ledger.jsonl")
    assert report["orders_submitted"] == 0
    assert report["live_trading"] is False
    assert "as" in report and "rl" in report
    assert report["primary_latency_ms"] == 75
    for horizon in ("1000", "5000", "30000"):
        assert horizon in report["as"]["markouts"]


def test_rigtorp_queue_counts_trades_fully_and_cancels_with_probability():
    identity = queue_decrement_probability(40.0, 1.0, 100.0, "identity")
    logged = queue_decrement_probability(40.0, 1.0, 100.0, "log1p")
    assert identity == pytest.approx(40.0 / (40.0 + 60.0))
    assert logged == pytest.approx(np.log1p(40.0) / (np.log1p(40.0) + np.log1p(60.0)))
    assert logged != pytest.approx(identity)

    send = Book(latency_ms=100, model="rigtorp", initial="send", queue_fn="identity")
    send.on_quote(0, 100.0, 100.02, 50, 50)
    send.decide(0, 100.0, None, True, False)
    send.on_quote(50, 100.0, 100.02, 80, 80)
    send.on_quote(100, 100.0, 100.02, 80, 80)
    assert send.bid is not None
    assert send.bid.queue_ahead == pytest.approx(50.0)

    ack = Book(latency_ms=100, model="rigtorp", initial="ack", queue_fn="identity")
    ack.on_quote(0, 100.0, 100.02, 50, 50)
    ack.decide(0, 100.0, None, True, False)
    ack.on_quote(100, 100.0, 100.02, 80, 80)
    assert ack.bid is not None and ack.bid.queue_ahead == pytest.approx(80.0)

    average = Book(latency_ms=100, model="rigtorp", initial="average", queue_fn="identity")
    average.on_quote(0, 100.0, 100.02, 50, 50)
    average.decide(0, 100.0, None, True, False)
    average.on_quote(100, 100.0, 100.02, 80, 80)
    assert average.bid is not None and average.bid.queue_ahead == pytest.approx(65.0)

    book = Book(latency_ms=0, model="rigtorp", initial="send", queue_fn="identity")
    book.on_quote(0, 100.0, 100.02, 10, 10)
    book.decide(0, 100.0, None, True, False)
    book.on_quote(1, 100.0, 100.02, 10, 10)
    book.on_trade(2, 100.0, 10)
    assert book.inventory == 0
    assert book.bid is not None and book.bid.queue_ahead == pytest.approx(0.0)
    book.on_quote(3, 100.0, 100.02, 0, 10)
    book.on_trade(4, 100.0, 1)
    assert book.inventory == 1

    swept = Book(latency_ms=0, model="rigtorp", initial="send", queue_fn="identity")
    swept.on_quote(0, 100.0, 100.02, 50, 50)
    swept.decide(0, 100.0, None, True, False)
    swept.on_quote(1, 100.0, 100.02, 50, 50)
    swept.on_trade(2, 99.90, 1)
    assert swept.inventory == 0
    assert swept.bid is not None and swept.bid.queue_ahead == pytest.approx(49.0)

    # Joins behind us, then a cancel. p = 100 / (100 + 50) on the identity function.
    cancel = Book(latency_ms=0, model="rigtorp", initial="send", queue_fn="identity")
    cancel.on_quote(0, 100.0, 100.02, 100, 10)
    cancel.decide(0, 100.0, None, True, False)
    cancel.on_quote(1, 100.0, 100.02, 150, 10)
    assert cancel.bid is not None and cancel.bid.queue_ahead == pytest.approx(100.0)
    cancel.on_quote(2, 100.0, 100.02, 140, 10)
    assert cancel.bid.queue_ahead == pytest.approx(100.0 - (100.0 / 150.0) * 10.0)
    assert cancel.inventory == 0


def test_negative_intrinsic_cancels_and_a_later_decision_can_requote():
    book = Book(latency_ms=0, model="rigtorp", initial="send", queue_fn="identity")
    book.on_quote(0, 100.0, 100.02, 10, 10)
    ahead = 10.0
    value = intrinsic_value("buy", 100.0, book.last_mid, ahead, 1.0, "identity", adverse_cost=5.0)
    assert value < 0
    assert fill_probability(ahead, 1.0, "identity") == pytest.approx(1.0 / 11.0)
    book.adverse.push(-5.0)
    book.on_quote(1, 100.0, 100.02, 10, 10)
    book.decide(1, 100.0, None, True, False)
    assert book.n_iv_rejects == 1
    assert book.bid is None
    assert book.n_posts == 0

    fresh = Book(latency_ms=0, model="rigtorp", initial="send", queue_fn="identity")
    fresh.on_quote(0, 100.0, 100.02, 10, 10)
    fresh.decide(0, 100.0, None, True, False)
    fresh.on_quote(1, 100.0, 100.02, 10, 10)
    assert fresh.bid is not None
    fresh.adverse.push(-5.0)
    fresh.on_quote(2, 100.0, 100.02, 10, 10)
    assert fresh.bid is None
    assert fresh.n_cancels == 1
    fresh.on_trade(3, 100.0, 20)
    assert fresh.inventory == 0
    fresh.adverse = RollingMoments(20, min_count=1)
    fresh.decide(4, 100.0, None, True, False)
    fresh.on_quote(4, 100.0, 100.02, 10, 10)
    assert fresh.n_requotes == 1
    assert fresh.bid is not None


def test_rolling_moments_match_the_stride_view():
    rng = np.random.default_rng(0)
    series = rng.normal(0.0, 0.02, 80)
    window = 20
    online = RollingMoments(window, min_count=5)
    got = []
    for value in series:
        online.push(float(value))
        got.append(np.nan if online.std() is None else online.std())
    batched = rolling_std_strided(series, window, min_count=5)
    assert np.allclose(np.asarray(got, dtype=np.float64), batched, atol=1e-8, equal_nan=True)


def test_queue_comparison_is_a_simulation_and_submits_nothing():
    report = build_queue_report(minutes=2, symbol="SPY")
    assert report["orders_submitted"] == 0
    assert report["live_trading"] is False
    assert report["recorded_iex_session"] is False
    assert {row["tape"] for row in report["sessions"]} == {"synthetic_constant_size", "synthetic_variable_size"}
    for row in report["sessions"]:
        for model in ("legacy", "rigtorp"):
            assert row[model]["as"]["n_posts"] > 0
            assert "1000" in row[model]["as"]["markouts"]
    assert any("cancel" in line for line in report["caveats"])


def test_committed_queue_comparison_keeps_the_legacy_fill_count():
    path = Path(__file__).resolve().parents[1] / "examples" / "intraday_mm_queue_compare.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["orders_submitted"] == 0
    assert report["recorded_iex_session"] is False
    constant = report["sessions"][0]
    variable = report["sessions"][1]
    assert constant["legacy"]["as"]["n_fills"] == 791
    assert constant["legacy"]["as"]["ending_mtm"] == "-12.5618445107"
    assert constant["rigtorp"]["as"]["n_fills"] == 36
    assert constant["rigtorp"]["rl"]["n_fills"] == 4
    assert variable["rigtorp"]["as"]["n_fills"] == 1
    assert variable["rigtorp"]["rl"]["mean_minute_return"] == "-2.15838982539e-05"


def test_live_session_is_windowed(tmp_path):
    code = main(
        [
            "--now",
            "2026-10-18T15:00:00+00:00",
            "--minutes",
            "1",
            "--out",
            str(tmp_path / "out.json"),
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
        ]
    )
    assert code == 3
    assert datetime(2026, 10, 18, tzinfo=timezone.utc).weekday() == 6
