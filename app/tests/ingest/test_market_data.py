"""RW-14 market-data cache, time-domain, persistence, and metric regressions."""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.clock import FrozenClock
from ingest.market_data import (
    MarketDataError,
    MarketDataService,
    calculate_daily_metrics,
    parse_exchange_time,
)
from orchestrator.state import EVENT_MARKET_DATA_RAW
from orchestrator.quant_ws import SnapshotSource
from quant.events import to_ns
from storage.parquet_store import write_snapshot

NOW = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


def _bars():
    return [
        {"t": "2026-07-01T04:00:00Z", "c": "100", "v": "1000"},
        {"t": "2026-07-02T04:00:00Z", "c": "102", "v": "2000"},
        {"t": "2026-07-03T04:00:00Z", "c": "101", "v": "3000"},
    ]


class Client:
    def __init__(self, clock):
        self.clock = clock
        self.quote_calls = 0
        self.bar_calls = 0
        self.fail = False
        self.future = False
        self.positions = []

    def latest_quote(self, symbol):
        self.quote_calls += 1
        if self.fail:
            raise RuntimeError("sanitized remote failure")
        delta = timedelta(minutes=5) if self.future else timedelta(seconds=-1)
        stamp = self.clock.now() + delta
        return {
            "symbol": symbol, "price": Decimal("100"), "spread_pct": Decimal("0.02"),
            "bid": Decimal("99.99"), "ask": Decimal("100.01"),
            "bid_size": Decimal("100"), "ask_size": Decimal("120"),
            "ts": stamp.isoformat(), "feed": "iex",
        }

    def daily_bars(self, symbol, *, limit):
        assert symbol == "AAPL" and limit == 60
        self.bar_calls += 1
        return _bars()

    def positions_info(self):
        return list(self.positions)


def _service(tmp_path):
    clock = FrozenClock(NOW)
    client = Client(clock)
    source = SnapshotSource()
    service = MarketDataService(
        client, symbols={"AAPL"}, clock=clock, raw_root=tmp_path / "raw",
        snapshot_source=source,
    )
    return service, client, clock, source


def test_daily_metrics_are_deterministic_and_annualized():
    got = calculate_daily_metrics("aapl", _bars())
    expected_returns = [math.log(1.02), math.log(101 / 102)]
    assert got.symbol == "AAPL" and got.observations == 3
    assert got.median_daily_dollar_volume == Decimal("204000")
    assert got.realized_volatility == pytest.approx(
        statistics.stdev(expected_returns) * math.sqrt(252)
    )


@pytest.mark.parametrize("value", [None, "", "2026-07-07T14:00:00", "not-a-time"])
def test_missing_naive_and_malformed_exchange_times_are_refused(value):
    with pytest.raises(MarketDataError):
        parse_exchange_time(value)


def test_quote_poll_publishes_epoch_book_and_advancing_atomic_snapshot(tmp_path):
    service, client, _, source = _service(tmp_path)
    assert service.book("AAPL") is None

    first = service.poll_quotes()
    version1, frame1 = source.read()
    second = service.poll_quotes()
    version2, frame2 = source.read()

    assert first["failed"] == second["failed"] == []
    assert service.book("AAPL").ts == to_ns(NOW - timedelta(seconds=1))
    assert service.book("AAPL").bids[0].qty == 100
    assert (version1, frame1["seq"]) == (1, 1)
    assert (version2, frame2["seq"]) == (2, 2)
    assert client.quote_calls == 2
    assert list((tmp_path / "raw" / "quotes").rglob("*.parquet"))
    assert service.verify_params.max_staleness_ns == 120_000_000_000


def test_future_or_failed_poll_never_reads_fresh(tmp_path):
    service, client, clock, _ = _service(tmp_path)
    client.future = True
    assert service.poll_quotes()["failed"] == ["AAPL"]
    assert service.book("AAPL") is None
    assert service.max_quote_age_s(now=clock.now()) == float("inf")
    with pytest.raises(MarketDataError):
        service.quote("AAPL")

    client.future = False
    assert service.poll_quotes()["failed"] == []
    client.fail = True
    assert service.poll_quotes()["failed"] == ["AAPL"]
    assert service.stale_reason("AAPL", now=clock.now(), limit_s=120) is not None


def test_quote_timestamp_is_compared_with_time_observed_after_remote_call(tmp_path):
    class AdvancingClock(FrozenClock):
        def advance(self, delta):
            self._at += delta

    clock = AdvancingClock(NOW)
    client = Client(clock)
    original = client.latest_quote

    def delayed_quote(symbol):
        clock.advance(timedelta(seconds=1))
        return original(symbol)

    client.latest_quote = delayed_quote
    service = MarketDataService(
        client,
        symbols=("AAPL", "MSFT"),
        clock=clock,
        raw_root=tmp_path / "raw",
        snapshot_source=SnapshotSource(),
    )

    result = service.poll_quotes()

    assert result == {
        "updated": ["AAPL", "MSFT"],
        "failed": [],
        "at": NOW.isoformat(),
    }
    assert service.quote("AAPL")["age_s"] == pytest.approx(2.0)
    assert service.quote("MSFT")["age_s"] == pytest.approx(1.0)


def test_quote_still_fails_closed_when_future_after_remote_call(tmp_path):
    service, client, _, _ = _service(tmp_path)
    original = client.latest_quote

    def slightly_future_quote(symbol):
        quote = original(symbol)
        quote["ts"] = (NOW + timedelta(microseconds=1)).isoformat()
        return quote

    client.latest_quote = slightly_future_quote

    assert service.poll_quotes()["failed"] == ["AAPL"]
    assert service.book("AAPL") is None


def test_daily_bar_cache_survives_a_second_service_without_refetch(tmp_path):
    service, client, clock, source = _service(tmp_path)
    assert service.refresh_daily_bars()["updated"] == ["AAPL"]
    metrics = service.metrics("AAPL")
    assert metrics is not None and metrics.observations == 3
    assert client.bar_calls == 1

    restarted = MarketDataService(
        client, symbols={"AAPL"}, clock=clock, raw_root=tmp_path / "raw",
        snapshot_source=source,
    )
    assert restarted.refresh_daily_bars()["cached"] == ["AAPL"]
    assert restarted.metrics("AAPL") == metrics
    assert client.bar_calls == 1


def test_unbound_preseeded_daily_bar_cache_is_never_self_signed(tmp_path):
    class Audit:
        def __init__(self):
            self.entries = []

        def append(self, event_type, **kwargs):
            self.entries.append((event_type, kwargs))

    clock = FrozenClock(NOW)
    client = Client(clock)
    audit = Audit()
    raw_root = tmp_path / "raw"
    forged = [
        {
            "t": (NOW - timedelta(days=25 - offset)).isoformat(),
            "c": str(Decimal("100") + Decimal(offset) / Decimal("1000000")),
            "v": "1000000000000",
        }
        for offset in range(25)
    ]
    write_snapshot(raw_root / "daily-bars", NOW.date().isoformat(), "AAPL", forged)
    service = MarketDataService(
        client,
        symbols={"AAPL"},
        clock=clock,
        raw_root=raw_root,
        snapshot_source=SnapshotSource(),
        audit_log=audit,
    )

    result = service.refresh_daily_bars()

    assert result["failed"] == ["AAPL"]
    assert result["cached"] == []
    assert service.metrics("AAPL") is None
    assert client.bar_calls == 0
    assert audit.entries == []


def test_current_session_daily_bar_never_enters_authority_bearing_metrics(tmp_path):
    service, client, _, _ = _service(tmp_path)
    client.daily_bars = lambda _symbol, *, limit: [
        *_bars(),
        {"t": NOW.isoformat(), "c": "10000", "v": "999999999"},
    ]

    assert service.refresh_daily_bars()["failed"] == []
    metric = service.metrics("AAPL")

    assert metric is not None
    assert metric.as_of.isoformat() == "2026-07-03"
    assert metric.observations == 3
    assert metric.median_daily_dollar_volume == Decimal("204000")


def test_holdings_only_symbols_join_the_polled_universe(tmp_path):
    clock = FrozenClock(NOW)
    client = Client(clock)
    client.positions = [{"symbol": "MSFT"}]
    service = MarketDataService(
        client,
        symbols={"AAPL"},
        additional_symbols_fn=lambda: (row["symbol"] for row in client.positions_info()),
        clock=clock,
        raw_root=tmp_path / "raw",
        snapshot_source=SnapshotSource(),
    )

    result = service.poll_quotes()

    assert result["failed"] == []
    assert service.symbols == ("AAPL", "MSFT")
    assert service.quote("MSFT")["symbol"] == "MSFT"
    assert service.stale_reason("MSFT", now=NOW, limit_s=120) is None


def test_symbol_discovery_failure_is_visible_and_globally_fail_closed(tmp_path):
    clock = FrozenClock(NOW)
    client = Client(clock)

    def unavailable():
        raise RuntimeError("positions unavailable")

    service = MarketDataService(
        client,
        symbols={"AAPL"},
        additional_symbols_fn=unavailable,
        clock=clock,
        raw_root=tmp_path / "raw",
        snapshot_source=SnapshotSource(),
    )

    assert service.poll_quotes()["failed"] == []
    assert service.max_quote_age_s(now=NOW) == float("inf")
    assert service.stale_reason("AAPL", now=NOW, limit_s=120) is not None
    assert service.status()["universe_fault"] == "additional_symbol_discovery_unavailable"


def test_raw_snapshots_are_bound_to_the_audit_seam(tmp_path):
    from types import SimpleNamespace

    class Audit:
        def __init__(self):
            self.entries = []

        def append(self, event_type, **kwargs):
            self.entries.append((event_type, kwargs))

        def verified_events(self, event_type):
            return [SimpleNamespace(**data) for kind, data in self.entries if kind == event_type]

    clock = FrozenClock(NOW)
    client = Client(clock)
    audit = Audit()
    service = MarketDataService(
        client,
        symbols={"AAPL"},
        clock=clock,
        raw_root=tmp_path / "raw",
        snapshot_source=SnapshotSource(),
        audit_log=audit,
    )

    service.poll_quotes()
    service.refresh_daily_bars()

    assert [entry[0] for entry in audit.entries] == [
        EVENT_MARKET_DATA_RAW, EVENT_MARKET_DATA_RAW
    ]
    assert {entry[1]["payload"]["kind"] for entry in audit.entries} == {
        "quote", "daily_bars"
    }
    assert all(len(entry[1]["payload"]["content_hash"]) == 64 for entry in audit.entries)


def test_one_faulted_universe_symbol_keeps_global_authority_fail_closed(tmp_path):
    clock = FrozenClock(NOW)
    client = Client(clock)
    original = client.latest_quote

    def mixed(symbol):
        if symbol == "MSFT":
            raise RuntimeError("halted or unavailable")
        return original(symbol)

    client.latest_quote = mixed
    service = MarketDataService(
        client,
        symbols=("AAPL", "MSFT"),
        clock=clock,
        raw_root=tmp_path / "raw",
        snapshot_source=SnapshotSource(),
    )

    result = service.poll_quotes()

    assert result == {
        "updated": ["AAPL"],
        "failed": ["MSFT"],
        "at": NOW.isoformat(),
    }
    assert service.quote("AAPL")["symbol"] == "AAPL"
    assert service.max_quote_age_s(now=NOW) == float("inf")
    assert service.stale_reason("AAPL", now=NOW, limit_s=120) is not None
    assert service.status()["faulted"] == ["MSFT"]
