"""Walk-forward sentiment event study.

The committed run builds posts from a fixed rule on the local bar calendar. It is
not an archive of r/wallstreetbets or X. The event study reads the resulting
snapshots, not the posts a second time.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sidecar.evalstats.ledger import TrialLedger
from sidecar.flow.config import HORIZONS
from sidecar.flow.study import ROOT, event_study
from sidecar.harness.backtest import load_bars
from sidecar.jsonutil import write_json
from sidecar.sentiment.posts import Post
from sidecar.sentiment.score import walk_forward
from sidecar.sentiment.snapshot import write_snapshots

DEFAULT_BARS = ROOT / "fixtures" / "etf_daily.csv"
FIXTURE_START = 252
FIXTURE_EVERY = 30
QUESTION = "Do unusual-buzz alerts predict underlying returns at 1 and 5 sessions?"


def fixture_posts(dates: list) -> list[Post]:
    posts: list[Post] = []
    last = len(dates) - max(HORIZONS) - 1
    for index in range(FIXTURE_START, last + 1):
        offset = index - FIXTURE_START
        spike = offset % FIXTURE_EVERY == 0 and offset > 0
        count = 8 if spike else 1 + (offset % 2)
        text = "$SPY bullish calls breakout" if spike else "$SPY"
        for copy in range(count):
            moment = datetime.fromisoformat(f"{dates[index].isoformat()}T15:00:00+00:00")
            posts.append(
                Post(
                    source="x",
                    id=f"fixture-{index}-{copy}",
                    created_at=moment,
                    author="fixture",
                    text=text,
                    metrics={"likes": 1},
                )
            )
    return posts


def sentiment_event_report(bars_path: Path, *, snapshot_path: Path) -> dict:
    bars = load_bars(bars_path)
    dates = [bar.day for bar in bars["SPY"]]
    posts = fixture_posts(dates)
    rows = walk_forward(posts)
    write_snapshots(snapshot_path, rows)
    events = [
        {
            "date": row["session_date"],
            "symbol": row["symbol"],
            "score": row["buzz"],
            "source": "fixture_posts",
        }
        for row in rows
        if row["alert"]
    ]
    body = event_study(bars, events)
    return {
        "schema_version": "sidecar.sentiment.event_study.v1",
        "orders_submitted": 0,
        "market_question": QUESTION,
        "market_question_answered": False,
        "event_source": "fixture_posts",
        "reason": (
            "The posts are generated from a fixed rule on the SPY bar calendar: one or two "
            "neutral $SPY posts on most sessions, and eight bullish posts every 30th session "
            "after a 20-session baseline. This is not an r/wallstreetbets or X archive, and "
            "Reddit was not called. The statistics describe that calendar. They do not answer "
            "whether social posts predict returns."
        ),
        "fixture_rule": {
            "symbol": "SPY",
            "start_index": FIXTURE_START,
            "spike_every": FIXTURE_EVERY,
            "quiet_counts": [1, 2],
            "spike_count": 8,
            "spike_text": "$SPY bullish calls breakout",
            "generated_at_note": "timestamps are 15:00 UTC on the bar date, before the New York close",
        },
        "snapshot_rows": len(rows),
        "alerts": len(events),
        "metrics_used_in_score": False,
        "threshold_status": "not_frozen",
        **body,
    }


def write_fixture_study(bars_path: Path, out: Path, ledger_path: Path, snapshot_path: Path) -> dict:
    report = sentiment_event_report(bars_path, snapshot_path=snapshot_path)
    ledger = TrialLedger(ledger_path)
    for horizon in HORIZONS:
        ledger.append(
            kind="parameter_set",
            name=f"sentiment.fixture_posts.horizon_{horizon}",
            config={
                "source": "fixture_posts",
                "symbol": "SPY",
                "start_index": FIXTURE_START,
                "spike_every": FIXTURE_EVERY,
                "horizon": horizon,
                "entry": "adj_close_to_adj_close",
            },
        )
    write_json(out, report)
    return report
