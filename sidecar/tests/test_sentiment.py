from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from sidecar.sentiment.inbox import load_inbox
from sidecar.sentiment.lexicon import lexicon_score
from sidecar.sentiment.posts import Post, SentimentError, decision_session
from sidecar.sentiment.reddit import fetch_reddit
from sidecar.sentiment.score import attach_flow, score_session, walk_forward
from sidecar.sentiment.snapshot import append_snapshots, prior_counts
from sidecar.sentiment.tickers import extract_tickers


def _post(day: date, text: str, *, likes: int = 1, hour: int = 15, source: str = "x", post_id: str | None = None) -> Post:
    return Post(
        source=source,
        id=post_id or f"{day.isoformat()}-{text}-{likes}",
        created_at=datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc),
        author="tester",
        text=text,
        metrics={"likes": likes},
    )


def test_reddit_missing_credentials_do_not_call_the_network(monkeypatch):
    monkeypatch.delenv("SIDECAR_REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("SIDECAR_REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SIDECAR_REDDIT_USER_AGENT", raising=False)
    monkeypatch.setenv("SIDECAR_REDDIT_CLIENT_SECRET", "SECRETVALUE")

    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network")

    as_of = datetime(2024, 6, 3, 20, tzinfo=timezone.utc)
    with pytest.raises(SentimentError, match="missing") as raised:
        fetch_reddit(as_of, transport=httpx.MockTransport(boom))
    assert "SECRETVALUE" not in str(raised.value)


def test_reddit_listing_drops_posts_after_as_of(monkeypatch):
    monkeypatch.setenv("SIDECAR_REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("SIDECAR_REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SIDECAR_REDDIT_USER_AGENT", "sidecar-test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_token"):
            assert request.method == "POST"
            return httpx.Response(200, json={"access_token": "token"})
        assert request.headers["Authorization"] == "bearer token"
        return httpx.Response(
            200,
            json={
                "data": {
                    "children": [
                        {"data": {"id": "old", "created_utc": 1700000000, "author": "a", "title": "$SPY bullish", "selftext": "", "score": 2, "num_comments": 1}},
                        {"data": {"id": "new", "created_utc": 1800000000, "author": "b", "title": "$QQQ", "selftext": "", "score": 1, "num_comments": 0}},
                    ]
                }
            },
        )

    posts = fetch_reddit(datetime(2024, 1, 1, tzinfo=timezone.utc), transport=httpx.MockTransport(handler))
    assert [post.id for post in posts] == ["old"]
    assert extract_tickers(posts[0].text) == ["SPY"]


def test_ticker_filter_blocks_common_words_and_unknown_symbols():
    assert extract_tickers("I like $SPY and NVDA but IT is ALL FOR now") == ["SPY", "NVDA"]
    assert extract_tickers("$it $AI $ZZZZ") == []
    assert extract_tickers("spy") == []
    assert lexicon_score("bullish calls breakout") == 1
    assert lexicon_score("bearish puts crash") == -1
    assert lexicon_score("hello") == 0


def test_inbox_skips_bad_lines_and_future_posts(tmp_path: Path):
    inbox = tmp_path / "x_inbox"
    inbox.mkdir()
    (inbox / "posts.jsonl").write_text(
        "\n".join(
            [
                '{"id":"1","created_at":"2024-06-03T15:00:00Z","author":"a","text":"$SPY","metrics":{"likes":1}}',
                '{"id":"2","created_at":"2024-06-04T15:00:00Z","author":"b","text":"$QQQ","metrics":{"likes":1}}',
                "not-json",
                '{"id":"3","text":"missing"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    posts, rejected = load_inbox(inbox, datetime(2024, 6, 3, 16, tzinfo=timezone.utc))
    assert rejected == 2
    assert [post.id for post in posts] == ["1"]
    assert load_inbox(tmp_path / "missing", datetime(2024, 6, 3, tzinfo=timezone.utc)) == ([], 0)


def test_after_close_posts_belong_to_the_next_session():
    assert decision_session(datetime(2024, 1, 2, 21, 0, tzinfo=timezone.utc)) == date(2024, 1, 2)
    assert decision_session(datetime(2024, 1, 2, 21, 1, tzinfo=timezone.utc)) == date(2024, 1, 3)


def test_buzz_uses_prior_sessions_only_and_ignores_likes():
    start = date(2024, 1, 2)
    quiet = []
    for index in range(20):
        for copy in range(1 + (index % 2)):
            quiet.append(_post(start + timedelta(days=index), "$SPY", likes=index, post_id=f"q{index}-{copy}"))
    spike = [_post(start + timedelta(days=20), "$SPY bullish calls breakout", likes=999, post_id=f"s{i}") for i in range(8)]
    rows = walk_forward(quiet + spike, universe=("SPY",))
    assert rows[0]["mention_z"] is None
    assert rows[19]["alert"] is False
    assert rows[20]["mentions"] == 8
    assert rows[20]["alert"] is True
    quiet_same = []
    for index in range(20):
        for copy in range(1 + (index % 2)):
            quiet_same.append(_post(start + timedelta(days=index), "$SPY", likes=1, post_id=f"q{index}-{copy}"))
    again = walk_forward(quiet_same + spike, universe=("SPY",))
    assert again[20]["buzz"] == rows[20]["buzz"]
    later = walk_forward(quiet + spike + [_post(start + timedelta(days=21), "$SPY moon", post_id="future")], universe=("SPY",))
    assert later[20]["mentions"] == 8
    assert later[20]["buzz"] == rows[20]["buzz"]


def test_snapshot_refuses_backdating_and_joins_flow(tmp_path: Path):
    path = tmp_path / "snapshots.jsonl"
    session = date(2024, 6, 3)
    posts = [_post(session, "$SPY bullish", post_id=str(i)) for i in range(6)]
    prior = { "SPY": [1, 2] * 10 }
    rows = score_session(posts, session=session, prior_counts=prior, universe=("SPY",))
    flow = [
        {
            "underlying": "SPY",
            "session_date": "2024-06-03",
            "max_contract_score": 4.0,
            "alert_count": 1,
        }
    ]
    joined = attach_flow(rows, flow)
    assert joined[0]["flow_score"] == 4.0
    assert joined[0]["joined_score"] == pytest.approx(joined[0]["buzz"] + 4.0)
    append_snapshots(path, joined)
    with pytest.raises(SentimentError, match="older"):
        append_snapshots(path, [{**joined[0], "session_date": "2024-06-02"}])
    assert prior_counts(path, "2024-06-04")["SPY"] == [6]


def test_fixture_study_does_not_answer_the_market_question(tmp_path: Path):
    from sidecar.sentiment.study import write_fixture_study
    from sidecar.flow.study import DEFAULT_BARS

    report = write_fixture_study(
        DEFAULT_BARS,
        tmp_path / "sentiment.json",
        tmp_path / "ledger.jsonl",
        tmp_path / "snapshots.jsonl",
    )
    assert report["market_question_answered"] is False
    assert report["orders_submitted"] == 0
    assert report["alerts"] >= 8
    assert report["horizons"]["1"]["eval"]["status"] == "ok"
    text = (tmp_path / "snapshots.jsonl").read_text(encoding="utf-8")
    assert "SECRET" not in text
    committed = json.loads((Path(__file__).resolve().parents[1] / "examples" / "sentiment_event_study.json").read_text())
    assert committed["market_question_answered"] is False
    assert report["events_sha256"] == committed["events_sha256"]
    assert report["horizons"]["1"]["eval"]["psr"] == committed["horizons"]["1"]["eval"]["psr"]
    assert report["horizons"]["5"]["eval"]["dsr"] == committed["horizons"]["5"]["eval"]["dsr"]
