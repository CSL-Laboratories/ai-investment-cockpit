"""Offline audit probes; writes only to a private temporary directory.

Run with the project virtualenv and the repository root as the only argument.
No production composition, network, real keyring, or runtime state is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from datetime import datetime, timezone

repo = Path(sys.argv[1]).resolve()
for name in tuple(os.environ):
    if name.startswith("COCKPIT_"):
        del os.environ[name]
os.environ["COCKPIT_ENV_FILE"] = str(Path(__file__).with_name("absent.env"))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
sys.path.insert(0, str(repo / "app"))

from core.clock import FrozenClock
from ingest.alpaca_news import AlpacaNewsConsumer
from ingest.market_data import MarketDataService
from orchestrator.ingest_runner import IngestRunner
from orchestrator.quant_ws import SnapshotSource
from storage.audit import AuditLog
from storage.engine import create_all, make_engine, make_session_factory
from workflows.events import persist_events, resolve_raw

clock = FrozenClock(datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc))
results = {}
with tempfile.TemporaryDirectory(prefix="ingest-", dir=Path(__file__).parent) as temp:
    root = Path(temp)
    engine = make_engine(f"sqlite:///{(root / 'probe.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    audit = AuditLog(sf, b"audit-probe-fixture-key-only", clock=clock)
    state = SimpleNamespace(clock=clock, session_factory=sf, audit=audit)

    # Both normal summary+body and the fallback body are irreversible on read-back.
    for name, summary in (("summary_and_body", "Vendor short summary."),
                          ("long_body_only", "")):
        raw = {"id": name, "symbols": ["AAPL"], "headline": "Test filing update",
               "created_at": clock.now().isoformat(), "summary": summary,
               "content": "x" * 4500 + " QUALIFICATION_ONLY_IN_ORIGINAL_BODY",
               "url": "https://example.invalid/article", "author": "Fixture source"}
        events = []
        AlpacaNewsConsumer(lambda: iter([raw])).run(events.append)
        persisted = persist_events(state, events, parquet_root=root / "raw")
        row = persisted.stored[0]
        recovered = resolve_raw(root / "raw", row.raw_ref, state=state, row=row)
        results[name] = {
            "audit_valid": audit.verify_chain().ok,
            "returned_keys": sorted(recovered),
            "returned_summary_length": len(recovered["summary"]),
            "original_content_length": len(raw["content"]),
            "qualification_recoverable": "QUALIFICATION_ONLY_IN_ORIGINAL_BODY" in json.dumps(recovered),
            "url_recoverable": raw["url"] in json.dumps(recovered),
        }
        assert not results[name]["qualification_recoverable"]

    # A real runner + real consumer report success while every persist attempt fails.
    def unavailable_persist(*args, **kwargs):
        raise OSError("fixture storage unavailable")

    consumer = AlpacaNewsConsumer(lambda: iter([{
        "id": "failed-delivery", "symbols": ["AAPL"], "headline": "Fixture news",
        "created_at": clock.now().isoformat(), "summary": "Fixture summary"}]))
    runner = IngestRunner(state, parquet_root=root / "unused",
                          persist_fn=unavailable_persist, clock=clock)
    runner.add_stream("alpaca:benzinga", consumer)
    outcome = runner.run_stream("alpaca:benzinga")
    status = runner.status()["feeds"]["alpaca:benzinga"]
    results["stream_persistence_failure"] = {
        "run_outcome": outcome,
        "operator_status": status,
        "internal_consumer_counters": consumer.counters,
    }
    assert outcome["status"] == "ok" and status["errors"] == 0
    assert consumer.counters["emit_error"] == 1 and "counters" not in status

    # A one-time signing/persistence failure strands the fixed daily-bars pathname.
    class FlakyAudit:
        def __init__(self):
            self.attempts = 0
            self.events = []

        def append(self, event_type, *, actor, refs, payload):
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("one-time audit write failure")
            self.events.append(SimpleNamespace(refs=refs, payload=payload))

        def verified_events(self, event_type):
            return self.events

    class BarsClient:
        calls = 0

        def daily_bars(self, symbol, *, limit):
            self.calls += 1
            return [{"t": f"2026-09-0{day}T04:00:00Z", "c": str(100 + day), "v": "1000000"}
                    for day in (1, 2, 3)]

    client, flaky = BarsClient(), FlakyAudit()
    market = MarketDataService(client, symbols=["AAPL"], clock=clock,
                               raw_root=root / "market", snapshot_source=SnapshotSource(),
                               audit_log=flaky)
    attempts = [market.refresh_daily_bars() for _ in range(3)]
    results["daily_bars_audit_retry"] = {
        "refresh_outcomes": attempts,
        "vendor_fetches": client.calls,
        "audit_append_attempts": flaky.attempts,
        "metrics_available": market.metrics("AAPL") is not None,
    }
    assert all(item["failed"] == ["AAPL"] for item in attempts)
    assert flaky.attempts == client.calls == 1
    engine.dispose()

print(json.dumps(results, indent=2))
