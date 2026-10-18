"""X posts dropped locally as JSONL.

Required fields on every line: id, created_at, author, text, metrics.
A malformed line is counted and skipped. A missing directory is an empty inbox.
Posts created after as_of are not returned.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sidecar.sentiment.posts import Post, SentimentError, parse_timestamp

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INBOX = ROOT / "artifacts" / "x_inbox"


def load_inbox(directory: Path, as_of: datetime) -> tuple[list[Post], int]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise SentimentError("as_of must be timezone-aware")
    if not directory.exists():
        return [], 0
    posts: list[Post] = []
    rejected = 0
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                rejected += 1
                continue
            if not isinstance(row, dict):
                rejected += 1
                continue
            try:
                post = _post_from_row(row)
            except SentimentError:
                rejected += 1
                continue
            if post.created_at > as_of:
                continue
            posts.append(post)
    return posts, rejected


def _post_from_row(row: dict) -> Post:
    post_id = row.get("id")
    author = row.get("author")
    text = row.get("text")
    metrics = row.get("metrics")
    if not isinstance(post_id, str) or not post_id:
        raise SentimentError("id")
    if not isinstance(author, str) or not author:
        raise SentimentError("author")
    if not isinstance(text, str):
        raise SentimentError("text")
    if not isinstance(metrics, dict):
        raise SentimentError("metrics")
    return Post(
        source="x",
        id=post_id,
        created_at=parse_timestamp(row.get("created_at")),
        author=author,
        text=text,
        metrics=metrics,
    )
