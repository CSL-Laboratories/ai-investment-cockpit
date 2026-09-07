"""Offline audit probes. All databases/keys are synthetic; no incident DB is opened.

Strict xfails encode desired behavior that this checkout does not yet provide.
Passing xfails are reproductions, never package acceptance evidence.
Run from app/: ../.venv/bin/python -m pytest -q -rx /tmp/astra-concurrency-20260906/test_probes.py
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, select

from core.clock import default_clock
from schemas.models.audit_entry import AuditEntry
from schemas.models.mode_state import ModeState
from storage.audit import AuditLog, audit_meta
from storage.engine import create_all, make_engine, make_session_factory


@pytest.fixture
def env(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'probe.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    first, second = AuditLog(sf, b"synthetic-audit-key"), AuditLog(sf, b"synthetic-audit-key")
    yield SimpleNamespace(engine=engine, sf=sf, first=first, second=second)
    engine.dispose()


@pytest.mark.xfail(strict=True, reason="healthy concurrent genesis is mislabeled tamper")
def test_concurrent_genesis_should_retry_not_report_tamper(env, monkeypatch):
    original = env.first._read_hwm
    injected = False

    def read(session):
        nonlocal injected
        value = original(session)
        if not injected:
            injected = True
            assert value is None
            env.second.append("concurrent-genesis")
        return value

    monkeypatch.setattr(env.first, "_read_hwm", read)
    try:
        env.first.append("loser")
    finally:
        assert injected
        assert env.second.verify_chain().ok


@pytest.mark.xfail(strict=True, reason="verification promotes uncommitted rows into erasure floor")
def test_rollback_after_in_transaction_verification_should_not_poison_floor(env):
    env.first.append("seed")
    with env.sf() as session:
        env.first.append_in(session, "later-rolled-back")
        assert env.first.verify_session(session).ok
        session.rollback()
    assert env.second.verify_chain().ok
    assert env.first.verify_chain().ok


@pytest.mark.xfail(strict=True, reason="append_in commits do not advance erasure floor")
def test_committed_append_in_should_detect_total_erasure(env):
    with env.sf() as session, session.begin():
        env.first.append_in(session, "committed")
    with env.sf() as session, session.begin():
        session.execute(delete(AuditEntry))
        session.execute(audit_meta.delete())
    assert not env.first.verify_chain().ok


@pytest.mark.xfail(strict=True, reason="caller-owned older snapshot compared with newer process floor")
def test_valid_older_caller_snapshot_should_not_be_labeled_erasure(env):
    env.first.append("seed")
    with env.sf() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN")
        session.execute(select(AuditEntry)).all()
        env.first.append("new-head")
        result = env.first.verify_session(session)
        assert result.ok, result.error


@pytest.mark.xfail(strict=True, reason="completeness reads coverage and model rows from different snapshots")
def test_atomic_state_commit_should_never_appear_uncovered(env):
    env.first.append("seed")
    injected = False

    def interleave(execute_state):
        nonlocal injected
        if not injected and execute_state.is_select and any(
            d.get("entity") is ModeState for d in execute_state.statement.column_descriptions
        ):
            injected = True
            env.second.record_state_change(
                ModeState(id="covered", mode="paper", created_at=default_clock.now()),
                "mode_change",
            )

    event.listen(env.sf, "do_orm_execute", interleave)
    try:
        uncovered = env.first.verify_completeness(ModeState)
    finally:
        event.remove(env.sf, "do_orm_execute", interleave)
    assert injected and env.second.verify_chain().ok
    assert uncovered == [], uncovered


def test_state_and_audit_hwm_roll_back_on_hwm_write_fault(env, monkeypatch):
    env.first.append("seed")
    before = env.first.head()
    original = env.first._bump_hwm

    def fault(session, seq):
        original(session, seq)
        raise RuntimeError("synthetic post-HWM failure")

    monkeypatch.setattr(env.first, "_bump_hwm", fault)
    with pytest.raises(RuntimeError, match="synthetic"):
        env.first.record_state_change(
            ModeState(id="must-rollback", mode="paper", created_at=default_clock.now()),
            "mode_change",
        )
    assert env.first.head() == before
    assert env.first.verify_chain().ok
    with env.sf() as session:
        assert session.get(ModeState, "must-rollback") is None


def test_lock_inversion_forces_busy_retry_with_same_audit_object(env, monkeypatch):
    # Reduces only this synthetic DB's wait, so the deterministic probe stays bounded.
    @event.listens_for(env.engine, "checkout")
    def timeout(connection, *_):
        connection.execute("PRAGMA busy_timeout=100")

    env.first.append("seed")
    has_audit_lock = threading.Event()
    errors, attempts = [], []
    original = env.first._read_hwm

    def signal_lock(session):
        if threading.current_thread().name == "standalone-probe":
            attempts.append(1)
            has_audit_lock.set()
        return original(session)

    monkeypatch.setattr(env.first, "_read_hwm", signal_lock)

    def standalone():
        try:
            env.first.append("standalone")
        except Exception as exc:
            errors.append(exc)

    with env.sf() as session, session.begin():
        session.add(ModeState(id="state", mode="paper", created_at=default_clock.now()))
        session.flush()  # Own SQLite writer lock before entering append_in.
        worker = threading.Thread(target=standalone, name="standalone-probe", daemon=True)
        worker.start()
        assert has_audit_lock.wait(3)
        env.first.append_in(session, "state", refs={"model":"mode_state","row_id":"state"})
    worker.join(5)
    assert not worker.is_alive() and not errors
    # One initial read, then at least the retried read + successful _bump_hwm read.
    assert len(attempts) >= 3, attempts
    assert env.first.verify_chain().ok


@pytest.mark.xfail(strict=True, reason="post_head_anchor samples a different unverified head after verification")
def test_anchor_must_publish_the_verified_snapshot_head(env, monkeypatch):
    from bridge.anchor import InMemoryAnchorStore, post_head_anchor

    verified_head = env.first.append("seed")
    original = env.first.verify_chain

    def interleave():
        result = original()
        # Keyless mutation, confined to this disposable DB, after verification returns.
        with env.sf() as session, session.begin():
            tail = session.get(AuditEntry, verified_head.id)
            tail.entry_hash = "f" * 64
        return result

    monkeypatch.setattr(env.first, "verify_chain", interleave)
    store = InMemoryAnchorStore()
    state = SimpleNamespace(audit=env.first, anchor_store=store)
    published = post_head_anchor(state, record=False)
    assert published == (verified_head.seq, verified_head.entry_hash)


@pytest.mark.xfail(strict=True, reason="concurrent key get-or-create returns different keys")
def test_first_boot_key_creation_should_return_one_shared_key(monkeypatch):
    from main import _hmac_key
    from core import secrets

    state = {}
    both_read_absent = threading.Barrier(2)
    first_finished = threading.Event()
    outputs, errors = {}, []
    calls = threading.local()

    def get(_name):
        count = getattr(calls, "count", 0)
        calls.count = count + 1
        if count == 0:
            both_read_absent.wait(3)
            return None
        return secrets.Redacted(state["value"])

    def put(_name, value):
        if threading.current_thread().name == "second-boot":
            assert first_finished.wait(3)
        state["value"] = value

    monkeypatch.setattr(secrets, "get_secret", get)
    monkeypatch.setattr(secrets, "set_secret", put)

    def boot():
        name = threading.current_thread().name
        try:
            outputs[name] = _hmac_key()
        except Exception as exc:
            errors.append(exc)
        finally:
            if name == "first-boot":
                first_finished.set()

    threads = [threading.Thread(target=boot, name=name, daemon=True)
               for name in ("first-boot", "second-boot")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not errors and all(not t.is_alive() for t in threads)
    # Never print generated test key material, even though no OS keyring is involved.
    assert len(outputs) == 2
    same_key = outputs["first-boot"] == outputs["second-boot"]
    assert same_key, "first boot returned distinct keys"


@pytest.mark.xfail(strict=True, reason="multi-CIK completion rollback poisons shared verifier floor")
def test_real_poll_completion_rollback_should_not_poison_shared_audit(env, monkeypatch):
    from ingest.edgar_pipeline import _record_poll_completions

    env.first.append("seed")
    original = env.first.append_in
    calls = 0

    def second_cik_fault(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic second-CIK failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(env.first, "append_in", second_cik_fault)
    with pytest.raises(RuntimeError, match="second-CIK"):
        _record_poll_completions(
            env.sf, env.first, default_clock, ["1", "2"], process_epoch="synthetic"
        )
    assert calls == 2
    assert env.second.verify_chain().ok and env.second.head()[0] == 1
    result = env.first.verify_chain()
    assert result.ok, result.error


@pytest.mark.xfail(strict=True, reason="total erase plus ordinary refill can replace observed history")
def test_total_erase_then_refill_must_never_regreen_in_same_process(env):
    for _ in range(3):
        env.first.append("original")
    assert env.first.verify_chain().ok
    with env.sf() as session, session.begin():
        session.execute(delete(AuditEntry))
        session.execute(audit_meta.delete())
    assert not env.first.verify_chain().ok
    for _ in range(3):
        env.first.append("replacement")
    assert not env.first.verify_chain().ok


@pytest.mark.xfail(strict=True, reason="recovery and trip can commit generation history out of order")
def test_concurrent_watch_only_transitions_should_restore_without_semantic_fork(env, monkeypatch):
    from config.settings import Settings
    from orchestrator.breakers import BreakerEngine
    from orchestrator.state import OrchestratorState, _restore_usage_tracker, EVENT_WATCH_ONLY

    state = OrchestratorState.build(env.sf, hmac_key=b"synthetic-audit-key", settings=Settings())
    breakers = BreakerEngine(state)
    original = state.audit.append
    injected = False

    def reorder(event_type, *args, **kwargs):
        nonlocal injected
        if event_type == EVENT_WATCH_ONLY and not injected:
            injected = True
            breakers.enter_watch_only("vendor", "second")
        return original(event_type, *args, **kwargs)

    monkeypatch.setattr(state.audit, "append", reorder)
    breakers.enter_watch_only("vendor", "first")
    assert state.audit.verify_chain().ok
    restored = _restore_usage_tracker(env.sf, state.audit)
    assert restored.integrity_error is None, restored.integrity_error


@pytest.mark.xfail(strict=True, reason="active breaker read is not pinned to chain verification")
def test_breaker_read_must_not_consume_unverified_concurrent_clear(env, monkeypatch):
    from config.settings import Settings
    from orchestrator.breakers import BreakerEngine
    from orchestrator.state import OrchestratorState, EVENT_BREAKER_CLEAR, EVENT_BREAKER_TRIP

    state = OrchestratorState.build(env.sf, hmac_key=b"synthetic-audit-key", settings=Settings())
    trip = state.audit.append(EVENT_BREAKER_TRIP, payload={"breaker":"test-trip"})
    original = state.audit.verify_chain
    injected = False

    def interleave():
        nonlocal injected
        result = original()
        if not injected:
            injected = True
            with env.sf() as session, session.begin():
                session.get(AuditEntry, trip.id).event_type = EVENT_BREAKER_CLEAR
        return result

    monkeypatch.setattr(state.audit, "verify_chain", interleave)
    assert "test-trip" in BreakerEngine(state).active_trips()


@pytest.mark.xfail(strict=True, reason="scheduled anchor rereads an unverified concurrent head")
def test_scheduled_anchor_must_publish_only_the_verified_snapshot(env, monkeypatch):
    from bridge.anchor import InMemoryAnchorStore
    from orchestrator.scheduler import run_anchor_head

    seed = env.first.append("seed")
    original = env.first.verify_chain

    def interleave():
        result = original()
        with env.sf() as session, session.begin():
            session.get(AuditEntry, seed.id).entry_hash = "f" * 64
        return result

    monkeypatch.setattr(env.first, "verify_chain", interleave)
    store = InMemoryAnchorStore()
    run_anchor_head(SimpleNamespace(audit=env.first, anchor_store=store))
    assert store.latest() == (seed.seq, seed.entry_hash)


@pytest.mark.parametrize("fault", ["delete", "bad-signature"])
@pytest.mark.xfail(strict=True, reason="HWM tamper between preflight and write is silently re-signed")
def test_concurrent_hwm_tamper_must_not_be_healed_by_bump(env, monkeypatch, fault):
    from sqlalchemy import update
    from storage.audit import AuditIntegrityError

    env.first.append("seed")
    injected = False
    original = env.first._hash

    def interleave(*args):
        nonlocal injected
        if not injected:
            injected = True
            with env.sf() as session, session.begin():
                if fault == "delete":
                    session.execute(audit_meta.delete())
                else:
                    session.execute(update(audit_meta).where(
                        audit_meta.c.k == "head_seq_sig").values(v="bad-signature"))
        return original(*args)

    monkeypatch.setattr(env.first, "_hash", interleave)
    with pytest.raises(AuditIntegrityError):
        env.first.append("must-refuse")


def test_stale_pinned_append_in_rolls_back_state_and_audit(env):
    env.first.append("seed")
    from sqlalchemy.exc import OperationalError

    with env.sf() as session:
        assert env.first.verify_session(session).ok
        env.second.append("competitor")
        session.add(ModeState(id="stale-state", mode="paper", created_at=default_clock.now()))
        with pytest.raises(OperationalError):
            env.first.append_in(session, "stale-state")
        session.rollback()
    assert env.second.verify_chain().ok and env.second.head()[0] == 2
    with env.sf() as session:
        assert session.get(ModeState, "stale-state") is None
