"""G8 — ingest→storage persistence bridge tests (SEC-13 / Invariant 8,
docs/security-hardening-backlog.md; docs/build-scaffold-ultracode.md WP8 ingest window).

Enforces the contract's events.py section: one write-once parquet batch per persist call,
EVENT_INGEST_BATCH binds the batch content_hash into the signed chain, per-item
``parquet://{date}/events-{batch_id}#{idx}`` raw_refs, identity dedupe, tz-aware ts
parsing with clock fallback, and FAIL-CLOSED verified read-back (verify_snapshot against
the audit-bound hash + per-item content_hash vs the MarketEvent row).
"""
from __future__ import annotations

from datetime import timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select

from core.clock import FrozenClock
from ingest.normalize import to_market_event
from schemas.models.audit_entry import AuditEntry
from storage.parquet_store import snapshot_path
from tests.workflows.conftest import NOW
from workflows.constants import EVENT_INGEST_BATCH
from workflows.events import (
    PersistResult,
    RawIntegrityError,
    RawUnavailableError,
    events_in_window,
    persist_events,
    resolve_raw,
)


def make_dict(i=1, symbol="AAPL", source="alpaca:benzinga", headline=None, summary=None,
              ts="2026-07-07T13:30:00+00:00", event_type="news"):
    return to_market_event(
        source=source, article_id=f"a-{i}", symbols=[symbol],
        headline=headline if headline is not None else f"Headline {i} about {symbol}",
        summary=summary if summary is not None else f"Summary {i}", ts=ts,
        event_type=event_type,
    )


def test_persist_round_trip_and_audit_binding(st, clock, tmp_path):
    root = tmp_path / "raw"
    dicts = [make_dict(1, "AAPL"), make_dict(2, "MSFT", source="finnhub", ts=1783776600.0)]
    res = persist_events(st, dicts, parquet_root=root, clock=clock)
    assert isinstance(res, PersistResult)
    assert len(res.stored) == 2 and res.duplicates == 0
    assert res.raw_key and res.raw_key.startswith("events-")
    date = clock.now().date().isoformat()
    for i, row in enumerate(res.stored):
        assert row.raw_ref == f"parquet://{date}/{res.raw_key}#{i}"
        assert row.ts.tzinfo is not None
    assert res.stored[0].symbol == "AAPL" and res.stored[1].symbol == "MSFT"
    # audit entry binds the batch hash; chain verifies
    with st.session_factory() as s:
        entries = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_INGEST_BATCH)).scalars().all()
    assert len(entries) == 1
    p = entries[0].payload
    assert p["raw_key"] == res.raw_key and p["date"] == date and p["count"] == 2
    assert isinstance(p["content_hash"], str) and len(p["content_hash"]) == 64
    assert st.audit.verify_chain().ok


def test_dedupe_within_call_and_against_db(st, clock, tmp_path):
    root = tmp_path / "raw"
    d = make_dict(1)
    res = persist_events(st, [d, dict(d)], parquet_root=root, clock=clock)
    assert len(res.stored) == 1 and res.duplicates == 1
    # same identity again -> nothing stored, no new parquet batch
    res2 = persist_events(st, [dict(d)], parquet_root=root, clock=clock)
    assert res2.stored == [] and res2.duplicates == 1 and res2.raw_key is None


def test_ts_parse_fallback_to_clock(st, clock, tmp_path):
    d = make_dict(1, ts=None)
    res = persist_events(st, [d], parquet_root=tmp_path / "raw", clock=clock)
    assert res.stored[0].ts == NOW


def test_events_in_window_uses_ingested_at(st, clock, tmp_path):
    root = tmp_path / "raw"
    early = FrozenClock(NOW - timedelta(hours=3))
    persist_events(st, [make_dict(1)], parquet_root=root, clock=early)
    persist_events(st, [make_dict(2)], parquet_root=root, clock=clock)
    rows = events_in_window(st, NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0].headline.startswith("Headline 2")
    both = events_in_window(st, NOW - timedelta(hours=4), NOW + timedelta(hours=1))
    assert len(both) == 2


def test_resolve_raw_verified_read_back(st, clock, tmp_path):
    root = tmp_path / "raw"
    d = make_dict(1)
    res = persist_events(st, [d], parquet_root=root, clock=clock)
    row = res.stored[0]
    item = resolve_raw(root, row.raw_ref, state=st, row=row)
    assert item["article_id"] == "a-1" and item["symbols"] == ["AAPL"]
    # SEC-13: the row carries the ingest-bound raw-item hash (to_market_event), and the
    # verified read-back returns the item still bound to it.
    assert row.content_hash == d["content_hash"]
    assert item["content_hash"] == row.content_hash


def test_resolve_raw_tamper_fails_closed(st, clock, tmp_path):
    root = tmp_path / "raw"
    res = persist_events(st, [make_dict(1)], parquet_root=root, clock=clock)
    row = res.stored[0]
    date = clock.now().date().isoformat()
    # post-ingest edit of the raw parquet bytes (below the LLM)
    p = snapshot_path(root, date, res.raw_key)
    recs = pq.read_table(p).to_pylist()
    recs[0]["item"] = recs[0]["item"].replace("Headline 1", "EVIL injected")
    p.unlink()
    pq.write_table(pa.Table.from_pylist(recs), p)
    with pytest.raises(RawIntegrityError):
        resolve_raw(root, row.raw_ref, state=st)


def test_resolve_raw_row_hash_mismatch_fails_closed(st, clock, tmp_path):
    root = tmp_path / "raw"
    res = persist_events(st, [make_dict(1)], parquet_root=root, clock=clock)
    row = res.stored[0]
    row.content_hash = "0" * 64   # simulate a mutated-domain-column attack
    with pytest.raises(RawIntegrityError):
        resolve_raw(root, row.raw_ref, state=st, row=row)


def test_resolve_raw_requires_verified_batch_entry(st, clock, tmp_path):
    root = tmp_path / "raw"
    res = persist_events(st, [make_dict(1)], parquet_root=root, clock=clock)
    row = res.stored[0]
    # a foreign audit log (different HMAC key) cannot vouch for the batch
    other = type(st).build(st.session_factory, hmac_key=b"attacker-key",
                           settings=st.settings, clock=clock)
    with pytest.raises(RawIntegrityError):
        resolve_raw(root, row.raw_ref, state=other)


def test_fallback_refs_without_parquet_root(st, clock, tmp_path):
    d = make_dict(1)
    res = persist_events(st, [d], parquet_root=None, clock=clock)
    row = res.stored[0]
    assert row.raw_ref == "alpaca:benzinga:a-1"
    assert res.raw_key is None
    with pytest.raises(RawUnavailableError):
        resolve_raw(tmp_path / "raw", row.raw_ref, state=st)


def test_symbolless_event_kept_not_dropped(st, clock, tmp_path):
    d = to_market_event(source="calendar", article_id="macro-1", symbols=[],
                        headline="macro: CPI print", summary="", ts=None,
                        event_type="calendar:macro")
    res = persist_events(st, [d], parquet_root=tmp_path / "raw", clock=clock)
    assert len(res.stored) == 1 and res.stored[0].symbol == ""
