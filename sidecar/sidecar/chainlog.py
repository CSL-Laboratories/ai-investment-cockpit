"""Append-only hash-chained JSONL log.

The cockpit audit chain is not reused. This log is a sidecar evidence file. Deleting it and
starting over is an operational failure this module cannot see; tampering with an existing
file fails verification.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sidecar.jsonutil import canonical_bytes, json_ready, sha256_canonical

GENESIS = "0" * 64


class ChainError(ValueError):
    """The log is missing, reordered, or no longer matches its hashes."""


def _lock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_EX)


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_UN)


def verify_entries(entries: list[dict]) -> None:
    prev = GENESIS
    for index, entry in enumerate(entries, start=1):
        if int(entry.get("seq", -1)) != index:
            raise ChainError(f"sequence break at row {index}")
        if entry.get("prev_hash") != prev:
            raise ChainError(f"prev_hash break at seq {index}")
        body = {key: value for key, value in entry.items() if key != "entry_hash"}
        digest = sha256_canonical(body)
        if entry.get("entry_hash") != digest:
            raise ChainError(f"entry_hash mismatch at seq {index}")
        prev = digest


def read_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if text == "":
        return []
    rows = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise ChainError(f"blank line {line_no}")
        try:
            import json

            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ChainError(f"invalid JSON at line {line_no}") from exc
        if not isinstance(row, dict):
            raise ChainError(f"line {line_no} is not an object")
        rows.append(row)
    verify_entries(rows)
    return rows


class ChainLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def entries(self) -> list[dict]:
        return read_entries(self.path)

    def append(self, record: dict[str, Any]) -> dict:
        if "seq" in record or "prev_hash" in record or "entry_hash" in record:
            raise ChainError("caller records must not set chain fields")
        body_in = json_ready(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("a+b") as lock_handle:
            _lock(lock_handle)
            try:
                existing = read_entries(self.path)
                prev = existing[-1]["entry_hash"] if existing else GENESIS
                body = dict(body_in)
                body["seq"] = len(existing) + 1
                body["prev_hash"] = prev
                body["entry_hash"] = sha256_canonical(body)
                line = canonical_bytes(body) + b"\n"
                with self.path.open("ab") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
                return body
            finally:
                _unlock(lock_handle)
