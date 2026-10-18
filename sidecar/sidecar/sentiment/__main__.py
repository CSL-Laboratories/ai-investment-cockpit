"""Read-only sentiment commands.

`snapshot` appends one session. `study` rebuilds the fixture report and does not
call Reddit.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from sidecar.flow.config import WATCHLIST
from sidecar.sentiment.inbox import DEFAULT_INBOX, load_inbox
from sidecar.sentiment.posts import decision_session
from sidecar.sentiment.reddit import fetch_reddit, require_reddit_env
from sidecar.sentiment.score import attach_flow, score_session
from sidecar.sentiment.snapshot import append_snapshots, prior_counts
from sidecar.sentiment.study import DEFAULT_BARS, write_fixture_study


def run_snapshot(
    *,
    as_of: datetime,
    sources: set[str],
    inbox: Path,
    out: Path,
    flow_path: Path | None,
) -> list[dict]:
    posts = []
    if "reddit" in sources:
        require_reddit_env()
        posts.extend(fetch_reddit(as_of))
    if "x" in sources:
        loaded, _rejected = load_inbox(inbox, as_of)
        posts.extend(loaded)
    session = decision_session(as_of)
    rows = score_session(
        posts,
        session=session,
        prior_counts=prior_counts(out, session.isoformat()),
        universe=WATCHLIST,
    )
    if flow_path is not None:
        scan = json.loads(flow_path.read_text(encoding="utf-8"))
        rows = attach_flow(rows, scan.get("underlyings", []))
    append_snapshots(out, rows)
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Read-only social sentiment snapshots")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--as-of", required=True)
    snapshot.add_argument("--sources", default="reddit,x")
    snapshot.add_argument("--inbox", type=Path, default=DEFAULT_INBOX)
    snapshot.add_argument("--out", type=Path, required=True)
    snapshot.add_argument("--flow", type=Path, default=None)
    study = sub.add_parser("study")
    study.add_argument("--bars", type=Path, default=DEFAULT_BARS)
    study.add_argument("--out", type=Path, required=True)
    study.add_argument("--ledger", type=Path, required=True)
    study.add_argument("--snapshots", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "study":
        write_fixture_study(args.bars, args.out, args.ledger, args.snapshots)
        return
    sources = {item.strip() for item in args.sources.split(",") if item.strip()}
    unknown = sources - {"reddit", "x"}
    if unknown or not sources:
        raise SystemExit("sources must be reddit, x, or both")
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    if as_of.tzinfo is None:
        raise SystemExit("as-of must be timezone-aware")
    run_snapshot(as_of=as_of, sources=sources, inbox=args.inbox, out=args.out, flow_path=args.flow)


if __name__ == "__main__":
    main()
