"""Research probes against unchanged main code, NOT a green acceptance suite.

Safety assertions intentionally fail where current code admits synthetic stale orders.
All state is temporary; FakeAlpaca is the only broker, fake transports the only feeds.
Run through run_diagnostics.py from the same directory to isolate imports/configuration.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import pytest
from agent_runner.transport import ProcResult
from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.paper_execution import PaperExecutionError, PaperExecutionService
from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.breakers import BreakerEngine
from orchestrator.halt import default_halt_path, halt_is_set
from orchestrator.ingest_runner import FEED_EDGAR
from orchestrator.state import EVENT_WATCH_ONLY, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from sqlalchemy import select
from storage.engine import create_all, make_engine, make_session_factory
from tests.brokers.test_paper_execution import OPERATOR_ID, _service
from tests.orchestrator.test_ingest_runner import _runner
from tests.workflows.conftest import FakeTransport
from tests.workflows.test_agent_calls import _call

pytest_plugins = ["tests.conftest"]
KEY = b"astra-durability-synthetic-fixture"


@pytest.fixture
def st(tmp_path, monkeypatch, _no_ambient_cockpit_env):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "HALT"))
    monkeypatch.setenv("COCKPIT_AUDIT_HEAD_FILE", str(tmp_path / "audit-head.json"))
    engine = make_engine(f"sqlite:///{(tmp_path / 'diagnosis.db').as_posix()}")
    create_all(engine)
    state = OrchestratorState.build(
        make_session_factory(engine), hmac_key=KEY, settings=Settings(_env_file=None),
        clock=FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)),
    )
    yield state
    engine.dispose()


def restart(old):
    return OrchestratorState.build(
        old.session_factory, hmac_key=KEY, settings=old.settings, clock=old.clock,
        anchor_store=old.anchor_store,
    )


def rebind(old_service, state):
    registry = TokenRegistry()
    gateway = OrderGateway(
        old_service._broker, st=state, token_registry=registry,
        execution_scope="paper", require_analysis_generation=True,
    )
    return PaperExecutionService(
        state, broker=old_service._broker, gateway=gateway, token_registry=registry,
        quote_fn=old_service._quote_fn, watchlist_fn=old_service._watchlist_fn,
    )


def approve(service, scenario):
    try:
        return service.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve",
            confirm_symbol="AAPL", request_id="astra-isolated-approval",
        )["status"]
    except PaperExecutionError as exc:
        return exc.code


def snapshot(state):
    with state.session_factory() as session:
        entries = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_WATCH_ONLY)
            .order_by(AuditEntry.seq)
        ).scalars().all()
        signed = [{"vendor": r.payload.get("vendor"), "state": r.payload.get("state"),
                       "generation": r.payload.get("generation")} for r in entries]
    return {
        "watch_only": BreakerEngine(state).is_watch_only(),
        "integrity_error": bool(state.usage.integrity_error),
        "memory": {k: {"state": w.state, "generation": w.generation, "used": w.used}
                for k, w in state.usage._w.items()},
        "signed": signed, "audit_ok": state.audit.verify_chain().ok,
        "halt": halt_is_set(default_halt_path()),
    }


def observe(case, **values):
    print("OBSERVATION " + json.dumps(dict(case=case, **values), sort_keys=True))


def reject_watch_append(real_append):
    def append(event_type, *args, **kwargs):
        if event_type == EVENT_WATCH_ONLY:
            raise OSError("injected watch-only commit failure")
        return real_append(event_type, *args, **kwargs)
    return append


def test_failed_watch_only_append_must_not_revive_prior_proposal(st, monkeypatch):
    service, scenario, client = _service(st)
    with monkeypatch.context() as patch:
        patch.setattr(st.audit, "append", reject_watch_append(st.audit.append))
        with pytest.raises(OSError, match="injected"):
            BreakerEngine(st).enter_watch_only("claude", "synthetic vendor blackout")
    before = snapshot(st)
    new = restart(st)
    outcome = approve(rebind(service, new), scenario)
    observe("failed_watch_append", before=before, after=snapshot(new), outcome=outcome,
            fake_submissions=len(client.submitted))
    assert client.submitted == [], "restart revived a proposal after an uncommitted blackout"


@pytest.mark.parametrize("failure_point", ["agent_job", "watch_only"])
def test_runner_limit_persistence_gap_must_fail_closed(st, monkeypatch, failure_point):
    service, scenario, client = _service(st)
    with monkeypatch.context() as patch:
        patch.setattr("workflows.agent_calls._provisioned_secret_values", list)
        if failure_point == "agent_job":
            def broken_record(*args, **kwargs):
                raise OSError("injected agent-job commit failure")
            patch.setattr(st.audit, "record_state_change", broken_record)
        else:
            patch.setattr(st.audit, "append", reject_watch_append(st.audit.append))
        transport = FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)])
        with pytest.raises(OSError, match="injected"):
            _call(st, transport)
    before = snapshot(st)
    new = restart(st)
    outcome = approve(rebind(service, new), scenario)
    observe("runner_gap_" + failure_point, before=before, after=snapshot(new),
            outcome=outcome, fake_submissions=len(client.submitted))
    assert client.submitted == [], "runner blackout was lost between memory and signed transition"


def test_failed_feed_recovery_commit_must_keep_order_path_closed(st, monkeypatch):
    breaker = BreakerEngine(st)
    breaker.enter_watch_only(FEED_EDGAR, "synthetic SEC blackout")
    with monkeypatch.context() as patch:
        patch.setattr(st.audit, "append", reject_watch_append(st.audit.append))
        with pytest.raises(OSError, match="injected"):
            breaker.recover_from_watch_only(FEED_EDGAR, Mode.read_only)
    before = snapshot(st)
    service, scenario, client = _service(st)
    outcome = approve(service, scenario)
    observe("failed_recovery_append", before=before, outcome=outcome,
            after=snapshot(st), restarted=snapshot(restart(st)),
            fake_submissions=len(client.submitted))
    assert client.submitted == [], "memory recovery authorized an order while signed state is exhausted"


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
                except Exception as exc:  # noqa: BLE001 - preserve worker failure as evidence
                    observed["worker_error"] = type(exc).__name__
            worker = threading.Thread(target=transition)
            workers.append(worker)
            worker.start()
            worker.join(timeout=2)
            observed["committed_before_broker"] = not worker.is_alive()
            observed["at_boundary"] = snapshot(st)
        return entry

    monkeypatch.setattr(st.audit, "intent", injected_intent)
    outcome = approve(service, scenario)
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    observe("submit_intent_transition", recover=recover, **observed,
            outcome=outcome, fake_submissions=len(client.submitted), after=snapshot(st))
    assert "worker_error" not in observed
    boundary = observed["at_boundary"]
    if boundary["watch_only"] or boundary["memory"]["claude"]["generation"] != 0:
        assert client.submitted == [], "blackout/generation changed before the final broker call"


def test_sec_429_and_recovery_are_separate_from_model_generation(st, tmp_path):
    from filings.edgar.fetch import SecFairAccessHalt, SecFetcher
    from tests.ingest.test_edgar_pipeline import StatusTransport

    service, scenario, client = _service(st)
    transport = StatusTransport(429)
    fetcher = SecFetcher(user_agent="Synthetic audit fixture audit@example.invalid",
                         transport=transport)

    class Poller:
        def poll(self, now, on_event):
            fetcher.get_bytes("https://data.sec.gov/submissions/CIK0000000001.json")

    runner = _runner(st, tmp_path / "raw")
    runner.add_poller(FEED_EDGAR, Poller())
    result = runner.poll(FEED_EDGAR)
    assert result["status"] == "error" and "SecFairAccessHalt" in result["error"]
    assert len(transport.requests) == 1
    assert issubclass(SecFairAccessHalt, Exception)
    new = restart(st)
    after = rebind(service, new)
    refused = approve(after, scenario)
    before = snapshot(new)
    assert client.submitted == [] and BreakerEngine(new).is_watch_only(FEED_EDGAR)
    assert not halt_is_set(default_halt_path())
    # Same transition called by _sync_edgar_watch_only after a trustworthy poll.
    BreakerEngine(new).recover_from_watch_only(FEED_EDGAR, Mode.paper)
    outcome = approve(after, scenario)
    observe("sec_429_recovery_scope", before=before, blocked_outcome=refused,
            after=snapshot(new), recovered_outcome=outcome,
            fake_submissions=len(client.submitted))
    assert BreakerEngine(new).current_generation("claude") == 0
    assert BreakerEngine(new).current_generation(FEED_EDGAR) == 2
    # A characterization, not an assertion of desired global invalidation semantics.
    assert outcome == "submitted" and len(client.submitted) == 1


def test_failed_edgar_transition_is_not_retried_while_memory_is_exhausted(st, monkeypatch, tmp_path):
    from filings.edgar.fetch import SecFairAccessHalt

    class Poller:
        def poll(self, now, on_event):
            raise SecFairAccessHalt("https://data.sec.gov/synthetic-fixture", 429)

    runner = _runner(st, tmp_path / "raw")
    runner.add_poller(FEED_EDGAR, Poller())
    with monkeypatch.context() as patch:
        patch.setattr(st.audit, "append", reject_watch_append(st.audit.append))
        with pytest.raises(OSError, match="injected"):
            runner.poll(FEED_EDGAR)
    runner.poll(FEED_EDGAR)
    before = snapshot(st)
    after = snapshot(restart(st))
    observe("edgar_retry_suppressed", before=before, after=after)
    assert after["watch_only"], "later failed poll did not persist the memory-only blackout"


def test_invalid_ledger_restart_remains_closed_without_healing(st):
    service, scenario, client = _service(st)
    BreakerEngine(st).enter_watch_only("claude", "synthetic corruption control")
    with st.session_factory() as session, session.begin():
        row = session.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_WATCH_ONLY)).scalar_one()
        row.payload = {**row.payload, "state": "ok", "generation": 0}
    new = restart(st)
    outcome = approve(rebind(service, new), scenario)
    observe("invalid_ledger_control", after=snapshot(new), outcome=outcome,
            fake_submissions=len(client.submitted))
    assert new.usage.integrity_error and BreakerEngine(new).is_watch_only()
    assert outcome == "lineage_unverified" and client.submitted == []
    assert not new.audit.verify_chain().ok


def test_audit_tamper_during_submit_intent_must_refuse(st, monkeypatch):
    service, scenario, client = _service(st)
    original = st.audit.intent
    before_broker = {}

    def injected_intent(op, *args, **kwargs):
        entry = original(op, *args, **kwargs)
        if op == "order_submit":
            with st.session_factory() as session, session.begin():
                row = session.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().first()
                row.payload = {**row.payload, "synthetic_keyless_tamper": True}
            before_broker["audit_ok"] = st.audit.verify_chain().ok
        return entry

    monkeypatch.setattr(st.audit, "intent", injected_intent)
    outcome = approve(service, scenario)
    observe("audit_tamper_at_submit", before_broker=before_broker,
            after=snapshot(st), outcome=outcome, fake_submissions=len(client.submitted))
    assert before_broker["audit_ok"] is False
    assert client.submitted == [], "invalid audit arrived before the final broker call"


@pytest.mark.parametrize("generation,state", [(None, "exhausted"), (True, "ok"),
                                             (-1, "ok"), ("2", "ok"), (1, "unknown")])
def test_malformed_signed_restore_control(st, generation, state):
    st.audit.append(EVENT_WATCH_ONLY, refs={"vendor": "claude"},
                    payload={"vendor": "claude", "generation": generation, "state": state})
    new = restart(st)
    assert new.audit.verify_chain().ok
    assert new.usage.integrity_error and BreakerEngine(new).is_watch_only()


def test_transient_restore_read_failure_installs_sticky_sentinel(st, monkeypatch):
    from storage.audit import AuditLog

    service, scenario, client = _service(st)
    with monkeypatch.context() as patch:
        def unreadable(*args, **kwargs):
            raise OSError("injected transient audit read failure")
        patch.setattr(AuditLog, "verify_session", unreadable)
        new = restart(st)
    outcome = approve(rebind(service, new), scenario)
    observe("transient_restore_read_failure", after=snapshot(new), outcome=outcome,
            fake_submissions=len(client.submitted))
    assert new.audit.verify_chain().ok
    assert new.usage.integrity_error and BreakerEngine(new).is_watch_only()
    assert client.submitted == []


def test_mutable_usage_window_is_not_authority(st):
    from schemas.models.usage_window import UsageWindow

    BreakerEngine(st).enter_watch_only("claude", "synthetic exhaustion")
    with st.session_factory() as session, session.begin():
        session.add(UsageWindow(
            id="untrusted-window", vendor="claude", generation=0, state="ok", used=0,
            window_start=st.clock.now(), window_end=st.clock.now(),
            updated_at=st.clock.now(), created_at=st.clock.now(),
        ))
    new = restart(st)
    assert new.audit.verify_chain().ok
    assert BreakerEngine(new).is_watch_only("claude")
    assert BreakerEngine(new).current_generation("claude") == 1
