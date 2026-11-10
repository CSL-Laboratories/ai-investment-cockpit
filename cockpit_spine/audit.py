"""A small hash-chained, append-only log for the operator session transcript.

This is the spine's own record of what an operator did in one session. It is
not the cockpit audit chain (``app/storage/audit.py``), which this tree never
imports, and it grants nothing. Each record binds the hash of the one before
it, so an edit, a deletion or a reorder breaks verification.
"""

from __future__ import annotations

import copy
from typing import Any

from successor.route_consumer.profile import canonical_digest

GENESIS = "sha256:" + "0" * 64
_KEYS = frozenset({"seq", "kind", "data", "prev", "hash"})


class AuditError(Exception):
    """The chain is not intact."""


def _hash_of(record: dict[str, Any]) -> str:
    return canonical_digest({k: v for k, v in record.items() if k != "hash"})


class AuditChain:
    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    @property
    def head(self) -> str:
        return self._records[-1]["hash"] if self._records else GENESIS

    def append(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        record = {
            "seq": len(self._records) + 1,
            "kind": kind,
            "data": copy.deepcopy(data),
            "prev": self.head,
        }
        record["hash"] = _hash_of(record)
        self._records.append(record)
        return copy.deepcopy(record)

    def records(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._records)

    def __len__(self) -> int:
        return len(self._records)


def verify_chain(records: object) -> str:
    """Return the head hash of an intact chain, or raise ``AuditError``."""

    if not isinstance(records, list):
        raise AuditError("chain is not a list")
    prev = GENESIS
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict) or set(record) != _KEYS:
            raise AuditError(f"record {index}: malformed")
        if record["seq"] != index:
            raise AuditError(f"record {index}: sequence {record['seq']!r} out of order")
        if record["prev"] != prev:
            raise AuditError(f"record {index}: previous hash does not link")
        if not isinstance(record["kind"], str) or not isinstance(record["data"], dict):
            raise AuditError(f"record {index}: malformed")
        if record["hash"] != _hash_of(record):
            raise AuditError(f"record {index}: hash does not match content")
        prev = record["hash"]
    return prev
