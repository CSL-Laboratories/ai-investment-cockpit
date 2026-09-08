"""BUG-159 D1-D4 durability and final broker-entry refusal contracts.

The eight named tests promote the retained Astra failures. All broker/feed/model leaves are
fake; the safety session, signed audit, paper service and OrderGateway are production code.
"""
from __future__ import annotations

import multiprocessing
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from agent_runner.transport import ProcResult
from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.paper_execution import PaperExecutionError, PaperExecutionService
from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.breakers import BreakerEngine
from orchestrator.ingest_runner import FEED_EDGAR
from orchestrator.state import EVENT_WATCH_ONLY, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from sqlalchemy import select
from storage.engine import create_all, make_engine, make_session_factory
from tests.brokers.test_paper_execution import OPERATOR_ID, _service
from tests.orchestrator.test_ingest_runner import _runner
from tests.workflows.conftest import FakeTransport
from tests.workflows.test_agent_calls import _call

KEY = b"bug159-watchonly-durability"
NOW = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path, monkeypatch, _no_ambient_cockpit_env):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "HALT"))
    engine = make_engine(f"sqlite:///{(tmp_path / 'durability.db').as_posix()}")
    create_all(engine)
    state = OrchestratorState.build(
        make_session_factory(engine),
        hmac_key=KEY,
        settings=Settings(_env_file=None),
        clock=FrozenClock(NOW),
        require_safety_session=True,
    )
    assert state.safety_session.opened and state.safety_session.accepting
    yield state
    engine.dispose()


def _restart(old):
    return OrchestratorState.build(
        old.session_factory,
        hmac_key=KEY,
        settings=old.settings,
        clock=old.clock,
        anchor_store=old.anchor_store,
        require_safety_session=True,
    )


def _rebind(old_service, state):
    registry = TokenRegistry()
    gateway = OrderGateway(
        old_service._broker,  # noqa: SLF001 - production service with a fresh process registry
        st=state,
        token_registry=registry,
        execution_scope="paper",
        require_analysis_generation=True,
    )
    return PaperExecutionService(
        state,
        broker=old_service._broker,  # noqa: SLF001
        gateway=gateway,
        token_registry=registry,
        quote_fn=old_service._quote_fn,  # noqa: SLF001
        watchlist_fn=old_service._watchlist_fn,  # noqa: SLF001
    )


def _approve(service, scenario, request_id):
    try:
        return service.decide(
            scenario.order.id,
            actor=OPERATOR_ID,
            decision="approve",
            confirm_symbol="AAPL",
            request_id=request_id,
        )["status"]
    except PaperExecutionError as exc:
        return exc.code


def _watch_events(state):
    with state.session_factory() as session:
        return session.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_WATCH_ONLY)
            .order_by(AuditEntry.seq)
        ).scalars().all()


def _reject_watch_append(real_append):
    def append(event_type, *args, **kwargs):
        if event_type == EVENT_WATCH_ONLY:
            raise OSError("injected watch-only commit failure")
        return real_append(event_type, *args, **kwargs)

    return append


def test_failed_watch_only_append_must_not_revive_prior_proposal(st, monkeypatch):
    service, scenario, client = _service(st)
    with monkeypatch.context() as patch:
        patch.setattr(st.audit, "append", _reject_watch_append(st.audit.append))
        with pytest.raises(OSError, match="injected"):
            BreakerEngine(st).enter_watch_only("claude", "synthetic vendor blackout")

    assert BreakerEngine(st).watch_only_reason() == "watch_only_persistence_failed"
    assert _approve(service, scenario, "d1-direct-current") == "watch_only"
    restarted = _restart(st)
    assert restarted.safety_session.startup_hold
    assert _approve(_rebind(service, restarted), scenario, "d1-direct-restart") == "watch_only"
    assert client.submitted == []


@pytest.mark.parametrize("failure_point", ["agent_job", "watch_only"])
def test_runner_limit_persistence_gap_must_fail_closed(st, monkeypatch, failure_point):
    service, scenario, client = _service(st)
    with monkeypatch.context() as patch:
        patch.setattr("workflows.agent_calls._provisioned_secret_values", list)
        if failure_point == "agent_job":
            patch.setattr(
                st.audit,
                "record_state_change",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    OSError("injected agent-job commit failure")
                ),
            )
        else:
            patch.setattr(st.audit, "append", _reject_watch_append(st.audit.append))
        transport = FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)])
        with pytest.raises(OSError, match="injected"):
            _call(st, transport)

    assert BreakerEngine(st).is_watch_only()
    expected = "stale_analysis" if failure_point == "agent_job" else "watch_only"
    assert _approve(service, scenario, f"d1-runner-current-{failure_point}") == expected
    restarted = _restart(st)
    assert restarted.safety_session.startup_hold
    assert _approve(
        _rebind(service, restarted), scenario, f"d1-runner-restart-{failure_point}"
    ) == "watch_only"
    assert client.submitted == []


def test_failed_edgar_transition_is_not_retried_while_memory_is_exhausted(
    st, monkeypatch, tmp_path
):
    from filings.edgar.fetch import SecFairAccessHalt

    class Poller:
        def poll(self, now, on_event):
            raise SecFairAccessHalt("https://data.sec.gov/synthetic-fixture", 429)

    runner = _runner(st, tmp_path / "raw")
    runner.add_poller(FEED_EDGAR, Poller())
    with monkeypatch.context() as patch:
        patch.setattr(st.audit, "append", _reject_watch_append(st.audit.append))
        with pytest.raises(OSError, match="injected"):
            runner.poll(FEED_EDGAR)

    assert runner.poll(FEED_EDGAR)["status"] == "error"
    rows = _watch_events(st)
    assert len(rows) == 1
    assert rows[0].payload["state"] == "exhausted"
    assert rows[0].payload["generation"] == 1
    assert rows[0].payload["transition_id"] == rows[0].refs["transition_id"]
    assert st.close_safety_session(producers_quiesced=True)
    restarted = _restart(st)
    assert restarted.safety_session.startup_hold is None
    assert BreakerEngine(restarted).is_watch_only(FEED_EDGAR)


def test_failed_feed_recovery_commit_must_keep_order_path_closed(st, monkeypatch):
    breaker = BreakerEngine(st)
    breaker.enter_watch_only(FEED_EDGAR, "synthetic SEC blackout")
    with monkeypatch.context() as patch:
        patch.setattr(st.audit, "append", _reject_watch_append(st.audit.append))
        with pytest.raises(OSError, match="injected"):
            breaker.recover_from_watch_only(FEED_EDGAR, Mode.read_only)

    assert breaker.watch_only_reason() == "watch_only_persistence_failed"
    assert st.usage.window(FEED_EDGAR).state == "exhausted"
    assert _watch_events(st)[-1].payload["state"] == "exhausted"
    service, scenario, client = _service(st)
    assert _approve(service, scenario, "d2-current") == "watch_only"
    restarted = _restart(st)
    assert _approve(_rebind(service, restarted), scenario, "d2-restart") == "watch_only"
    assert client.submitted == []


def test_failed_mode_downgrade_keeps_recovery_denied_and_retryable(st, monkeypatch):
    breaker = BreakerEngine(st)
    breaker.enter_watch_only("claude", "synthetic model blackout")
    with monkeypatch.context() as patch:
        patch.setattr(
            breaker._ladder,  # noqa: SLF001 - inject the production recovery dependency
            "recover",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("injected mode recovery failure")
            ),
        )
        with pytest.raises(OSError, match="mode recovery"):
            breaker.recover_from_watch_only("claude", Mode.auto_full)

    assert breaker.watch_only_reason() == "watch_only_persistence_failed"
    assert st.usage.window("claude").state == "exhausted"
    assert [row.payload["state"] for row in _watch_events(st)] == ["exhausted"]
    event, mode = breaker.recover_from_watch_only("claude", Mode.auto_full)
    assert event is not None and mode is Mode.read_only
    assert st.usage.window("claude").state == "ok"


@pytest.mark.parametrize("recover", [False, True])
def test_transition_during_submit_intent_must_refuse(st, monkeypatch, recover):
    service, scenario, client = _service(st)
    original = st.audit.intent
    workers = []
    observed = {}

    def injected_intent(op, *args, **kwargs):
        entry = original(op, *args, **kwargs)
        if op == "order_submit":
            def transition():
                try:
                    breaker = BreakerEngine(st)
                    breaker.enter_watch_only("claude", "synthetic concurrent limit")
                    if recover:
                        breaker.recover_from_watch_only("claude", Mode.paper)
                except BaseException as exc:  # noqa: BLE001 - preserve worker outcome
                    observed["worker_error"] = type(exc).__name__

            worker = threading.Thread(target=transition)
            workers.append(worker)
            worker.start()
            worker.join(timeout=2)
            observed["worker_waiting"] = worker.is_alive()
        return entry

    monkeypatch.setattr(st.audit, "intent", injected_intent)
    outcome = _approve(service, scenario, f"d3-{recover}")
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert "worker_error" not in observed
    assert observed["worker_waiting"] is recover
    assert outcome == "gateway_mode"
    assert client.submitted == []
    results = [
        row for row in st.audit.verified_events("result:order_submit")
        if (row.refs or {}).get("aborted") == "mode"
    ]
    assert len(results) == 1


def test_audit_tamper_during_submit_intent_must_refuse(st, monkeypatch):
    service, scenario, client = _service(st)
    original = st.audit.intent

    def injected_intent(op, *args, **kwargs):
        entry = original(op, *args, **kwargs)
        if op == "order_submit":
            with st.session_factory() as session, session.begin():
                row = session.execute(
                    select(AuditEntry).order_by(AuditEntry.seq)
                ).scalars().first()
                row.payload = {**row.payload, "synthetic_keyless_tamper": True}
            assert not st.audit.verify_chain().ok
        return entry

    monkeypatch.setattr(st.audit, "intent", injected_intent)
    assert _approve(service, scenario, "d4-tamper") == "gateway_audit_integrity"
    assert client.submitted == []
    assert BreakerEngine(st).watch_only_reason() == "final_audit_unverified"
    assert len(st.audit.unresolved_intents()) == 1


@pytest.mark.parametrize("barrier", ["risk", "pending_exposure"])
def test_denial_admitted_after_late_submit_barriers_refuses(st, monkeypatch, barrier):
    service, scenario, client = _service(st)
    if barrier == "risk":
        original = service._fresh_risk  # noqa: SLF001
        calls = 0

        def injected_risk(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 3:
                BreakerEngine(st).enter_watch_only("claude", "late risk barrier")
            return result

        monkeypatch.setattr(service, "_fresh_risk", injected_risk)
    else:
        original = service._gateway._pending_ok  # noqa: SLF001
        calls = 0

        def injected_pending(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                BreakerEngine(st).enter_watch_only("claude", "late pending barrier")
            return result

        monkeypatch.setattr(service._gateway, "_pending_ok", injected_pending)  # noqa: SLF001

    assert _approve(service, scenario, f"late-{barrier}") == "gateway_mode"
    assert client.submitted == []


def test_final_authority_read_failure_and_required_anchor_loss_refuse(st, monkeypatch):
    service, scenario, client = _service(st)
    original_intent = st.audit.intent
    original_verify = st.audit.verify_session
    armed = False

    def unreadable_after_intent(session):
        if armed:
            raise OSError("injected final snapshot read failure")
        return original_verify(session)

    def arm_after_intent(op, *args, **kwargs):
        nonlocal armed
        entry = original_intent(op, *args, **kwargs)
        if op == "order_submit":
            armed = True
        return entry

    monkeypatch.setattr(st.audit, "verify_session", unreadable_after_intent)
    monkeypatch.setattr(st.audit, "intent", arm_after_intent)
    assert _approve(service, scenario, "d4-read-failure") == "gateway_audit_integrity"
    assert client.submitted == []


def test_required_anchor_disappearing_at_final_snapshot_refuses(st):
    service, scenario, client = _service(st)

    class VanishingAnchor:
        vanished = False

        def latest(self):
            return None if self.vanished else st.audit.head()

    anchor = VanishingAnchor()
    st.anchor_store = anchor
    original = service._gateway._final_audit_authority  # noqa: SLF001

    def lose_anchor(state):
        anchor.vanished = True
        return original(state)

    service._gateway._final_audit_authority = lose_anchor  # noqa: SLF001
    assert _approve(service, scenario, "d4-anchor-loss") == "gateway_audit_integrity"
    assert client.submitted == []


def test_d4_residual_sql_commit_after_final_snapshot_is_not_claimed_atomic(st, monkeypatch):
    """B08 characterization: an unrestricted writer after the snapshot remains residual."""
    from storage.audit import AuditIntegrityError

    service, scenario, client = _service(st)
    original = service._gateway._final_audit_authority  # noqa: SLF001

    def tamper_after_verified_snapshot(state):
        result = original(state)
        assert result[0]
        with st.session_factory() as session, session.begin():
            row = session.execute(
                select(AuditEntry).order_by(AuditEntry.seq)
            ).scalars().first()
            row.payload = {**row.payload, "post_snapshot_sql_writer": True}
        return result

    monkeypatch.setattr(service._gateway, "_final_audit_authority", tamper_after_verified_snapshot)  # noqa: SLF001,E501
    with pytest.raises(AuditIntegrityError):
        service.decide(
            scenario.order.id,
            actor=OPERATOR_ID,
            decision="approve",
            confirm_symbol="AAPL",
            request_id="d4-explicit-residual",
        )
    assert len(client.submitted) == 1
    assert not st.audit.verify_chain().ok


def test_clean_session_restart_and_final_authority_positive_control_submit(st):
    service, scenario, client = _service(st)
    assert st.close_safety_session(producers_quiesced=True)
    restarted = _restart(st)
    assert restarted.safety_session.startup_hold is None
    assert _approve(_rebind(service, restarted), scenario, "positive-clean-restart") == "submitted"
    assert len(client.submitted) == 1
    assert restarted.audit.verify_chain().ok


def test_committed_transition_with_lost_ack_reconciles_exact_identity(st, monkeypatch):
    real_append = st.audit.append

    def commit_then_lose_ack(event_type, *args, **kwargs):
        row = real_append(event_type, *args, **kwargs)
        if event_type == EVENT_WATCH_ONLY:
            raise OSError("synthetic acknowledgement loss")
        return row

    with monkeypatch.context() as patch:
        patch.setattr(st.audit, "append", commit_then_lose_ack)
        with pytest.raises(OSError, match="acknowledgement"):
            BreakerEngine(st).enter_watch_only("claude", "ambiguous commit")

    breaker = BreakerEngine(st)
    assert breaker.watch_only_reason() == "watch_only_active"
    assert breaker.current_generation("claude") == 1
    rows = _watch_events(st)
    assert len(rows) == 1
    assert rows[0].payload["transition_id"] == rows[0].refs["transition_id"]
    breaker.enter_watch_only("claude", "duplicate observation coalesces")
    assert len(_watch_events(st)) == 1


def test_new_trip_supersedes_committed_recovery_before_memory_publication(st, monkeypatch):
    breaker = BreakerEngine(st)
    breaker.enter_watch_only("claude", "first denial")
    recovery_committed = threading.Event()
    allow_publication = threading.Event()
    original = BreakerEngine._committed_transition
    recovery_reconciles = 0

    def delayed_recovery_publication(self, transition):
        nonlocal recovery_reconciles
        result = original(self, transition)
        if transition.kind == "recovery":
            recovery_reconciles += 1
            if recovery_reconciles == 2:
                recovery_committed.set()
                assert allow_publication.wait(timeout=5)
        return result

    monkeypatch.setattr(BreakerEngine, "_committed_transition", delayed_recovery_publication)
    errors = []

    def recover():
        try:
            breaker.recover_from_watch_only("claude", Mode.paper)
        except BaseException as exc:  # noqa: BLE001 - worker errors fail the test
            errors.append(exc)

    worker = threading.Thread(target=recover)
    worker.start()
    assert recovery_committed.wait(timeout=5)
    BreakerEngine(st).enter_watch_only("claude", "newer admitted denial")
    allow_publication.set()
    worker.join(timeout=5)
    assert not worker.is_alive() and errors == []
    rows = _watch_events(st)
    assert [(row.payload["state"], row.payload["generation"]) for row in rows] == [
        ("exhausted", 1), ("ok", 2), ("exhausted", 3)
    ]
    assert breaker.watch_only_reason() == "watch_only_active"
    assert breaker.current_generation("claude") == 3
    assert st.usage.integrity_error is None


def test_vendor_recovery_cannot_clear_other_vendor_denial(st):
    breaker = BreakerEngine(st)
    breaker.enter_watch_only("claude", "model denied")
    breaker.enter_watch_only(FEED_EDGAR, "feed denied")
    event, _mode = breaker.recover_from_watch_only(FEED_EDGAR, Mode.paper)
    assert event is not None
    assert breaker.is_watch_only(FEED_EDGAR) is False
    assert breaker.is_watch_only("claude") is True
    assert breaker.watch_only_reason() == "watch_only_active"


def test_unproven_shutdown_and_unresolved_intent_leave_restart_held(st):
    assert st.close_safety_session(producers_quiesced=False) is False
    assert st.safety_session.close_error == "producers_not_quiescent"
    restarted = _restart(st)
    assert restarted.safety_session.startup_hold


def test_configured_blocking_stream_cannot_manufacture_clean_close(st, tmp_path):
    from orchestrator.ingest_runner import IngestRunner

    entered = threading.Event()
    release = threading.Event()

    class BlockingConsumer:
        def run(self, on_event, max_frames=None):
            del on_event, max_frames
            entered.set()
            release.wait(timeout=5)
            return 0

    runner = IngestRunner(st, parquet_root=tmp_path / "raw")
    runner.add_stream("synthetic:blocking", BlockingConsumer())
    runner.start_streams()
    assert entered.wait(timeout=5)
    assert runner.join_streams(timeout_s=0.01) is False
    assert st.close_safety_session(producers_quiesced=False) is False
    release.set()
    assert runner.join_streams(timeout_s=5)
    assert _restart(st).safety_session.startup_hold


def test_unresolved_submit_intent_prevents_clean_close(st):
    st.audit.intent("order_submit", refs={"order_id": "synthetic-unresolved"})
    assert st.close_safety_session(producers_quiesced=True) is False
    assert "unresolved submit intent" in st.safety_session.close_error
    assert _restart(st).safety_session.startup_hold


def test_legacy_nonempty_and_unknown_protocol_start_held(tmp_path):
    for suffix, event_type, payload, refs in [
        ("legacy", "legacy_event", {}, {}),
        (
            "unknown",
            "safety_session_open",
            {"version": 999, "session_id": "unknown"},
            {"session_id": "unknown"},
        ),
    ]:
        engine = make_engine(f"sqlite:///{(tmp_path / f'{suffix}.db').as_posix()}")
        create_all(engine)
        sf = make_session_factory(engine)
        legacy = OrchestratorState.build(
            sf, hmac_key=KEY, settings=Settings(_env_file=None), clock=FrozenClock(NOW)
        )
        legacy.audit.append(event_type, refs=refs, payload=payload)
        state = OrchestratorState.build(
            sf,
            hmac_key=KEY,
            settings=Settings(_env_file=None),
            clock=FrozenClock(NOW),
            require_safety_session=True,
        )
        assert state.audit.verify_chain().ok
        assert state.safety_session.startup_hold
        assert BreakerEngine(state).watch_only_reason() == "watch_only_startup_hold"
        engine.dispose()


def test_malformed_signed_transition_cannot_receive_a_clean_session_close(st):
    st.audit.append(
        EVENT_WATCH_ONLY,
        actor="synthetic-invalid-writer",
        refs={"vendor": "claude", "transition_id": "watch_malformed"},
        payload={
            "vendor": "claude",
            "state": "exhausted",
            "generation": 1,
            "transition_id": "watch_malformed",
            "transition_kind": "recovery",
            "watch_only": True,
        },
    )
    assert st.close_safety_session(producers_quiesced=True) is False
    assert "malformed watch-only transition protocol" in st.safety_session.close_error
    restarted = _restart(st)
    assert restarted.safety_session.startup_hold
    assert BreakerEngine(restarted).is_watch_only()


def test_concurrent_session_ownership_allows_exactly_one_process(tmp_path, monkeypatch):
    db_path = tmp_path / "owners.db"
    engine = make_engine(f"sqlite:///{db_path.as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    genesis = OrchestratorState.build(
        sf,
        hmac_key=KEY,
        settings=Settings(_env_file=None),
        clock=FrozenClock(NOW),
        require_safety_session=True,
    )
    assert genesis.close_safety_session(producers_quiesced=True)

    from storage.audit import AuditLog

    original = AuditLog.append_in
    barrier = threading.Barrier(2)

    def synchronized_open(self, session, event_type, actor=None, refs=None, payload=None):
        if event_type == "safety_session_open":
            barrier.wait(timeout=5)
        return original(self, session, event_type, actor=actor, refs=refs, payload=payload)

    monkeypatch.setattr(AuditLog, "append_in", synchronized_open)
    states = []

    def build_owner():
        states.append(OrchestratorState.build(
            sf,
            hmac_key=KEY,
            settings=Settings(_env_file=None),
            clock=FrozenClock(NOW),
            require_safety_session=True,
        ))

    workers = [threading.Thread(target=build_owner) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    assert len(states) == 2
    assert sum(state.safety_session.opened for state in states) == 1
    assert sum(bool(state.safety_session.startup_hold) for state in states) == 1
    engine.dispose()


def _crash_worker(db_path: str, trace_path: str, checkpoint: str) -> None:
    """Spawn-safe abrupt-exit worker; no Python cleanup/finally participates."""
    from orchestrator.breakers import BreakerEngine
    from orchestrator.state import EVENT_WATCH_ONLY, OrchestratorState
    from storage.audit import AuditLog
    from storage.engine import create_all, make_engine, make_session_factory

    trace = Path(trace_path)
    engine = make_engine(f"sqlite:///{Path(db_path).as_posix()}")
    create_all(engine)

    if checkpoint == "before_session_commit":
        def exit_before_commit(self, session, seq, *, established):
            del self, session, seq, established
            trace.write_text("before_session_commit", encoding="utf-8")
            os._exit(31)

        AuditLog._bump_hwm = exit_before_commit

    state = OrchestratorState.build(
        make_session_factory(engine),
        hmac_key=KEY,
        settings=Settings(_env_file=None),
        clock=FrozenClock(NOW),
        require_safety_session=True,
    )
    if checkpoint == "before_session_commit":
        os._exit(32)
    if checkpoint == "after_session_commit":
        trace.write_text("after_session_commit", encoding="utf-8")
        os._exit(36)

    if checkpoint == "after_denial_admission":
        def exit_before_watch_write(event_type, *args, **kwargs):
            del args, kwargs
            if event_type == EVENT_WATCH_ONLY:
                trace.write_text("after_denial_admission", encoding="utf-8")
                os._exit(33)

        state.audit.append = exit_before_watch_write
    elif checkpoint == "after_watch_flush":
        real_bump = state.audit._bump_hwm

        def exit_after_flush(session, seq, *, established):
            real_bump(session, seq, established=established)
            trace.write_text("after_watch_flush", encoding="utf-8")
            os._exit(34)

        state.audit._bump_hwm = exit_after_flush

    BreakerEngine(state).enter_watch_only("claude", "child crash checkpoint")
    os._exit(35)


@pytest.mark.parametrize(
    ("checkpoint", "exit_code"),
    [
        ("before_session_commit", 31),
        ("after_session_commit", 36),
        ("after_denial_admission", 33),
        ("after_watch_flush", 34),
    ],
)
def test_child_process_interruption_preserves_session_refusal(
    tmp_path, checkpoint, exit_code
):
    db_path = tmp_path / f"{checkpoint}.db"
    trace_path = tmp_path / f"{checkpoint}.trace"
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_worker,
        args=(str(db_path), str(trace_path), checkpoint),
    )
    process.start()
    process.join(timeout=15)
    assert not process.is_alive()
    assert process.exitcode == exit_code
    assert trace_path.read_text(encoding="utf-8") == checkpoint

    engine = make_engine(f"sqlite:///{db_path.as_posix()}")
    state = OrchestratorState.build(
        make_session_factory(engine),
        hmac_key=KEY,
        settings=Settings(_env_file=None),
        clock=FrozenClock(NOW),
        require_safety_session=True,
    )
    assert state.audit.verify_chain().ok
    if checkpoint == "before_session_commit":
        assert state.safety_session.opened
        assert state.safety_session.startup_hold is None
        assert state.close_safety_session(producers_quiesced=True)
    else:
        assert state.safety_session.startup_hold
        assert BreakerEngine(state).watch_only_reason() == "watch_only_startup_hold"
        assert _watch_events(state) == []
    engine.dispose()


def test_usage_status_separates_committed_and_failed_transition(st, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(st.audit, "append", _reject_watch_append(st.audit.append))
        with pytest.raises(OSError):
            BreakerEngine(st).enter_watch_only("claude", "status visibility")
    status = st.usage.status_snapshot()
    assert status["committed"]["claude"]["state"] == "ok"
    assert status["pending"]["claude"]["phase"] == "failed"
    assert status["pending"]["claude"]["persistence_error"].startswith("OSError:")


def test_status_database_failure_is_explicit_and_sticky(st):
    from orchestrator.app import create_app

    app = create_app(st)
    endpoint = next(route.endpoint for route in app.routes if route.path == "/status")

    def unavailable():
        raise OSError("synthetic status database outage")

    st.session_factory = unavailable
    body = endpoint()
    assert body["status"] == "unavailable"
    assert body["watch_only"] is True
    assert body["audit_chain"]["error"] == "status_database_unavailable"
    assert BreakerEngine(st).is_watch_only()


def test_status_healthy_control_separates_session_and_committed_authority(st):
    from orchestrator.app import create_app

    app = create_app(st)
    endpoint = next(route.endpoint for route in app.routes if route.path == "/status")
    body = endpoint()
    assert body["audit_chain"]["ok"] is True
    assert body["watch_only"] is False
    assert body["watch_only_authority"]["pending"] == {}
    assert body["safety_session"]["opened"] is True
    assert body["safety_session"]["startup_hold"] is None
