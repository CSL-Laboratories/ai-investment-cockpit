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
import os
import tempfile
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


_UNLINK_RETRY_DELAYS = (0.01, 0.05, 0.1, 0.25, 0.5)


def content_hash(records) -> str:
    raw = json.dumps(records, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def snapshot_path(root, date: str, symbol: str) -> Path:
    return Path(root) / date / f"{symbol}.parquet"


def _read_records(path: Path) -> list[dict]:
    """Parse a byte image after its OS file handle has been deterministically closed."""
    raw = path.read_bytes()
    return pq.read_table(pa.BufferReader(raw)).to_pylist()


def _unlink_staged(path: Path) -> None:
    """Remove a staging link after short-lived Windows readers release it."""
    for delay in _UNLINK_RETRY_DELAYS:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(delay)
    path.unlink(missing_ok=True)


def write_snapshot(root, date: str, symbol: str, records: list[dict]) -> tuple[Path, str]:
    p = snapshot_path(root, date, symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(prefix=f".{p.stem}-", suffix=".parquet", dir=p.parent)
    os.close(fd)
    staged = Path(staged_name)
    try:
        pq.write_table(pa.Table.from_pylist(records), staged)
        staged_digest = content_hash(_read_records(staged))
        # A same-filesystem hard link is an atomic create-if-absent on Ubuntu, Windows,
        # and macOS.  Unlike an exists-check followed by pq.write_table, it can never
        # overwrite or bless a file raced into the authority-bearing cache path.
        try:
            os.link(staged, p)
        except FileExistsError as exc:
            raise FileExistsError(f"snapshot already exists (write-once): {p}") from exc
        if not hmac.compare_digest(
            content_hash(_read_records(p)), staged_digest
        ):
            raise RuntimeError(f"snapshot changed during publication: {p}")
    finally:
        _unlink_staged(staged)
    # BUG-094: hash what was PERSISTED, not the pre-persist Python objects. Pyarrow infers
    # one column type, so a mixed int/float column round-trips int 5 as float 5.0 — hashing
    # the inputs made a legitimately-written snapshot fail verify_snapshot later (a false
    # alarm), while a value whose ``default=str`` rendering coincides (Decimal('1.5') vs
    # float 1.5) hashed identically across a substitution. Write-hash and verify-hash now
    # both derive from the same read-back representation of the same file.
    return p, staged_digest


def read_snapshot(root, date: str, symbol: str) -> list[dict]:
    return _read_records(snapshot_path(root, date, symbol))


class SnapshotIntegrityError(RuntimeError):
    """The exact snapshot bytes read did not match their authority-bound logical hash."""


def read_verified_snapshot(
    root, date: str, symbol: str, expected_hash: str
) -> list[dict]:
    """Read one pathname once and verify the exact records returned to the caller.

    A verify-then-read sequence reopens the pathname and permits an atomic rename between
    the two opens.  Reading the bytes once pins one opened file object; parsing and hashing
    that in-memory image makes a later pathname substitution irrelevant.
    """
    records = _read_records(snapshot_path(root, date, symbol))
    if not hmac.compare_digest(content_hash(records), expected_hash):
        raise SnapshotIntegrityError(
            f"snapshot {date}/{symbol} does not match its authority-bound hash"
        )
    return records


def verify_snapshot(root, date: str, symbol: str, expected_hash: str) -> bool:
    """Re-read the persisted file and confirm its content still hashes to ``expected_hash``
    (the value bound into the audit chain at ingest). Detects any post-ingest edit."""
    try:
        read_verified_snapshot(root, date, symbol, expected_hash)
    except SnapshotIntegrityError:
        return False
    return True
