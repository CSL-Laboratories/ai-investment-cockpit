"""WP2 — Parquet snapshot store, keyed by date/symbol.

Raw market snapshots are stored SEPARATELY from model summaries (Invariant 8) and are
**write-once**. ``content_hash`` is a canonical hash of the logical records that the
caller records in the audit chain (SEC-13); ``verify_snapshot`` re-reads the file and
re-derives the hash, so any post-ingest edit to the raw bytes is detected below the LLM
(the grounding verifier and any reconstruction must call it before trusting raw).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def content_hash(records) -> str:
    raw = json.dumps(records, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def snapshot_path(root, date: str, symbol: str) -> Path:
    return Path(root) / date / f"{symbol}.parquet"


def write_snapshot(root, date: str, symbol: str, records: list[dict]) -> tuple[Path, str]:
    p = snapshot_path(root, date, symbol)
    if p.exists():
        raise FileExistsError(f"snapshot already exists (write-once): {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(records), p)
    return p, content_hash(records)


def read_snapshot(root, date: str, symbol: str) -> list[dict]:
    return pq.read_table(snapshot_path(root, date, symbol)).to_pylist()


def verify_snapshot(root, date: str, symbol: str, expected_hash: str) -> bool:
    """Re-read the persisted file and confirm its content still hashes to ``expected_hash``
    (the value bound into the audit chain at ingest). Detects any post-ingest edit."""
    return hmac.compare_digest(content_hash(read_snapshot(root, date, symbol)), expected_hash)
