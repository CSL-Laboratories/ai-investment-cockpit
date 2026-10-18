"""Intraday maker: queue, latency, markouts, parquet, and no orders."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sidecar.intraday_mm.feed import FeedError, IEX_URL, assert_data_url, parse_stream_message, subscribe_message
from sidecar.intraday_mm.fills import Book, Fill, markout
from sidecar.intraday_mm.session import build_report, main, synthetic_tape
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
