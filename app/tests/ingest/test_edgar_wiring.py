"""RW-11 production reachability for the in-process EDGAR extraction path."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from config.settings import Settings
from core.clock import FrozenClock
from filings.edgar.fetch import SEC_COMPANY_TICKERS_URL, SecFetcher
from filings.edgar.submissions_poll import PollBaselineStore
from filings.raw_store import RawStore
from main import build_ingest_fetchers, build_ingest_runner, compose
from orchestrator.ingest_runner import FEED_EDGAR
from orchestrator.state import OrchestratorState
from schemas.models.company import Company
from schemas.models.delta import Delta
from schemas.models.market_event import MarketEvent
from storage.engine import create_all, make_engine, make_session_factory
from tests.filings._sec_replay import FixtureTransport, VirtualTime, build_registry

UA = "Lucas Guerra lucas@example.com (personal research)"
NVDA = "0001045810"
CLOCK = FrozenClock(datetime(2026, 7, 8, 14, 0, tzinfo=UTC))


def _state(tmp_path, settings, *, clock=CLOCK):
    db = tmp_path / "cockpit.db"
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    state = OrchestratorState.build(
        sf, hmac_key=b"rw11-test-key", settings=settings, clock=clock
    )
    return db, sf, state


def _fetcher_factory():
    registry = build_registry()
    registry[SEC_COMPANY_TICKERS_URL] = (
        200,
        json.dumps({
            "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
        }).encode(),
    )

    def build():
        virtual = VirtualTime()
        return SecFetcher(
            user_agent=UA,
            rate_per_sec=2.0,
            transport=FixtureTransport(registry),
            monotonic=virtual.monotonic,
            sleep=virtual.sleep,
        )

    return build


def _fetchers(settings, db, state, tmp_path, **overrides):
    return build_ingest_fetchers(
        settings,
        session_factory=state.session_factory,
        audit_log=state.audit,
        watchlist=settings.watchlist_symbols,
        cockpit_db_path=db,
        parquet_root=tmp_path / "raw",
        edgar_clock=state.clock,
        edgar_fetcher_factory=_fetcher_factory(),
        **overrides,
    )


def test_user_agent_selects_in_process_edgar_without_brew_db(tmp_path):
    settings = Settings(sec_user_agent=UA, watchlist="NVDA")
    db, _sf, state = _state(tmp_path, settings)

    fetch = _fetchers(settings, db, state, tmp_path)[FEED_EDGAR]

    assert fetch is not None
    assert fetch.edgar_mode == "in_process"


def test_in_process_edgar_is_preferred_when_legacy_brew_is_also_configured(tmp_path):
    legacy = tmp_path / "legacy-brew.db"
    settings = Settings(
        sec_user_agent=UA, watchlist="NVDA", brew_db_path=str(legacy)
    )
    db, _sf, state = _state(tmp_path, settings)

    fetch = _fetchers(settings, db, state, tmp_path)[FEED_EDGAR]

    assert fetch.edgar_mode == "in_process"
    assert fetch.db_path == str(db)


def test_legacy_brew_remains_the_fallback_without_user_agent(tmp_path):
    legacy = tmp_path / "legacy-brew.db"
    settings = Settings(brew_db_path=str(legacy))
    db, _sf, state = _state(tmp_path, settings)

    fetch = _fetchers(settings, db, state, tmp_path)[FEED_EDGAR]

    assert fetch is not None
    assert fetch.db_path == str(legacy)
    assert not hasattr(fetch, "edgar_mode")


def test_legacy_brew_fallback_survives_blank_ua_or_empty_watchlist(tmp_path):
    legacy = tmp_path / "legacy-brew.db"
    for settings in (
        Settings(sec_user_agent="   ", watchlist="NVDA", brew_db_path=str(legacy)),
        Settings(sec_user_agent=UA, brew_db_path=str(legacy)),
    ):
        db, _sf, state = _state(tmp_path, settings)
        fetch = _fetchers(settings, db, state, tmp_path)[FEED_EDGAR]
        assert fetch.db_path == str(legacy)
        assert not hasattr(fetch, "edgar_mode")


def test_compose_reaches_in_process_edgar_when_operator_configures_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COCKPIT_SEC_USER_AGENT", UA)
    monkeypatch.setenv("COCKPIT_WATCHLIST", "NVDA")
    monkeypatch.setenv("COCKPIT_RAW_MIN_FREE_BYTES", "0")

    _state_obj, app, _services = compose(
        db_url=f"sqlite:///{(tmp_path / 'composed.db').as_posix()}",
        parquet_root=str(tmp_path / "raw-composed"),
        clock=CLOCK,
        edgar_fetcher_factory=_fetcher_factory(),
    )

    feed = app.state.ingest.polls[FEED_EDGAR]
    assert feed.poller._fetch.edgar_mode == "in_process"  # noqa: SLF001 - wiring contract


def test_unprovisioned_edgar_does_not_restrict_in_memory_composition():
    _state_obj, app, _services = compose(db_url="sqlite:///:memory:")
    assert FEED_EDGAR not in app.state.ingest.polls


def test_configured_edgar_fails_per_feed_on_in_memory_composition(monkeypatch):
    monkeypatch.setenv("COCKPIT_SEC_USER_AGENT", UA)
    monkeypatch.setenv("COCKPIT_WATCHLIST", "NVDA")

    _state_obj, app, _services = compose(db_url="sqlite:///:memory:")

    assert FEED_EDGAR not in app.state.ingest.polls


def test_in_process_edgar_caches_full_market_cik_map_for_one_day(tmp_path):
    settings = Settings(sec_user_agent=UA, watchlist="NVDA", raw_min_free_bytes=0)
    db, _sf, state = _state(tmp_path, settings)
    built = []
    base_factory = _fetcher_factory()

    def tracking_factory():
        fetcher = base_factory()
        built.append(fetcher)
        return fetcher

    fetch = build_ingest_fetchers(
        settings,
        session_factory=state.session_factory,
        audit_log=state.audit,
        watchlist=settings.watchlist_symbols,
        cockpit_db_path=db,
        parquet_root=tmp_path / "raw",
        edgar_clock=state.clock,
        edgar_fetcher_factory=tracking_factory,
    )[FEED_EDGAR]

    fetch(None, CLOCK.now().isoformat())
    fetch(None, CLOCK.now().isoformat())

    mapping_requests = [
        request
        for fetcher in built
        for request in fetcher._transport.requests  # noqa: SLF001 - wire-level request proof
        if request[1] == SEC_COMPANY_TICKERS_URL
    ]
    assert len(built) == 2, "the per-run SEC byte budget must still get a fresh fetcher"
    assert len(mapping_requests) == 1, "full-market CIK map was fetched twice in one day"


def test_composed_runner_polls_pipeline_and_emits_cited_attributed_event(tmp_path):
    settings = Settings(
        sec_user_agent=UA,
        sec_max_new_filings_per_poll=250,
        watchlist="NVDA",
        raw_min_free_bytes=0,
    )
    db, sf, state = _state(tmp_path, settings)
    # First-contact production behavior baselines history. Put the boundary immediately
    # before the recorded newest 8-K so this fixture represents the next scheduled tick.
    PollBaselineStore(sf, state.audit).establish(
        NVDA, "0001045810-26-000056", CLOCK.now()
    )
    fetchers = _fetchers(settings, db, state, tmp_path)
    runner = build_ingest_runner(
        state, parquet_root=tmp_path / "raw", fetchers=fetchers,
        settings=settings, clock=CLOCK, monotonic=lambda: 1.0,
    )

    result = runner.poll(FEED_EDGAR)

    assert result["status"] == "ok" and result["emitted"] >= 1
    with sf() as session:
        company = session.execute(select(Company)).scalar_one()
        deltas = session.execute(select(Delta)).scalars().all()
        events = session.execute(select(MarketEvent)).scalars().all()
    assert (company.ticker, company.cik) == ("NVDA", NVDA)
    assert deltas and events
    assert {event.source for event in events} == {FEED_EDGAR}
    assert {event.symbol for event in events} == {"NVDA"}
    store = RawStore(tmp_path / "raw" / "sec", min_free_bytes=0)
    assert all(store.resolve(delta.raw_ref) for delta in deltas)
    status = runner.status()["feeds"][FEED_EDGAR]["feed_status"]
    assert status["status"] == "current"
    assert status["trustworthy"] is True
    assert state.audit.verify_chain().ok


def test_unknown_watchlist_ticker_is_degraded_not_a_quiet_day(tmp_path):
    settings = Settings(sec_user_agent=UA, watchlist="AAPL", raw_min_free_bytes=0)
    db, _sf, state = _state(tmp_path, settings)
    runner = build_ingest_runner(
        state,
        parquet_root=tmp_path / "raw",
        fetchers=_fetchers(settings, db, state, tmp_path),
        settings=settings,
        clock=CLOCK,
        monotonic=lambda: 1.0,
    )

    result = runner.poll(FEED_EDGAR)
    feed_status = runner.status()["feeds"][FEED_EDGAR]["feed_status"]

    assert result["status"] == "error"
    assert feed_status["status"] == "degraded"
    assert feed_status["trustworthy"] is False
    assert feed_status["quiet_day"] is False


def test_one_extraction_failure_emits_healthy_delta_then_retries_failed_filing(
    tmp_path, monkeypatch
):
    import filings.delta as delta_module

    class MutableClock(FrozenClock):
        def advance(self, delta):
            self._at += delta  # noqa: SLF001 - purpose-built deterministic test clock

    clock = MutableClock(CLOCK.now())
    settings = Settings(
        sec_user_agent=UA,
        sec_max_new_filings_per_poll=250,
        watchlist="NVDA",
        raw_min_free_bytes=0,
    )
    db, sf, state = _state(tmp_path, settings, clock=clock)
    PollBaselineStore(sf, state.audit).establish(
        NVDA, "0001193125-26-275783", clock.now()
    )
    real_compute = delta_module.compute_deltas
    attempts = {"count": 0}

    def fail_first(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient extraction fault")
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(delta_module, "compute_deltas", fail_first)
    runner = build_ingest_runner(
        state,
        parquet_root=tmp_path / "raw",
        fetchers=_fetchers(settings, db, state, tmp_path),
        settings=settings,
        clock=clock,
        monotonic=lambda: 1.0,
    )

    partial = runner.poll(FEED_EDGAR)
    partial_status = runner.status()["feeds"][FEED_EDGAR]["feed_status"]
    assert partial["status"] == "ok" and partial["emitted"] >= 1
    assert partial_status["status"] == "degraded"
    assert partial_status["trustworthy"] is False

    clock.advance(timedelta(minutes=5))
    recovered = runner.poll(FEED_EDGAR)
    recovered_status = runner.status()["feeds"][FEED_EDGAR]["feed_status"]
    assert recovered["status"] == "ok"
    assert recovered_status["status"] in {"current", "nothing-material"}
    assert recovered_status["trustworthy"] is True
    with sf() as session:
        from schemas.models.filing_extraction_state import FilingExtractionState

        states = session.execute(select(FilingExtractionState)).scalars().all()
    retried = [row for row in states if row.attempts == 2]
    assert len(retried) == 1 and retried[0].status == "complete"
    assert state.audit.verify_chain().ok


def test_companyfacts_recovery_emits_complete_accession_once(tmp_path, monkeypatch):
    """Recovered numeric evidence must reach the event stream, not die at accession dedup."""
    import filings.edgar.companyfacts as companyfacts
    from schemas.models.filing_extraction_state import FilingExtractionState

    class MutableClock(FrozenClock):
        def advance(self, delta):
            self._at += delta  # noqa: SLF001 - purpose-built deterministic test clock

    clock = MutableClock(CLOCK.now())
    settings = Settings(
        sec_user_agent=UA,
        sec_max_new_filings_per_poll=250,
        watchlist="NVDA",
        raw_min_free_bytes=0,
    )
    db, sf, state = _state(tmp_path, settings, clock=clock)
    # Place first-contact before the recorded in-scope corpus so periodic pairs are
    # available and the companyfacts outage exercises numeric, not only 8-K text, deltas.
    PollBaselineStore(sf, state.audit).establish(
        NVDA, "0001225208-20-008929", clock.now()
    )
    runner = build_ingest_runner(
        state,
        parquet_root=tmp_path / "raw",
        fetchers=_fetchers(settings, db, state, tmp_path),
        settings=settings,
        clock=clock,
        monotonic=lambda: 1.0,
    )
    real_fetch = companyfacts.fetch_companyfacts

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("temporary companyfacts outage")

    monkeypatch.setattr(companyfacts, "fetch_companyfacts", unavailable)
    partial = runner.poll(FEED_EDGAR)
    with sf() as session:
        retry_accessions = set(session.execute(
            select(FilingExtractionState.accession_no).where(
                FilingExtractionState.status == "error"
            )
        ).scalars().all())
        partial_accessions = set(session.execute(select(Delta.accession_no)).scalars().all())

    assert partial["status"] == "ok"
    assert retry_accessions
    assert retry_accessions.isdisjoint(partial_accessions)

    monkeypatch.setattr(companyfacts, "fetch_companyfacts", real_fetch)
    clock.advance(timedelta(minutes=5))
    recovered = runner.poll(FEED_EDGAR)
    with sf() as session:
        numeric_accessions = set(session.execute(
            select(Delta.accession_no).where(Delta.kind == "numeric")
        ).scalars().all())
        remaining_errors = session.execute(
            select(FilingExtractionState).where(FilingExtractionState.status == "error")
        ).scalars().all()

    assert recovered["status"] == "ok" and recovered["emitted"] > 0
    assert numeric_accessions & retry_accessions
    assert remaining_errors == []
    assert state.audit.verify_chain().ok
