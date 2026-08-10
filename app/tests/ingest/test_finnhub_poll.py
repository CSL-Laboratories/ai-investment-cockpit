"""WP3 — Finnhub poller: rate-limit never exceeded, minId cursor advances, dedup."""
from __future__ import annotations

from ingest.finnhub_poll import FinnhubPoller


def test_rate_limit_never_exceeds_60_per_minute():
    calls = {"n": 0}

    def fetch(min_id, now):
        calls["n"] += 1
        return []

    p = FinnhubPoller(fetch=fetch)
    # hammer 20 poll attempts per second across a full minute
    for i in range(1200):
        p.poll(now=i * 0.05, on_event=lambda e: None)
    assert calls["n"] <= 60 and calls["n"] >= 1        # bucket gated the firehose to <=60/min


def test_min_id_cursor_and_dedup():
    seen_min_ids = []

    def fetch(min_id, now):
        seen_min_ids.append(min_id)
        if min_id < 7:
            return [{"id": 5, "headline": "a", "summary": "s", "related": "AAPL"},
                    {"id": 7, "headline": "b", "summary": "s", "related": "MSFT,NVDA"}]
        return [{"id": 7, "headline": "b", "summary": "s", "related": "MSFT"}]  # id 7 again -> dup

    out = []
    p = FinnhubPoller(fetch=fetch)
    assert p.poll(now=1.0, on_event=out.append) == 2   # ids 5,7 (bucket starts empty -> first grant at t>=1)
    assert p.poll(now=3.0, on_event=out.append) == 0   # id 7 deduped
    assert seen_min_ids == [0, 7]                       # cursor advanced to max id seen
    assert out[1]["symbols"] == ["MSFT", "NVDA"]


def test_bad_id_skipped_not_batch_crash():
    def fetch(min_id, now):
        return [{"id": "N/A", "headline": "x"}, {"id": 9, "headline": "ok", "summary": "s"}]
    out = []
    assert FinnhubPoller(fetch=fetch).poll(now=1.0, on_event=out.append) == 1  # good one still emitted
    assert out[0]["article_id"] == "9"


def test_bucket_cannot_exceed_60_over_full_minute():
    from ingest.ratelimit import TokenBucket
    b = TokenBucket(rate_per_sec=1.0, capacity=1.0)
    assert sum(1 for i in range(0, 61) if b.allow(float(i))) <= 60   # t=0..60 inclusive


def test_poison_id_does_not_strand_cursor():
    calls = {"n": 0}

    def fetch(min_id, now):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"id": 10 ** 12, "headline": "poison", "summary": "s"}]   # absurd id
        if min_id >= 10 ** 9:
            return []                                                        # (would be stranded)
        return [{"id": min_id + 1, "headline": "real", "summary": "s"}]

    out = []
    p = FinnhubPoller(fetch=fetch)
    p.poll(now=1.0, on_event=out.append)
    assert p.poll(now=3.0, on_event=out.append) == 1   # cursor clamped -> real item still fetched


# ---- BUG-069: a failed emit is not a drop ----------------------------------------------
def test_failed_emit_rolls_back_the_key_and_holds_the_cursor():
    """The key was committed before the emit and stayed committed on failure, while a
    later success advanced the cursor past the failed id — burned in dedup, skipped by
    the cursor, never delivered. Rollback + cursor clamp make the next poll retry it."""
    items = [{"id": 100, "related": "AAPL", "headline": "h100", "datetime": 1},
             {"id": 105, "related": "MSFT", "headline": "h105", "datetime": 2}]

    def fetch(min_id, _now):
        return [i for i in items if i["id"] > min_id]

    p = FinnhubPoller(fetch)
    calls = {"n": 0}

    def flaky(event):
        calls["n"] += 1
        if event["article_id"] == "100" and calls["n"] == 1:
            raise RuntimeError("persist_events down")

    assert p.poll(1.0, flaky) == 1                     # 105 emitted; 100 failed + rolled back
    assert p.counters["emit_error"] == 1
    assert p._min_id < 100, "the cursor must never advance past a failed emit"

    delivered = []
    assert p.poll(2.0, lambda e: delivered.append(e["article_id"])) == 1
    assert delivered == ["100"], "the failed story must be re-fetched and delivered"
    assert p._min_id == 105, "once everything is handled the cursor catches up"


def test_duplicate_only_poll_still_advances_the_cursor():
    items = [{"id": 7, "related": "AAPL", "headline": "h", "datetime": 1}]

    def fetch(min_id, _now):
        return [i for i in items if i["id"] > min_id]

    p = FinnhubPoller(fetch)
    assert p.poll(1.0, lambda e: None) == 1
    assert p._min_id == 7
