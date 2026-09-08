"""Independent publication, restart and substitution review for BUG-152."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import ingest.market_data as market_module
import storage.parquet_store as parquet
from ingest.market_data import MarketDataError
from orchestrator.state import EVENT_MARKET_DATA_RAW
from storage.audit import AuditLog
from storage.engine import create_all, make_engine, make_session_factory
from tests.ingest.test_market_data import NOW, _bars, _service


def _signed_service(tmp_path, audit=None):
    market, client, clock, _ = _service(tmp_path)
    if audit is None:
        engine = make_engine(f"sqlite:///{(tmp_path / 'publication.db').as_posix()}")
        create_all(engine)
        audit = AuditLog(make_session_factory(engine), hmac_key=b"independent-bars", clock=clock)
    market._audit = audit
    return market, client, audit


def _changed_rows():
    return [{**row, "v": "999999999"} for row in _bars()]


def test_post_publication_path_swap_cannot_supply_metrics(tmp_path, monkeypatch):
    market, _, audit = _signed_service(tmp_path)
    write = parquet.write_snapshot

    def publish_then_substitute(root, day, key, records, **kwargs):
        path, digest = write(root, day, key, records, **kwargs)
        path.unlink()
        write(root, day, key, _changed_rows())
        return path, digest

    monkeypatch.setattr(market_module, "write_snapshot", publish_then_substitute)
    result = market.refresh_daily_bars()
    assert result["failed"] == ["AAPL"]
    assert market.metrics("AAPL") is None
    assert audit.verify_chain().ok


def test_restart_after_link_failure_refuses_changed_retry_binding(tmp_path, monkeypatch):
    market, _, audit = _signed_service(tmp_path)

    def unavailable_link(*args, **kwargs):
        raise PermissionError("publication temporarily unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(parquet.os, "link", unavailable_link)
        assert market.refresh_daily_bars()["failed"] == ["AAPL"]
    assert not list((tmp_path / "raw").rglob("*.parquet"))
    assert len(audit.verified_events(EVENT_MARKET_DATA_RAW)) == 1
    restarted, client, _ = _signed_service(tmp_path, audit)
    client.daily_bars = lambda symbol, *, limit: _changed_rows()
    assert restarted.refresh_daily_bars()["failed"] == ["AAPL"]
    assert restarted.metrics("AAPL") is None
    assert len(audit.verified_events(EVENT_MARKET_DATA_RAW)) == 1


def test_restart_after_link_failure_can_publish_identical_signed_bytes(tmp_path, monkeypatch):
    market, _, audit = _signed_service(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(parquet.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
        assert market.refresh_daily_bars()["failed"] == ["AAPL"]
    restarted, client, _ = _signed_service(tmp_path, audit)
    assert restarted.refresh_daily_bars()["updated"] == ["AAPL"]
    assert restarted.metrics("AAPL") is not None
    assert client.bar_calls == 1
    assert len(audit.verified_events(EVENT_MARKET_DATA_RAW)) == 1


def test_competing_final_file_is_preserved_but_not_trusted(tmp_path, monkeypatch):
    market, _, audit = _signed_service(tmp_path)
    link = parquet.os.link

    def raced_link(staged, final):
        pq.write_table(pa.Table.from_pylist(_changed_rows()), final)
        return link(staged, final)

    with monkeypatch.context() as patch:
        patch.setattr(parquet.os, "link", raced_link)
        assert market.refresh_daily_bars()["failed"] == ["AAPL"]
    final = parquet.snapshot_path(tmp_path / "raw" / "daily-bars", NOW.date().isoformat(), "AAPL")
    assert parquet._read_records(final) == _changed_rows()
    assert not list(final.parent.glob(".AAPL-*.parquet"))
    restarted, client, _ = _signed_service(tmp_path, audit)
    assert restarted.refresh_daily_bars()["failed"] == ["AAPL"]
    assert restarted.metrics("AAPL") is None
    assert client.bar_calls == 0


def test_staged_read_failure_leaves_no_binding_and_retry_succeeds(tmp_path, monkeypatch):
    market, _, audit = _signed_service(tmp_path)
    read = parquet._read_records

    def unreadable_staged(path):
        if path.name.startswith(".AAPL-"):
            raise OSError("staged read unavailable")
        return read(path)

    with monkeypatch.context() as patch:
        patch.setattr(parquet, "_read_records", unreadable_staged)
        assert market.refresh_daily_bars()["failed"] == ["AAPL"]
    assert not list((tmp_path / "raw").rglob("*.parquet"))
    assert audit.verified_events(EVENT_MARKET_DATA_RAW) == []
    assert market.refresh_daily_bars()["updated"] == ["AAPL"]


def test_transient_staging_cleanup_failure_is_retried(tmp_path, monkeypatch):
    market, _, audit = _signed_service(tmp_path)
    unlink = Path.unlink
    attempts = []

    def busy_staged(path, *args, **kwargs):
        if path.name.startswith(".AAPL-"):
            attempts.append(path)
            if len(attempts) < 3:
                raise PermissionError("transient scanner handle")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", busy_staged)
    assert market.refresh_daily_bars()["updated"] == ["AAPL"]
    assert len(attempts) == 3
    assert len(list((tmp_path / "raw").rglob("*.parquet"))) == 1
    assert len(audit.verified_events(EVENT_MARKET_DATA_RAW)) == 1


def test_concurrent_different_digests_never_both_get_signed(tmp_path):
    market, _, _, _ = _service(tmp_path)
    entered, release, second_started, second_append = Event(), Event(), Event(), Event()
    calls = []
    calls_lock = Lock()

    class SlowAudit:
        def verified_events(self, event_type):
            return []

        def append(self, event_type, **kwargs):
            digest = kwargs["payload"]["content_hash"]
            with calls_lock:
                calls.append(digest)
            if digest == "a" * 64:
                entered.set()
                assert release.wait(5)
            else:
                second_append.set()

    market._audit = SlowAudit()

    def bind(digest):
        if digest == "b" * 64:
            second_started.set()
        market._bind_raw(kind="daily_bars", day="2026-07-07", key="AAPL", digest=digest, count=3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(bind, "a" * 64)
        assert entered.wait(5)
        second = pool.submit(bind, "b" * 64)
        assert second_started.wait(5)
        # A released lock permits the second callback while the first signature is in flight.
        second_append.wait(0.2)
        release.set()
        first.result(timeout=5)
        with pytest.raises(MarketDataError, match="different content"):
            second.result(timeout=5)
    assert calls == ["a" * 64]
