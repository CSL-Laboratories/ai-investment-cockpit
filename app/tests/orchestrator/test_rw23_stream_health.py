"""BUG-151: live delivery health cannot confuse a failed write with a quiet feed."""
import json
import threading
from types import SimpleNamespace

import pytest

from ingest.alpaca_news import AlpacaNewsConsumer, _to_event
from ingest.calendars import CalendarPoller
from orchestrator.ingest_runner import FEED_ALPACA_NEWS, FEED_CALENDAR, IngestRunner, PersistSink
from tests.orchestrator import test_ingest_runner as support
from workflows.events import persist_events

st, parquet_root = support.st, support.parquet_root
CLOCK, ENTRIES, NOW = support.CLOCK, support.ENTRIES, support.NOW


def _frame(article_id=1, **changes):
    return {"id": article_id, "symbols": ["AAPL"], "headline": "Reported earnings",
            "summary": "Untrusted article text", "created_at": NOW.isoformat(), **changes}


def _runner(st, parquet_root, persist_fn):
    return IngestRunner(st, parquet_root=parquet_root, clock=CLOCK,
                        monotonic=lambda: 0.0, persist_fn=persist_fn)


def test_gap_survives_unrelated_news_and_clears_only_after_exact_redelivery(st, parquet_root):
    frames = [_frame()]
    failed = False

    def once(state, batch, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("PRIVATE DISK PATH and article contents must stay out of status")
        return persist_events(state, batch, **kwargs)

    runner = _runner(st, parquet_root, once)
    runner.add_stream(FEED_ALPACA_NEWS, AlpacaNewsConsumer(lambda: iter(frames)))
    first = runner.run_stream(FEED_ALPACA_NEWS)
    assert first["status"] == "degraded" and first["degraded"]
    assert first["persist_errors"] == 1 and first["unresolved_events"] == 1
    assert first["counters"] == {"frame_error": 0, "emit_error": 1}
    assert first["persisted"] == 0 and first["emitted"] == 0
    serialized = json.dumps(runner.status())
    assert "PRIVATE" not in serialized and "Untrusted article text" not in serialized

    frames[:] = [_frame(2)]
    unrelated = runner.run_stream(FEED_ALPACA_NEWS)
    assert unrelated["persisted"] == 1 and unrelated["delivery_gap"]
    assert unrelated["status"] == "degraded"

    frames[:] = [_frame()]
    recovered = runner.run_stream(FEED_ALPACA_NEWS)
    assert recovered["persisted"] == 2 and recovered["persist_errors"] == 1
    assert recovered["counters"]["emit_error"] == 1  # historical error is still visible
    assert recovered["unresolved_events"] == 0 and not recovered["delivery_gap"]
    assert recovered["status"] == "ok" and not recovered["degraded"]
    assert not runner.status()["feeds"][FEED_ALPACA_NEWS]["degraded"]


@pytest.mark.parametrize("changed", ["source", "article_id", "content"])
def test_changed_identity_never_clears_an_earlier_gap(st, parquet_root, changed):
    should_fail = True

    def persist(state, batch, **kwargs):
        if should_fail:
            raise OSError("synthetic write failure")
        return persist_events(state, batch, **kwargs)

    sink = PersistSink(st, feed=FEED_ALPACA_NEWS, parquet_root=parquet_root,
                       clock=CLOCK, persist_fn=persist)
    missing = _to_event(_frame())
    with pytest.raises(OSError):
        sink(missing)
    should_fail = False
    unrelated = _to_event(_frame(headline="A corrected story"))
    if changed == "source":
        unrelated = {**missing, "source": "finnhub"}
    elif changed == "article_id":
        unrelated = {**missing, "article_id": "another-id"}
    sink(unrelated)
    assert sink.counters()["delivery_gap"]
    assert sink.counters()["unresolved_events"] == 1


def test_commit_then_lost_ack_duplicate_can_prove_exact_gap_resolved(st, parquet_root):
    failed = False

    def commit_then_raise(state, batch, **kwargs):
        nonlocal failed
        result = persist_events(state, batch, **kwargs)
        if not failed:
            failed = True
            raise OSError("post-commit connection lost")
        return result

    sink = PersistSink(st, feed=FEED_ALPACA_NEWS, parquet_root=parquet_root,
                       clock=CLOCK, persist_fn=commit_then_raise)
    event = _to_event(_frame())
    with pytest.raises(OSError):
        sink(event)
    assert sink.counters()["delivery_gap"]
    sink(event)
    assert sink.counters()["duplicates"] == 1
    assert not sink.counters()["delivery_gap"]


def test_same_id_legacy_duplicate_cannot_claim_different_content_was_recovered(st):
    original = _to_event(_frame())
    replacement = _to_event(_frame(headline="Materially different article"))
    persist_events(st, [replacement], parquet_root=None, clock=CLOCK)
    failed = False

    def once(state, batch, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("synthetic failure")
        return persist_events(state, batch, **kwargs)

    sink = PersistSink(st, feed=FEED_ALPACA_NEWS, parquet_root=None,
                       clock=CLOCK, persist_fn=once)
    with pytest.raises(OSError):
        sink(original)
    sink(original)
    assert sink.counters()["duplicates"] == 1
    assert sink.counters()["delivery_gap"]


def test_gap_capacity_is_bounded_and_overflow_cannot_be_cleared_by_known_replays(st):
    failing = True

    def persist(*args, **kwargs):
        if failing:
            raise OSError("synthetic outage")
        return SimpleNamespace(stored=[object()], duplicates=0)

    sink = PersistSink(st, feed=FEED_ALPACA_NEWS, parquet_root=None,
                       persist_fn=persist, max_pending_gaps=2)
    events = [_to_event(_frame(i)) for i in range(3)]
    for event in events:
        with pytest.raises(OSError):
            sink(event)
    assert sink.counters()["unresolved_events"] == 2
    assert sink.counters()["persist_errors"] == 3
    assert sink.counters()["delivery_gap_overflow"]
    assert all(isinstance(token, bytes) and len(token) == 32 for token in sink._pending_gaps)
    failing = False
    for event in events:
        sink(event)
    assert sink.counters()["unresolved_events"] == 0
    assert sink.counters()["delivery_gap"] and sink.counters()["delivery_gap_overflow"]


def test_unknown_identity_failure_remains_bounded_and_cannot_be_claimed_recovered(st):
    failing = True

    def persist(*args, **kwargs):
        if failing:
            raise ValueError("SECRET error")
        return SimpleNamespace(stored=[object()], duplicates=0)

    sink = PersistSink(st, feed="test", parquet_root=None, persist_fn=persist)
    for event in ({"content": "SECRET"}, _to_event(_frame("x" * 5000))):
        with pytest.raises(ValueError):
            sink(event)
    failing = False
    sink(_to_event(_frame()))
    stats = sink.counters()
    assert stats["unidentified_delivery_gap"] and stats["delivery_gap"]
    assert stats["unresolved_events"] == 0
    assert "SECRET" not in json.dumps(stats)


def test_worker_is_alive_and_delivery_is_degraded_immediately_after_failed_write(st, parquet_root):
    observed, release = threading.Event(), threading.Event()

    def frames():
        yield _frame()
        observed.set()  # the first callback has completed before the iterator resumes
        assert release.wait(5), "test worker was not released"

    def fail(*args, **kwargs):
        raise OSError("synthetic outage")

    runner = _runner(st, parquet_root, fail)
    feed = runner.add_stream(FEED_ALPACA_NEWS, AlpacaNewsConsumer(frames))
    worker = threading.Thread(target=runner.run_stream, args=(FEED_ALPACA_NEWS,), daemon=True)
    feed.thread = worker
    worker.start()
    try:
        assert observed.wait(5)
        health = runner.status()["feeds"][FEED_ALPACA_NEWS]
        assert health["alive"] and health["status"] == "degraded"
        assert health["errors"] == 0 and health["persist_errors"] == 1
        assert health["delivery_gap"] and health["counters"]["emit_error"] == 1
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_structural_frame_loss_remains_visible_after_later_success(st, parquet_root):
    runner = _runner(st, parquet_root, persist_events)
    runner.add_stream(FEED_ALPACA_NEWS, AlpacaNewsConsumer(lambda: iter([[], _frame()])))
    result = runner.run_stream(FEED_ALPACA_NEWS)
    assert result["persisted"] == 1 and result["emitted"] == 1
    assert result["counters"]["frame_error"] == 1
    assert result["status"] == "degraded" and result["degraded"]
    assert not result["delivery_gap"]  # structural loss never produced a storage identity


@pytest.mark.parametrize("deliver", [False, True])
def test_connection_error_is_visible_until_a_clean_run_or_live_delivery(st, parquet_root, deliver):
    should_fail = True
    observed, release = threading.Event(), threading.Event()

    def frames():
        if should_fail:
            raise ConnectionError("synthetic disconnect")
        if deliver:
            yield _frame()
            observed.set()
            assert release.wait(5), "test worker was not released"

    runner = _runner(st, parquet_root, persist_events)
    feed = runner.add_stream(FEED_ALPACA_NEWS, AlpacaNewsConsumer(frames, max_reconnects=0))
    assert runner.run_stream(FEED_ALPACA_NEWS)["status"] == "error"
    health = runner.status()["feeds"][FEED_ALPACA_NEWS]
    assert health["status"] == "error" and health["degraded"] and not health["alive"]
    should_fail = False
    if deliver:
        worker = threading.Thread(target=runner.run_stream, args=(FEED_ALPACA_NEWS,), daemon=True)
        feed.thread = worker
        worker.start()
        try:
            assert observed.wait(5)
            health = runner.status()["feeds"][FEED_ALPACA_NEWS]
            assert health["alive"] and health["status"] == "ok" and not health["degraded"]
        finally:
            release.set()
            worker.join(5)
        assert not worker.is_alive()
    else:
        assert runner.run_stream(FEED_ALPACA_NEWS)["status"] == "ok"
    health = runner.status()["feeds"][FEED_ALPACA_NEWS]
    assert health["errors"] == 1 and health["last_error"]
    assert health["status"] == "ok" and not health["degraded"]


def test_caught_poller_emit_failure_is_degraded_until_the_same_event_recovers(st, parquet_root):
    failed = False
    entries = list(ENTRIES[:1])

    def once(state, batch, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("synthetic outage")
        return persist_events(state, batch, **kwargs)

    runner = _runner(st, parquet_root, once)
    runner.add_poller(FEED_CALENDAR, CalendarPoller(lambda: list(entries)))
    result = runner.poll(FEED_CALENDAR)
    assert result["status"] == "degraded" and result["persist_errors"] == 1
    assert result["counters"]["emit_error"] == 1
    assert runner.status()["feeds"][FEED_CALENDAR]["degraded"]
    entries[:] = ENTRIES[1:]
    assert runner.poll(FEED_CALENDAR, now=1)["status"] == "degraded"
    entries[:] = ENTRIES[:1]
    recovered = runner.poll(FEED_CALENDAR, now=2)
    assert recovered["status"] == "ok" and not recovered["delivery_gap"]
    assert not runner.status()["feeds"][FEED_CALENDAR]["degraded"]
