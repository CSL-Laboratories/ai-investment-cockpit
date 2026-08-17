"""WP2 — Parquet snapshots: lossless round-trip, stable + edit-sensitive content_hash,
write-once, and post-ingest edit detection via verify_snapshot."""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from storage.parquet_store import (
    content_hash,
    read_snapshot,
    read_verified_snapshot,
    snapshot_path,
    verify_snapshot,
    write_snapshot,
)

RECORDS = [
    {"symbol": "AAPL", "price": 190.5, "ts": "2026-07-06T09:30:00Z"},
    {"symbol": "AAPL", "price": 191.0, "ts": "2026-07-06T09:31:00Z"},
]


def test_snapshot_roundtrip_and_verify(tmp_path):
    path, h = write_snapshot(tmp_path, "2026-07-06", "AAPL", RECORDS)
    assert path.exists()
    assert read_snapshot(tmp_path, "2026-07-06", "AAPL") == RECORDS      # lossless
    assert content_hash(RECORDS) == h                                     # stable
    assert verify_snapshot(tmp_path, "2026-07-06", "AAPL", h) is True     # untouched


def test_content_hash_edit_sensitive():
    assert content_hash([{"x": 1}]) != content_hash([{"x": 2}])


def test_write_once(tmp_path):
    write_snapshot(tmp_path, "2026-07-06", "AAPL", RECORDS)
    with pytest.raises(FileExistsError):
        write_snapshot(tmp_path, "2026-07-06", "AAPL", RECORDS)


def test_writer_closes_os_files_before_arrow_reads(tmp_path, monkeypatch):
    original = pq.read_table
    sources = []

    def reject_os_path(source, *args, **kwargs):
        sources.append(source)
        assert isinstance(source, pa.BufferReader)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(pq, "read_table", reject_os_path)
    write_snapshot(tmp_path, "2026-07-06", "AAPL", RECORDS)
    assert len(sources) == 2


def test_staging_cleanup_retries_a_transient_reader_lock(tmp_path, monkeypatch):
    original = Path.unlink
    blocked_attempts = 0

    def transient_lock(path, *args, **kwargs):
        nonlocal blocked_attempts
        if path.name.startswith(".AAPL-") and blocked_attempts < 2:
            blocked_attempts += 1
            raise PermissionError("simulated transient Windows reader lock")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", transient_lock)
    path, digest = write_snapshot(tmp_path, "2026-07-06", "AAPL", RECORDS)
    assert blocked_attempts == 2
    assert verify_snapshot(tmp_path, "2026-07-06", "AAPL", digest) is True
    assert path.exists()


def test_post_ingest_edit_detected(tmp_path):
    _, h = write_snapshot(tmp_path, "2026-07-06", "AAPL", RECORDS)
    # attacker overwrites the raw file below the app with different content
    pq.write_table(pa.Table.from_pylist([{"symbol": "AAPL", "price": 999.0, "ts": "x"}]),
                   snapshot_path(tmp_path, "2026-07-06", "AAPL"))
    assert verify_snapshot(tmp_path, "2026-07-06", "AAPL", h) is False   # bound hash no longer matches


def test_verified_read_consumes_the_same_opened_bytes_it_hashes(tmp_path, monkeypatch):
    path, digest = write_snapshot(tmp_path, "2026-07-06", "AAPL", RECORDS)
    replacement = tmp_path / "replacement.parquet"
    pq.write_table(pa.Table.from_pylist([{"symbol": "AAPL", "price": 999.0, "ts": "x"}]),
                   replacement)
    original = pq.read_table
    swapped = False

    def swap_after_open(source, *args, **kwargs):
        nonlocal swapped
        if isinstance(source, pa.BufferReader) and not swapped:
            swapped = True
            replacement.replace(path)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(pq, "read_table", swap_after_open)
    assert read_verified_snapshot(tmp_path, "2026-07-06", "AAPL", digest) == RECORDS
    assert read_snapshot(tmp_path, "2026-07-06", "AAPL") != RECORDS


# ---- BUG-094: one serialization, both sides --------------------------------------------
def test_mixed_int_float_column_still_verifies(tmp_path):
    """Pyarrow infers ONE column type: int 5 in a float column round-trips as 5.0. Hashing
    the pre-persist objects made this legitimately-written snapshot FAIL verification."""
    from storage.parquet_store import verify_snapshot, write_snapshot

    records = [{"symbol": "AAPL", "px": 5}, {"symbol": "MSFT", "px": 5.5}]
    _, digest = write_snapshot(tmp_path, "2026-08-10", "MIX", records)
    assert verify_snapshot(tmp_path, "2026-08-10", "MIX", digest) is True
