"""BUG-157: quote discovery extends daily readiness without repeating verified work."""
from collections import Counter
from datetime import timedelta

import ingest.market_data as market_module
from core.clock import FrozenClock
from ingest.market_data import MarketDataService
from orchestrator.quant_ws import SnapshotSource
from storage.audit import AuditLog
from storage.engine import create_all, make_engine, make_session_factory
from storage.parquet_store import snapshot_path, write_snapshot
from tests.ingest.test_market_data import NOW, Client, _bars


class HoldingsClient(Client):
    def __init__(self, clock):
        super().__init__(clock)
        self.bar_calls = []
        self.unavailable = set()

    def daily_bars(self, symbol, *, limit):
        assert limit == 60
        self.bar_calls.append(symbol)
        if symbol in self.unavailable:
            raise OSError("temporary bar outage")
        return _bars()


def _market(tmp_path):
    clock = FrozenClock(NOW)
    client = HoldingsClient(clock)
    engine = make_engine(f"sqlite:///{(tmp_path / 'daily.db').as_posix()}")
    create_all(engine)
    audit = AuditLog(make_session_factory(engine), hmac_key=b"daily-universe", clock=clock)
    market = MarketDataService(
        client, symbols=["AAPL"], clock=clock, raw_root=tmp_path / "raw",
        snapshot_source=SnapshotSource(), audit_log=audit,
        additional_symbols_fn=lambda: [row["symbol"] for row in client.positions_info()],
    )
    return market, client, clock


def _discover(market, client, *symbols):
    client.positions = [{"symbol": symbol, "qty": "10"} for symbol in symbols]
    assert market.poll_quotes()["failed"] == []


def test_new_holding_is_quoted_and_loaded_without_refetch_or_recalculation(tmp_path, monkeypatch):
    market, client, _ = _market(tmp_path)
    calculations = Counter()
    calculate = market_module.calculate_daily_metrics

    def measured(symbol, bars):
        calculations[symbol] += 1
        return calculate(symbol, bars)

    monkeypatch.setattr(market_module, "calculate_daily_metrics", measured)
    _discover(market, client)
    assert market.refresh_daily_bars()["updated"] == ["AAPL"]
    first_metric = market.metrics("AAPL")
    initial_calculations = calculations["AAPL"]
    _discover(market, client, "MSFT")
    assert market.quote("MSFT")["symbol"] == "MSFT"
    assert market.refresh_daily_bars() == {
        "updated": ["MSFT"], "cached": ["AAPL"], "failed": [], "at": NOW.date().isoformat(),
    }
    assert market.metrics("AAPL") is first_metric
    assert market.metrics("MSFT") is not None
    assert calculations["AAPL"] == initial_calculations
    assert market.refresh_daily_bars()["cached"] == ["AAPL", "MSFT"]
    assert client.bar_calls == ["AAPL", "MSFT"]


def test_new_holding_failure_retries_only_the_missing_metrics(tmp_path):
    market, client, _ = _market(tmp_path)
    market.refresh_daily_bars()
    existing = market.metrics("AAPL")
    _discover(market, client, "MSFT")
    client.unavailable.add("MSFT")
    assert market.refresh_daily_bars()["failed"] == ["MSFT"]
    assert market.metrics("MSFT") is None
    client.unavailable.clear()
    assert market.refresh_daily_bars()["updated"] == ["MSFT"]
    assert client.bar_calls == ["AAPL", "MSFT", "MSFT"]
    assert market.metrics("AAPL") is existing


def test_removed_then_readded_holding_reuses_verified_disk_cache(tmp_path):
    market, client, _ = _market(tmp_path)
    _discover(market, client, "MSFT")
    market.refresh_daily_bars()
    _discover(market, client)
    assert market.metrics("MSFT") is None
    assert market.refresh_daily_bars()["cached"] == ["AAPL"]
    _discover(market, client, "MSFT")
    assert market.refresh_daily_bars()["cached"] == ["AAPL", "MSFT"]
    assert market.metrics("MSFT") is not None
    assert client.bar_calls == ["AAPL", "MSFT"]


def test_removed_failed_symbol_does_not_keep_current_universe_unready(tmp_path):
    market, client, _ = _market(tmp_path)
    _discover(market, client, "MSFT")
    client.unavailable.add("MSFT")
    assert market.refresh_daily_bars()["failed"] == ["MSFT"]
    _discover(market, client)
    result = market.refresh_daily_bars()
    assert result["failed"] == [] and result["cached"] == ["AAPL"]
    assert client.bar_calls == ["AAPL", "MSFT"]


def test_new_holding_unsigned_cache_cannot_gain_memory_readiness(tmp_path):
    market, client, _ = _market(tmp_path)
    market.refresh_daily_bars()
    write_snapshot(tmp_path / "raw" / "daily-bars", NOW.date().isoformat(), "MSFT", _bars())
    _discover(market, client, "MSFT")
    for _ in range(2):
        assert market.refresh_daily_bars()["failed"] == ["MSFT"]
        assert market.metrics("MSFT") is None
    assert client.bar_calls == ["AAPL"]


def test_readded_holding_revalidates_changed_cache(tmp_path):
    market, client, _ = _market(tmp_path)
    _discover(market, client, "MSFT")
    market.refresh_daily_bars()
    _discover(market, client)
    root = tmp_path / "raw" / "daily-bars"
    day = NOW.date().isoformat()
    snapshot_path(root, day, "MSFT").unlink()
    write_snapshot(root, day, "MSFT", [{**row, "v": "99999999"} for row in _bars()])
    _discover(market, client, "MSFT")
    assert market.refresh_daily_bars()["failed"] == ["MSFT"]
    assert market.metrics("MSFT") is None
    assert client.bar_calls == ["AAPL", "MSFT"]


def test_each_current_symbol_refreshes_after_day_rollover(tmp_path):
    market, client, clock = _market(tmp_path)
    _discover(market, client, "MSFT")
    market.refresh_daily_bars()
    clock._at += timedelta(days=1)
    assert market.refresh_daily_bars()["updated"] == ["AAPL", "MSFT"]
    assert client.bar_calls == ["AAPL", "MSFT", "AAPL", "MSFT"]


def test_symbol_removed_during_bar_fetch_is_not_republished(tmp_path):
    market, client, _ = _market(tmp_path)
    market.refresh_daily_bars()
    _discover(market, client, "MSFT")
    fetch = client.daily_bars

    def remove_during_fetch(symbol, *, limit):
        rows = fetch(symbol, limit=limit)
        _discover(market, client)
        return rows

    client.daily_bars = remove_during_fetch
    result = market.refresh_daily_bars()
    assert result["updated"] == [] and result["cached"] == ["AAPL"]
    assert market.metrics("MSFT") is None
    client.daily_bars = fetch
    _discover(market, client, "MSFT")
    assert market.refresh_daily_bars()["cached"] == ["AAPL", "MSFT"]
    assert client.bar_calls == ["AAPL", "MSFT"]
