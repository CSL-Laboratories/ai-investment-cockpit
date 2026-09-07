"""RW-22: committed limits history and genuine vendor recovery (BUG-126 / BUG-131).

These compose the SQLite audit reader and real scheduler/service recovery callers.
All databases, credentials and observations are synthetic; no external service is used.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from brokers.paper_execution import PaperExecutionService
from brokers.paper_performance import PaperPerformance
from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.breakers import BreakerEngine, StaleAnalysisError
from orchestrator.ingest_runner import FEED_EDGAR, IngestRunner
from orchestrator.limits import (
    EVENT_LIMITS_CHANGE,
    current_limits,
    default_limits,
    fail_closed_limits,
)
from orchestrator.scheduler import run_market_quote_poll
from orchestrator.state import EVENT_WATCH_ONLY, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from storage.audit import audit_meta
from storage.engine import create_all, make_engine, make_session_factory


KEY = b"rw22-synthetic-audit-key"
NOW = datetime(2026, 7, 7, 14, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "HALT"))
    engine = make_engine(f"sqlite:///{(tmp_path / 'rw22.db').as_posix()}")
    create_all(engine)
    state = OrchestratorState.build(
        make_session_factory(engine), hmac_key=KEY,
        settings=Settings(_env_file=None), clock=FrozenClock(NOW),
    )
    yield state
    engine.dispose()


def _limits(st, budget):
    return st.audit.append(
        EVENT_LIMITS_CHANGE, actor="operator",
        payload={"limits": {"deep_model_daily_budget": budget}},
    )


def _forged_tail(st):
    with st.session_factory() as session, session.begin():
        head = session.execute(
            select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)
        ).scalar_one()
        session.add(AuditEntry(
            id="forged-tail", seq=head.seq + 1, prev_hash=head.entry_hash,
            entry_hash="0" * 64, event_type=EVENT_LIMITS_CHANGE, actor="operator",
            refs={}, payload={"limits": {"deep_model_daily_budget": 50},
                              "_ts": head.created_at.isoformat()},
            created_at=head.created_at,
        ))


def _database_image(st):
    with st.session_factory() as session:
        return (
            session.execute(select(AuditEntry.__table__).order_by(AuditEntry.seq)).all(),
            session.execute(select(audit_meta).order_by(audit_meta.c.k)).all(),
        )


@pytest.mark.parametrize("damage", ["delete", "event_type", "payload", "created_at"])
@pytest.mark.parametrize("forged_append", [False, True])
def test_damaged_tightening_before_surviving_head_never_loosens(st, damage, forged_append):
    _limits(st, 4)
    tightening = _limits(st, 2)
    st.audit.append("heartbeat", actor="test")
    if forged_append:
        _forged_tail(st)
    with st.session_factory() as session, session.begin():
        row = session.get(AuditEntry, tightening.id)
        if damage == "delete":
            session.delete(row)
        elif damage == "event_type":
            row.event_type = "erased_limits"
        elif damage == "payload":
            row.payload = {**row.payload, "limits": {"deep_model_daily_budget": 50}}
        else:
            row.created_at += timedelta(seconds=1)
    before = _database_image(st)
    assert not st.audit.verify_chain().ok
    assert current_limits(st) == fail_closed_limits(st.settings)
    assert _database_image(st) == before  # reading must not heal either rows or watermark


def test_forged_append_preserves_latest_intact_committed_limits(st):
    _limits(st, 4)
    _limits(st, 2)
    st.audit.append("heartbeat", actor="test")
    _forged_tail(st)
    before = _database_image(st)
    assert not st.audit.verify_chain().ok
    assert current_limits(st)["deep_model_daily_budget"] == 2
    assert _database_image(st) == before
    assert not st.audit.verify_chain().ok  # limited fallback never authorizes the whole chain


def test_forged_append_without_committed_limits_uses_tightest_floor(st):
    assert current_limits(st) == default_limits(st.settings)
    st.audit.append("heartbeat", actor="test")
    _forged_tail(st)
    assert current_limits(st) == fail_closed_limits(st.settings)


def test_deleted_tail_cannot_be_masked_by_forged_rows_above_high_water(st):
    _limits(st, 4)
    tightening = _limits(st, 2)
    _forged_tail(st)
    with st.session_factory() as session, session.begin():
        session.execute(delete(AuditEntry).where(AuditEntry.id == tightening.id))
    assert current_limits(st) == fail_closed_limits(st.settings)


@pytest.mark.parametrize("damage", ["missing", "invalid"])
def test_untrusted_high_water_never_authorizes_a_prefix(st, damage):
    _limits(st, 2)
    _forged_tail(st)
    with st.session_factory() as session, session.begin():
        if damage == "missing":
            session.execute(audit_meta.delete())
        else:
            session.execute(audit_meta.update().where(audit_meta.c.k == "head_seq_sig")
                            .values(v="invalid"))
    assert current_limits(st) == fail_closed_limits(st.settings)


def test_replayed_watermark_does_not_hide_a_surviving_signed_tightening(st):
    _limits(st, 4)
    with st.session_factory() as session:
        old_watermark = [dict(row._mapping) for row in session.execute(select(audit_meta))]
    _limits(st, 2)
    _forged_tail(st)
    with st.session_factory() as session, session.begin():
        session.execute(audit_meta.delete())
        session.execute(audit_meta.insert(), old_watermark)
    # A fresh reader must reject the signed tighter row above the replayed watermark,
    # even before that reader has learned an in-process observed-head floor.
    from storage.audit import AuditLog

    st.audit = AuditLog(st.session_factory, KEY, st.clock)
    assert current_limits(st) == fail_closed_limits(st.settings)


def test_verified_limits_selection_uses_the_same_sqlite_snapshot(st, monkeypatch):
    _limits(st, 4)
    tightening = _limits(st, 2)
    st.audit.append("heartbeat", actor="test")
    verify = st.audit.verify_committed_prefix_session

    def delete_after_verification(session):
        result = verify(session)
        assert result.ok
        # This commits while the caller's read transaction remains open (SQLite WAL).
        with st.session_factory() as writer, writer.begin():
            writer.execute(delete(AuditEntry).where(AuditEntry.id == tightening.id))
        return result

    monkeypatch.setattr(st.audit, "verify_committed_prefix_session", delete_after_verification)
    assert current_limits(st)["deep_model_daily_budget"] == 2
    monkeypatch.setattr(st.audit, "verify_committed_prefix_session", verify)
    assert current_limits(st) == fail_closed_limits(st.settings)


def _restart_with_integrity_failure(st):
    for state in ("ok", "exhausted"):
        st.audit.append(
            EVENT_WATCH_ONLY, actor="test", refs={"vendor": "claude"},
            payload={"vendor": "claude", "state": state, "generation": 0},
        )
    restarted = OrchestratorState.build(
        st.session_factory, hmac_key=KEY, settings=st.settings, clock=st.clock,
    )
    assert restarted.usage.integrity_error
    assert restarted.audit.verify_chain().ok  # semantic contradiction, valid signed ledger
    return restarted


class _HealthyQuotes:
    def poll_quotes(self):
        return {"updated": ["AAPL"], "failed": []}

    def max_quote_age_s(self, *, now):
        return 0.0


class _QuietPoller:
    def poll(self, now, on_event):
        return 0


@pytest.mark.parametrize("caller", ["market_data", FEED_EDGAR, "paper_performance"])
def test_healthy_composed_callers_cannot_recover_global_integrity_failure(st, tmp_path, caller):
    st = _restart_with_integrity_failure(st)
    before = _database_image(st)
    deliveries = []
    runner = IngestRunner(st, parquet_root=tmp_path / "raw", clock=st.clock)
    runner.add_poller(
        FEED_EDGAR, _QuietPoller(), status_fn=lambda _day: SimpleNamespace(
            status=SimpleNamespace(value="current"), trustworthy=True,
            quiet_day=True, reason="healthy source",
        ),
    )
    paper = PaperExecutionService(
        st, broker=None, gateway=None, token_registry=None, quote_fn=lambda _symbol: None,
        deliver=lambda channel, payload: deliveries.append((channel, payload)) or True,
    )
    for _ in range(2):
        if caller == "market_data":
            result = run_market_quote_poll(st, market_data=_HealthyQuotes())
            assert result["recovered"] is False
        elif caller == FEED_EDGAR:
            result = runner.poll(FEED_EDGAR)
            assert result["status"] == "ok" and result["feed_status"]["trustworthy"]
        else:
            result = paper._refresh_performance(PaperPerformance(
                True, NOW.date().isoformat(), equity_drawdown_today=Decimal("0"),
                consecutive_losses=0,
            ))
            assert result["system_events"] == 0
        breaker = BreakerEngine(st)
        assert breaker.is_watch_only() and breaker.is_watch_only(caller)
        assert st.usage.window(caller).generation == 0
        with pytest.raises(StaleAnalysisError):
            breaker.assert_fresh_analysis("claude", 0)
    assert deliveries == []
    assert _database_image(st) == before


def test_direct_recovery_cannot_bypass_integrity_refusal(st):
    st = _restart_with_integrity_failure(st)
    before = _database_image(st)
    for _ in range(2):
        event, mode = BreakerEngine(st).recover_from_watch_only("market_data", Mode.read_only)
        assert event is None and mode is Mode.read_only
    assert _database_image(st) == before
    assert st.usage.integrity_error


def test_concurrent_recovery_emits_only_one_transition(st):
    breaker = BreakerEngine(st)
    breaker.enter_watch_only("market_data", "stale quotes")
    ready = Barrier(2)

    def recover():
        ready.wait(timeout=5)
        return BreakerEngine(st).recover_from_watch_only("market_data", Mode.read_only)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: recover(), range(2)))
    assert sum(event is not None for event, _mode in results) == 1
    assert st.usage.window("market_data").generation == 2
    assert not breaker.is_watch_only("market_data")
    rows = st.audit.verified_events(EVENT_WATCH_ONLY)
    assert sum(bool(row.payload.get("recovered")) for row in rows) == 1


def test_healthy_quote_poll_recovers_once_then_stays_quiet(st):
    breaker = BreakerEngine(st)
    breaker.enter_watch_only("market_data", "stale quotes")
    assert run_market_quote_poll(st, market_data=_HealthyQuotes())["recovered"] is True
    assert run_market_quote_poll(st, market_data=_HealthyQuotes())["recovered"] is False
    assert st.usage.window("market_data").generation == 2
    assert not breaker.is_watch_only()


def test_failed_recovery_write_keeps_window_exhausted_and_retryable(st, monkeypatch):
    breaker = BreakerEngine(st)
    breaker.enter_watch_only("market_data", "stale quotes")
    window = st.usage.window("market_data")
    previous = (window.state, window.generation)
    append = st.audit.append

    def refuse_recovery(event_type, **kwargs):
        if event_type == EVENT_WATCH_ONLY and kwargs.get("payload", {}).get("recovered"):
            raise OSError("synthetic audit write failure")
        return append(event_type, **kwargs)

    monkeypatch.setattr(st.audit, "append", refuse_recovery)
    with pytest.raises(OSError, match="synthetic audit write failure"):
        breaker.recover_from_watch_only("market_data", Mode.read_only)
    assert (window.state, window.generation) == previous == ("exhausted", 1)
    assert breaker.is_watch_only("market_data")
    assert breaker.can_recover_watch_only("market_data")
    assert not any(row.payload.get("recovered")
                   for row in st.audit.verified_events(EVENT_WATCH_ONLY))

    monkeypatch.setattr(st.audit, "append", append)
    recovered, _mode = breaker.recover_from_watch_only("market_data", Mode.read_only)
    assert recovered is not None
    assert (window.state, window.generation) == ("ok", 2)
    assert sum(bool(row.payload.get("recovered"))
               for row in st.audit.verified_events(EVENT_WATCH_ONLY)) == 1
