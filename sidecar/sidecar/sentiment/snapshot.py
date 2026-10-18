"""Append-only sentiment snapshots.

A later file must not gain an earlier session, and a symbol-session pair is written
once. That keeps a walk-forward read from seeing a rewritten past.
"""

from __future__ import annotations

import json
from pathlib import Path

from sidecar.sentiment.posts import SentimentError


def load_snapshots(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or "session_date" not in row or "symbol" not in row:
            raise SentimentError(f"snapshot line {line_no} is missing session_date or symbol")
        rows.append(row)
    return rows


def _check_order(rows: list[dict]) -> None:
    seen: set[tuple[str, str]] = set()
    last = ""
    for row in rows:
        day = str(row["session_date"])
        key = (day, str(row["symbol"]))
        if key in seen:
            raise SentimentError("duplicate sentiment snapshot")
        if day < last:
            raise SentimentError("sentiment snapshots are not in session order")
        seen.add(key)
        last = day


def write_snapshots(path: Path, rows: list[dict]) -> None:
    _check_order(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def append_snapshots(path: Path, rows: list[dict]) -> None:
    existing = load_snapshots(path)
    if existing and rows:
        latest = max(str(row["session_date"]) for row in existing)
        if min(str(row["session_date"]) for row in rows) < latest:
            raise SentimentError("refusing to append a snapshot older than the latest stored session")
    combined = existing + rows
    _check_order(combined)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def prior_counts(path: Path, session: str) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {}
    rows = [row for row in load_snapshots(path) if str(row["session_date"]) < session]
    rows.sort(key=lambda row: (row["session_date"], row["symbol"]))
    for row in rows:
        counts.setdefault(str(row["symbol"]), []).append(int(row["mentions"]))
    return counts
