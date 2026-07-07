"""WP2 — Parquet snapshots: lossless round-trip, stable + edit-sensitive content_hash,
write-once, and post-ingest edit detection via verify_snapshot."""
from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from storage.parquet_store import (
    content_hash,
    read_snapshot,
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


def test_post_ingest_edit_detected(tmp_path):
    _, h = write_snapshot(tmp_path, "2026-07-06", "AAPL", RECORDS)
    # attacker overwrites the raw file below the app with different content
    pq.write_table(pa.Table.from_pylist([{"symbol": "AAPL", "price": 999.0, "ts": "x"}]),
                   snapshot_path(tmp_path, "2026-07-06", "AAPL"))
    assert verify_snapshot(tmp_path, "2026-07-06", "AAPL", h) is False   # bound hash no longer matches
