"""WP3 — Alpaca news consumer: replay, dedup, and reconnect-with-backoff over a drop."""
from __future__ import annotations

from ingest.alpaca_news import AlpacaNewsConsumer


def _frame(i, sym="AAPL"):
    return {"id": i, "symbols": [sym], "headline": f"h{i}", "summary": f"s{i}",
            "created_at": "2026-07-06T09:30:00Z"}


def test_replay_fixture_emits_normalized_events():
    frames = [_frame(1), _frame(2), _frame(3)]
    out = []
    c = AlpacaNewsConsumer(connect=lambda: iter(frames))
    assert c.run(out.append) == 3
    assert [e["article_id"] for e in out] == ["1", "2", "3"]
    assert out[0]["source"] == "alpaca:benzinga" and out[0]["symbols"] == ["AAPL"]
    assert out[0]["event_type"] == "news"          # ordinary frame -> unaffected


# ---- Inverse Cramer: classified at THIS ingest point, before persist_events signs it ---
def test_cramer_buy_call_is_tagged_at_ingest():
    frame = {"id": 9, "symbols": ["AAPL"], "headline": "Jim Cramer Says Buy AAPL",
            "summary": "Cramer recommends buying Apple", "created_at": "2026-07-06T09:30:00Z"}
    out = []
    AlpacaNewsConsumer(connect=lambda: iter([frame])).run(out.append)
    assert out[0]["event_type"] == "cramer_call:buy"
    assert out[0]["symbols"] == ["AAPL"]


def test_cramer_sell_call_is_tagged_at_ingest():
    frame = {"id": 10, "symbols": ["TSLA"], "headline": "Cramer Says Sell TSLA", "summary": None,
            "created_at": "2026-07-06T09:30:00Z"}
    out = []
    AlpacaNewsConsumer(connect=lambda: iter([frame])).run(out.append)
    assert out[0]["event_type"] == "cramer_call:sell"


def test_ambiguous_cramer_mention_stays_ordinary_news():
    frame = {"id": 11, "symbols": ["AAPL", "MSFT"], "headline": "Cramer discusses AAPL and MSFT",
            "summary": "buy AAPL, sell MSFT", "created_at": "2026-07-06T09:30:00Z"}
    out = []
    AlpacaNewsConsumer(connect=lambda: iter([frame])).run(out.append)
    assert out[0]["event_type"] == "news"          # ambiguous (2 tickers) -> never guessed


def test_dedup_by_article_id():
    frames = [_frame(1), _frame(1), _frame(2)]
    out = []
    c = AlpacaNewsConsumer(connect=lambda: iter(frames))
    assert c.run(out.append) == 2                      # duplicate id 1 dropped


def test_reconnect_with_backoff_and_dedup_across_drop():
    # first connection yields 1,2 then drops; reconnect yields 2 (dup) + 3
    batches = [([_frame(1), _frame(2)], 1), ([_frame(2), _frame(3)], None)]
    calls = {"i": 0}
    sleeps = []

    def connect():
        idx = calls["i"]
        calls["i"] += 1
        frames, raise_after = batches[idx]

        def gen():
            for k, f in enumerate(frames):
                yield f
                if raise_after is not None and k == raise_after:
                    raise ConnectionError("dropped")
        return gen()

    out = []
    c = AlpacaNewsConsumer(connect=connect, sleep=sleeps.append, backoff=lambda n: 0.5 * n)
    assert c.run(out.append) == 3                       # 1,2 then reconnect -> 3 (2 deduped)
    assert [e["article_id"] for e in out] == ["1", "2", "3"]
    assert sleeps == [0.5]                              # backed off once before reconnect


def test_flappy_link_resets_consecutive_counter():
    # each connect delivers ONE frame then drops; with a proper reset we survive many cycles
    counter = {"i": 0}

    def connect():
        counter["i"] += 1
        fid = counter["i"]

        def gen():
            yield {"id": fid, "headline": "h", "summary": "s"}
            raise ConnectionError("flap")
        return gen()

    out = []
    c = AlpacaNewsConsumer(connect=connect, max_reconnects=3)
    assert c.run(out.append, max_frames=8) == 8         # would die at ~3 without the reset


def test_malformed_frame_is_not_a_disconnect():
    frames = [{"id": 1, "headline": {"nested": "x"}, "symbols": "AAPL"},   # non-str field
              {"id": 2, "headline": "ok", "summary": "s"}]
    out = []
    # max_reconnects=0: if the bad frame were treated as a disconnect, run() would raise
    assert AlpacaNewsConsumer(connect=lambda: iter(frames), max_reconnects=0).run(out.append) == 2
