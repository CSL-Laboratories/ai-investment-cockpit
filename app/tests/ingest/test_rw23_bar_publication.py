"""BUG-152: failed signing cannot leave an unsigned write-once daily cache."""
from types import SimpleNamespace

import pytest

from ingest.market_data import MarketDataError
from tests.ingest.test_market_data import _service


class Audit:
    def __init__(self):
        self.calls = 0
        self.entries = []

    def verified_events(self, event_type):
        return self.entries

    def append(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise OSError("one-time fixture audit outage")
        self.entries.append(SimpleNamespace(**kwargs))


def test_daily_bars_retry_after_one_time_audit_failure(tmp_path):
    market, client, _, _ = _service(tmp_path)
    market._audit = audit = Audit()
    assert market.refresh_daily_bars()["failed"] == ["AAPL"]
    assert not list((tmp_path / "raw").rglob("*.parquet"))
    assert market.metrics("AAPL") is None
    assert market.refresh_daily_bars()["updated"] == ["AAPL"]
    assert audit.calls == client.bar_calls == 2
    assert market.metrics("AAPL") is not None


def test_reused_identity_cannot_silently_bind_another_digest(tmp_path):
    market, _, _, _ = _service(tmp_path)
    market._bind_raw(kind="daily_bars", day="2026-07-07", key="AAPL", digest="a" * 64, count=3)
    with pytest.raises(MarketDataError, match="different content"):
        market._bind_raw(kind="daily_bars", day="2026-07-07", key="AAPL", digest="b" * 64, count=3)
