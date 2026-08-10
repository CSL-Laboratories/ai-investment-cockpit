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


# ---- BUG-069: a failed emit is not a drop (streaming side) -----------------------------
def test_failed_emit_rolls_back_the_key_so_a_reseen_frame_can_emit():
    frame = {"id": 42, "headline": "h", "created_at": "2026-07-07T14:00:00Z",
             "symbols": ["AAPL"], "summary": "s"}
    batches = [[frame], [frame]]                        # the frame is seen again later

    def connect():
        return iter(batches.pop(0))

    consumer = AlpacaNewsConsumer(connect)
    calls = {"n": 0}

    def flaky(event):
        calls["n"] += 1
        raise RuntimeError("persist_events down")

    assert consumer.run(flaky, max_frames=1) == 0       # emit failed
    assert consumer.counters["emit_error"] == 1

    delivered = []
    assert consumer.run(lambda e: delivered.append(e["article_id"]), max_frames=1) == 1
    assert delivered == ["42"], "the rolled-back key must let the re-seen frame emit"


def test_a_structurally_bad_frame_is_counted_and_commits_nothing():
    bad = "not-a-dict-frame"                            # msg.get raises AttributeError
    consumer = AlpacaNewsConsumer(lambda: iter([bad]))
    assert consumer.run(lambda e: None, max_frames=1) == 0
    assert consumer.counters["frame_error"] == 1
    assert consumer.counters["emit_error"] == 0
