"""Mention velocity, lexicon sentiment, and the unusual-buzz rule.

The buzz formula was fixed before the fixture returns were computed:

    buzz = mention_z * (1 + sentiment) / 2

An alert requires buzz >= 2, at least 5 mentions, and sentiment > 0.
Likes and other metrics are stored on the post and are not in the formula.
The trailing window is the previous 20 sessions of that symbol, not including
the session being scored.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sidecar.flow.score import trailing_z
from sidecar.sentiment.lexicon import lexicon_score
from sidecar.sentiment.posts import Post, decision_session
from sidecar.sentiment.tickers import ALLOWED, extract_tickers

BUZZ_MIN = 2.0
MENTION_MIN = 5
WINDOW = 20


def _fmt(value: float | None) -> float | None:
    if value is None:
        return None
    return float(format(value, ".12g"))


def score_session(
    posts: list[Post],
    *,
    session: date,
    prior_counts: dict[str, list[int]],
    universe: tuple[str, ...] = (),
) -> list[dict]:
    """Score `universe` plus any other allowlisted ticker that showed up.

    A name on `universe` with no post is a zero, so the baseline is not only the
    days the name happened to be mentioned.
    """
    grouped: dict[str, list[Post]] = defaultdict(list)
    for post in posts:
        if decision_session(post.created_at) != session:
            continue
        for symbol in extract_tickers(post.text):
            grouped[symbol].append(post)
    rows = []
    for symbol in sorted(set(grouped) | set(universe)):
        if symbol not in ALLOWED:
            continue
        mentioned = grouped.get(symbol, [])
        mentions = len(mentioned)
        if mentions == 0:
            sentiment = 0.0
        else:
            sentiment = sum(lexicon_score(post.text) for post in mentioned) / mentions
        z_value = trailing_z([float(item) for item in prior_counts.get(symbol, [])], float(mentions), window=WINDOW, minimum=WINDOW)
        buzz = None if z_value is None else z_value * (1.0 + sentiment) / 2.0
        sources: dict[str, int] = defaultdict(int)
        for post in mentioned:
            sources[post.source] += 1
        rows.append(
            {
                "session_date": session.isoformat(),
                "symbol": symbol,
                "mentions": mentions,
                "mention_z": _fmt(z_value),
                "sentiment": _fmt(sentiment),
                "buzz": _fmt(buzz),
                "flow_score": None,
                "alert": buzz is not None and buzz >= BUZZ_MIN and mentions >= MENTION_MIN and sentiment > 0,
                "sources": dict(sorted(sources.items())),
                "metrics_used_in_score": False,
            }
        )
    return rows


def walk_forward(posts: list[Post], *, universe: tuple[str, ...] = ("SPY",)) -> list[dict]:
    """Score every decision session using only posts assigned to that session or earlier."""
    sessions = sorted({decision_session(post.created_at) for post in posts})
    history: dict[str, list[int]] = defaultdict(list)
    rows: list[dict] = []
    for session in sessions:
        visible = [post for post in posts if decision_session(post.created_at) == session]
        day_rows = score_session(visible, session=session, prior_counts=history, universe=universe)
        rows.extend(day_rows)
        for row in day_rows:
            history[row["symbol"]].append(row["mentions"])
    return rows


def attach_flow(rows: list[dict], flow_underlyings: list[dict]) -> list[dict]:
    """Join on symbol and session date. Missing flow data leaves flow_score null."""
    lookup: dict[tuple[str, str], float] = {}
    for item in flow_underlyings:
        if int(item.get("alert_count") or 0) <= 0:
            continue
        score = item.get("max_contract_score")
        if score is None:
            continue
        lookup[(str(item["underlying"]), str(item["session_date"]))] = float(score)
    joined = []
    for row in rows:
        copy = dict(row)
        flow_score = lookup.get((row["symbol"], row["session_date"]))
        copy["flow_score"] = flow_score
        if flow_score is None or row["buzz"] is None:
            copy["joined_score"] = None
        else:
            copy["joined_score"] = float(format(row["buzz"] + flow_score, ".12g"))
        joined.append(copy)
    return joined
