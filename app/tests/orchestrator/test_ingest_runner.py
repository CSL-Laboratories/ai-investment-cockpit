"""RW-01 — the ingest runner: WP3 pollers -> ``workflows.events.persist_events``.

These pin the properties the runner exists to provide, each with a control so a green is
CAUSAL rather than vacuous:

  * events emitted by a REAL WP3 poller land as REAL ``MarketEvent`` rows through the REAL
    ``persist_events`` (no fake sink, no test-only injection) and the audit chain still
    verifies;
  * the sink calls ``persist_events`` ONCE PER EVENT, so a storage failure PROPAGATES into
    ``on_event`` and the poller's "a failed emit is not a drop" rollback actually fires —
    the property a per-tick buffered flush would silently destroy;
  * a duplicate is a SUCCESS (counted, not raised), so a re-published window is a no-op;
  * one sick feed never kills the others, and its sickness is REPORTED, not swallowed;
  * a mis-wired clock (``ClockContractError``) is NOT caught — it stays loud on the first tick.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from config.settings import Settings
from core.clock import FrozenClock
from ingest.calendars import CalendarPoller
from ingest.clock_contract import ClockContractError
from ingest.edgar_brew import EdgarBrewPoller
from orchestrator.breakers import BreakerEngine
from orchestrator.ingest_runner import FEED_CALENDAR, FEED_EDGAR, IngestRunner, PersistSink
from orchestrator.state import OrchestratorState
from schemas.models.market_event import MarketEvent
from storage.engine import create_all, make_engine, make_session_factory

NOW = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
CLOCK = FrozenClock(NOW)

ENTRIES = [
    {"symbol": "AAPL", "date": "2026-07-30", "note": "Q3 earnings"},
    {"symbol": "MSFT", "date": "2026-07-29", "note": "Q4 earnings"},
]


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'ingest.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=b"rw01-ingest-key", settings=Settings(),
                                   clock=CLOCK)


@pytest.fixture
def parquet_root(tmp_path):
    root = tmp_path / "raw"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _events(st) -> list[MarketEvent]:
    with st.session_factory() as s:
        return list(s.execute(select(MarketEvent).order_by(MarketEvent.id)).scalars().all())


def _runner(st, parquet_root, **kw) -> IngestRunner:
    return IngestRunner(st, parquet_root=parquet_root, clock=CLOCK,
                        monotonic=kw.pop("monotonic", lambda: 0.0), **kw)


# ---------------------------------------------------------------------------------------
# the headline: a real poller's events become real rows through the real persist path
# ---------------------------------------------------------------------------------------
def test_poll_persists_real_market_event_rows_through_persist_events(st, parquet_root):
    runner = _runner(st, parquet_root)
    runner.add_poller(FEED_CALENDAR, CalendarPoller(lambda: list(ENTRIES)))

    out = runner.poll(FEED_CALENDAR)
    assert out["status"] == "ok" and out["emitted"] == 2

    rows = _events(st)
    assert {r.symbol for r in rows} == {"AAPL", "MSFT"}
    assert all(r.source == "calendar" for r in rows)
    assert all(r.event_type == "calendar:earnings" for r in rows)
    # SEC-13/Inv 8: raw landed in parquet and the batch is audit-bound, so the chain verifies.
    assert all(r.raw_ref.startswith("parquet://") for r in rows)
    assert st.audit.verify_chain().ok


def test_second_poll_of_a_republished_window_is_a_clean_no_op(st, parquet_root):
    runner = _runner(st, parquet_root)
    runner.add_poller(FEED_CALENDAR, CalendarPoller(lambda: list(ENTRIES)))

    runner.poll(FEED_CALENDAR)
    assert runner.poll(FEED_CALENDAR)["emitted"] == 0
    assert len(_events(st)) == 2, "a re-published window duplicated rows"
    assert st.audit.verify_chain().ok


def test_sink_counts_a_db_level_duplicate_as_success_not_failure(st, parquet_root):
    """``persist_events`` de-dupes against the DB and RETURNS (never raises) — so a poller with
    a cold dedup (a restart) re-emitting an already-stored item is a clean no-op, and the
    poller correctly keeps its key."""
    sink = PersistSink(st, feed=FEED_CALENDAR, parquet_root=parquet_root, clock=CLOCK)
    poller = CalendarPoller(lambda: list(ENTRIES[:1]))
    poller.poll(0.0, sink)
    assert (sink.persisted, sink.duplicates) == (1, 0)

    fresh_poller = CalendarPoller(lambda: list(ENTRIES[:1]))     # cold dedup, same entry
    fresh_poller.poll(0.0, sink)
    assert sink.duplicates == 1
    assert len(_events(st)) == 1


# ---------------------------------------------------------------------------------------
# the load-bearing one: per-event persist keeps the poller rollback contract alive
# ---------------------------------------------------------------------------------------
def test_persist_failure_propagates_so_the_poller_rolls_back_and_retries(st, parquet_root):
    """THE reason the sink is per-event rather than per-tick.

    A per-tick buffered flush would have every ``on_event`` return BEFORE the flush failed —
    dedup keys burnt, cursors advanced past unpersisted rows, counters reporting success. Here
    the failure reaches the poller, which un-burns the key; a later healthy tick re-emits, and
    nothing is lost.
    """
    calls = {"n": 0}

    def flaky_persist(state, batch, *, parquet_root, clock=None):
        calls["n"] += 1
        raise OSError("disk full")

    sink = PersistSink(st, feed=FEED_CALENDAR, parquet_root=parquet_root, clock=CLOCK,
                       persist_fn=flaky_persist)
    poller = CalendarPoller(lambda: list(ENTRIES[:1]))

    assert poller.poll(0.0, sink) == 0
    assert calls["n"] == 1, "the sink must call persist per event, not buffer the tick"
    assert poller.counters["emit_error"] == 1
    assert poller.counters["duplicate"] == 0            # key rolled back, entry re-emittable

    healthy = PersistSink(st, feed=FEED_CALENDAR, parquet_root=parquet_root, clock=CLOCK)
    assert poller.poll(1.0, healthy) == 1, "evidence was lost across a transient outage"
    assert len(_events(st)) == 1


def test_sink_calls_persist_once_per_event_never_batched(st, parquet_root):
    batches = []

    def recording_persist(state, batch, *, parquet_root, clock=None):
        batches.append(list(batch))
        from workflows.events import persist_events

        return persist_events(state, batch, parquet_root=parquet_root, clock=clock)

    sink = PersistSink(st, feed=FEED_CALENDAR, parquet_root=parquet_root, clock=CLOCK,
                       persist_fn=recording_persist)
    CalendarPoller(lambda: list(ENTRIES)).poll(0.0, sink)

    assert len(batches) == 2, "events were batched — the rollback contract is broken"
    assert all(len(b) == 1 for b in batches)


# ---------------------------------------------------------------------------------------
# fail-soft, never fail-silent
# ---------------------------------------------------------------------------------------
def test_one_sick_feed_is_reported_and_never_kills_the_others(st, parquet_root):
    class Sick:
        def poll(self, now, on_event):
            raise RuntimeError("vendor 500")

    runner = _runner(st, parquet_root)
    runner.add_poller("sick", Sick())
    runner.add_poller(FEED_CALENDAR, CalendarPoller(lambda: list(ENTRIES)))

    out = runner.poll_all()
    assert out["sick"]["status"] == "error"
    assert "vendor 500" in out["sick"]["error"]
    assert out["sick"]["consecutive_errors"] == 1
    assert out[FEED_CALENDAR]["status"] == "ok" and out[FEED_CALENDAR]["emitted"] == 2
    assert len(_events(st)) == 2

    runner.poll("sick")
    assert runner.polls["sick"].consecutive_errors == 2, "repeated failure must be visible"


def test_a_recovering_feed_clears_its_consecutive_error_count(st, parquet_root):
    state = {"fail": True}

    class Flappy:
        def poll(self, now, on_event):
            if state["fail"]:
                raise RuntimeError("transient")
            return 0

    runner = _runner(st, parquet_root)
    runner.add_poller("flappy", Flappy())
    runner.poll("flappy")
    state["fail"] = False
    assert runner.poll("flappy")["status"] == "ok"
    assert runner.polls["flappy"].consecutive_errors == 0


def test_unknown_feed_is_reported_not_raised(st, parquet_root):
    assert _runner(st, parquet_root).poll("nope")["status"] == "not_registered"


def test_clock_contract_violation_is_never_swallowed(st, parquet_root):
    """``ingest/clock_contract.py`` exists because every softening of this produced a silently
    dead feed. A mis-wired clock must raise on the FIRST tick, through the runner."""
    runner = _runner(st, parquet_root)
    # clock=lambda: <monotonic float> is the exact historical defect.
    runner.add_poller(FEED_EDGAR, EdgarBrewPoller(lambda c, n: [], clock=lambda: 12345.0))
    with pytest.raises(ClockContractError):
        runner.poll(FEED_EDGAR)


# ---------------------------------------------------------------------------------------
# observability: a poller's own signals reach the operator, never get discarded
# ---------------------------------------------------------------------------------------
def test_status_surfaces_poller_signals_and_feed_freshness(st, parquet_root):
    class Wedged:
        counters = {"no_ticker": 3, "future_ts": 0}
        progress_signal = "NO FORWARD PROGRESS: the feed is WEDGED"
        strand_signal = None
        cursor = "2026-07-07 13:00:00.000000"

        def poll(self, now, on_event):
            return 0

    class Outcome:
        status = type("S", (), {"value": "not-run"})()
        trustworthy = False
        quiet_day = False
        reason = "the brew HAS NOT RUN; zero events does NOT mean nothing happened"

    runner = _runner(st, parquet_root)
    runner.add_poller(FEED_EDGAR, Wedged(), status_fn=lambda _day: Outcome())
    runner.poll(FEED_EDGAR)

    entry = runner.status()["feeds"][FEED_EDGAR]
    assert "WEDGED" in entry["progress_signal"]
    assert entry["counters"] == {"no_ticker": 3}       # zero counters are omitted as noise
    assert entry["cursor"] == "2026-07-07 13:00:00.000000"
    # the feed's OWN freshness verdict rides along, so 0 events is not read as a quiet day
    assert entry["feed_status"]["status"] == "not-run"
    assert entry["feed_status"]["trustworthy"] is False
    assert entry["feed_status"]["quiet_day"] is False


def test_status_of_an_unreadable_freshness_reader_fails_closed(st, parquet_root):
    def boom(_day):
        raise OSError("runs dir gone")

    runner = _runner(st, parquet_root)
    runner.add_poller(FEED_EDGAR, CalendarPoller(lambda: []), status_fn=boom)
    verdict = runner.status()["feeds"][FEED_EDGAR]["feed_status"]
    assert verdict["status"] == "unreadable" and verdict["trustworthy"] is False


def test_edgar_degradation_enters_watch_only_and_a_clean_poll_recovers_fail_closed(
    st, parquet_root,
):
    state = {"trustworthy": False}

    class Poller:
        def poll(self, now, on_event):
            return 0

    class Outcome:
        status = type("S", (), {"value": "current"})()
        quiet_day = False
        reason = "SEC source health changed"

        @property
        def trustworthy(self):
            return state["trustworthy"]

    runner = _runner(st, parquet_root)
    runner.add_poller(FEED_EDGAR, Poller(), status_fn=lambda _day: Outcome())
    first = runner.poll(FEED_EDGAR)
    assert first["feed_status"]["trustworthy"] is False
    assert BreakerEngine(st).is_watch_only(FEED_EDGAR)

    state["trustworthy"] = True
    second = runner.poll(FEED_EDGAR)
    assert second["feed_status"]["trustworthy"] is True
    assert not BreakerEngine(st).is_watch_only(FEED_EDGAR)
    assert st.audit.verify_chain().ok


# ---------------------------------------------------------------------------------------
# the push feed (blocking consumer) drives through the same sink
# ---------------------------------------------------------------------------------------
def test_stream_feed_persists_through_the_same_sink(st, parquet_root):
    from ingest.alpaca_news import AlpacaNewsConsumer

    frames = [
        {"id": 1, "symbols": ["AAPL"], "headline": "AAPL beats", "summary": "s",
         "created_at": NOW.isoformat()},
        {"id": 2, "symbols": ["MSFT"], "headline": "MSFT guides", "summary": "s",
         "created_at": NOW.isoformat()},
    ]
    runner = _runner(st, parquet_root)
    runner.add_stream("alpaca:benzinga", AlpacaNewsConsumer(lambda: iter(frames)))

    out = runner.run_stream("alpaca:benzinga")
    assert out["status"] == "ok" and out["emitted"] == 2
    rows = _events(st)
    assert {r.symbol for r in rows} == {"AAPL", "MSFT"}
    assert all(r.source == "alpaca:benzinga" for r in rows)
    assert st.audit.verify_chain().ok
    assert runner.status()["feeds"]["alpaca:benzinga"]["kind"] == "stream"
