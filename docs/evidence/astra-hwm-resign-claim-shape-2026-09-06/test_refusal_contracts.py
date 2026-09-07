"""Desired refusal contracts against unchanged application code.

All mutations are bounded faults in disposable SQLite fixtures. Assertions require
refusal and preserved evidence; no production ledger or credential store is opened.
Expected misses remain ordinary failing tests, never disguised as acceptance passes.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, update

from core.clock import default_clock
from schemas.models.audit_entry import AuditEntry
from schemas.models.mode_state import ModeState
from storage.audit import AuditIntegrityError, AuditLog, audit_meta
from storage.engine import create_all, make_engine, make_session_factory


@pytest.fixture
def env(tmp_path, request):
    engine = make_engine(f"sqlite:///{(tmp_path / 'synthetic.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    first = AuditLog(sf, b"public-fixture-key")
    other = AuditLog(sf, b"public-fixture-key")
    observations = {}
    yield SimpleNamespace(
        engine=engine, sf=sf, audit=first, other=other, observations=observations,
    )
    engine.dispose()
    if destination := os.environ.get("HWM_RESEARCH_OBSERVATIONS"):
        output = Path(destination)
        output.mkdir(exist_ok=True)
        (output / f"{request.node.name}.json").write_text(
            json.dumps(observations, sort_keys=True, indent=2) + "\n"
        )


def _meta(env):
    with env.sf() as session:
        return dict(session.execute(select(audit_meta.c.k, audit_meta.c.v)).all())


def _fault(env, fault):
    with env.sf() as session, session.begin():
        if fault == "missing-pair":
            session.execute(audit_meta.delete())
        elif fault == "missing-value":
            session.execute(audit_meta.delete().where(audit_meta.c.k == "head_seq"))
        elif fault == "missing-signature":
            session.execute(audit_meta.delete().where(audit_meta.c.k == "head_seq_sig"))
        elif fault == "invalid-signature":
            session.execute(update(audit_meta).where(
                audit_meta.c.k == "head_seq_sig"
            ).values(v="invalid-fixture-signature"))
        else:
            raise AssertionError("unknown bounded fault")
    return _meta(env)


FAULTS = ["missing-pair", "missing-value", "missing-signature", "invalid-signature"]


@pytest.mark.parametrize("operation", ["append", "append-in", "intent"])
@pytest.mark.parametrize("fault", FAULTS)
def test_changed_hwm_at_write_boundary_must_refuse(env, monkeypatch, operation, fault):
    seed = env.audit.append("fixture-seed")
    assert env.audit.verify_chain().ok
    original_hash = env.audit._hash
    injected = False
    bad_meta = None
    observation = env.observations

    def interleave(*args):
        nonlocal injected, bad_meta
        if not injected:
            injected = True
            bad_meta = _fault(env, fault)
            observation["fault_visible_to_independent_verifier"] = not env.other.verify_chain().ok
        return original_hash(*args)

    monkeypatch.setattr(env.audit, "_hash", interleave)
    refusal = None
    try:
        if operation == "append":
            env.audit.append("must-refuse")
        elif operation == "intent":
            env.audit.intent("fixture-only-must-refuse")
        else:
            # Caller has not flushed any writes or explicitly pinned a read snapshot.
            with env.sf() as session, session.begin():
                env.audit.append_in(session, "must-refuse")
                session.add(ModeState(
                    id="must-rollback", mode="read_only", created_at=default_clock.now(),
                ))
    except AuditIntegrityError as exc:
        refusal = type(exc).__name__
    finally:
        monkeypatch.setattr(env.audit, "_hash", original_hash)
    verified = env.other.verify_chain()
    with env.sf() as session:
        state_present = session.get(ModeState, "must-rollback") is not None
    observation.update(
        fault=fault, operation=operation, injected=injected, refusal=refusal,
        seed_head=seed.seq, final_head=env.audit.head()[0],
        final_audit_ok=verified.ok, final_audit_error=verified.error,
        damaged_meta_preserved=_meta(env) == bad_meta,
        state_row_present=state_present,
    )
    assert injected and observation["fault_visible_to_independent_verifier"]
    assert refusal == "AuditIntegrityError", observation
    assert env.audit.head()[0] == seed.seq and not state_present
    assert _meta(env) == bad_meta and not verified.ok


@pytest.mark.parametrize("fault", FAULTS)
def test_preexisting_hwm_damage_already_refuses(env, fault):
    seed = env.audit.append("fixture-seed")
    damaged = _fault(env, fault)
    with pytest.raises(AuditIntegrityError):
        env.audit.append("must-refuse")
    env.observations.update(
        fault=fault, final_head=env.audit.head()[0],
        damaged_meta_preserved=_meta(env) == damaged,
        final_audit_ok=env.other.verify_chain().ok,
    )
    assert env.audit.head()[0] == seed.seq
    assert _meta(env) == damaged and not env.other.verify_chain().ok


@pytest.mark.parametrize("operation", ["pinned-append-in", "result"])
@pytest.mark.parametrize("fault", ["missing-pair", "invalid-signature"])
def test_pinned_snapshot_preserves_refusal(env, monkeypatch, operation, fault):
    from sqlalchemy.exc import OperationalError

    seed = env.audit.intent("fixture-only")
    original_hash = env.audit._hash
    injected = False

    def interleave(*args):
        nonlocal injected
        if not injected:
            injected = True
            _fault(env, fault)
        return original_hash(*args)

    monkeypatch.setattr(env.audit, "_hash", interleave)
    if operation == "result":
        # #70 pins result() before its duplicate guard; retry sees damaged authority.
        with pytest.raises(AuditIntegrityError):
            env.audit.result(seed, "fixture-only")
    else:
        with env.sf() as session, session.begin():
            session.connection().exec_driver_sql("BEGIN")
            session.execute(select(AuditEntry)).all()
            with pytest.raises(OperationalError):
                env.audit.append_in(session, "must-refuse")
            session.rollback()
    monkeypatch.setattr(env.audit, "_hash", original_hash)
    env.observations.update(
        injected=injected, operation=operation, final_head=env.audit.head()[0],
        final_audit_ok=env.other.verify_chain().ok,
    )
    assert injected and env.audit.head()[0] == seed.seq
    assert not env.other.verify_chain().ok


def test_state_first_transaction_blocks_concurrent_meta_change(env, monkeypatch):
    from sqlalchemy.exc import OperationalError

    @event.listens_for(env.engine, "checkout")
    def bound_fixture_wait(connection, *_):
        connection.execute("PRAGMA busy_timeout=30")

    env.audit.append("fixture-seed")
    original_hash = env.audit._hash
    blocked = False

    def attempt_change(*args):
        nonlocal blocked
        if not blocked:
            with pytest.raises(OperationalError):
                _fault(env, "missing-pair")
            blocked = True
        return original_hash(*args)

    monkeypatch.setattr(env.audit, "_hash", attempt_change)
    env.audit.record_state_change(
        ModeState(id="covered", mode="read_only", created_at=default_clock.now()),
        "fixture-state",
    )
    monkeypatch.setattr(env.audit, "_hash", original_hash)
    env.observations.update(meta_change_blocked=blocked, final_audit_ok=env.audit.verify_chain().ok)
    assert blocked and env.audit.verify_chain().ok
    assert env.audit.verify_completeness(ModeState) == []


@pytest.mark.parametrize("fault", ["missing-pair", "invalid-signature"])
def test_sql_write_boundary_with_independent_writer_must_refuse(env, fault):
    """Confirm the interval at SQL execution without replacing any AuditLog method."""
    env.audit.append("fixture-seed")
    injected = False
    worker_errors = []
    damaged = None

    def at_insert(connection, cursor, statement, parameters, context, executemany):
        nonlocal injected, damaged
        if injected or not statement.startswith("INSERT INTO audit_entry "):
            return
        injected = True
        env.observations["sqlite_in_transaction_before_insert"] = (
            connection.connection.driver_connection.in_transaction
        )

        def writer():
            nonlocal damaged
            try:
                damaged = _fault(env, fault)
            except Exception as exc:
                worker_errors.append(type(exc).__name__)

        worker = threading.Thread(target=writer, daemon=True)
        worker.start()
        worker.join(3)
        assert not worker.is_alive() and not worker_errors
        env.observations["independent_writer_committed_before_insert"] = True
        env.observations["fault_visible_to_independent_verifier"] = not env.other.verify_chain().ok

    event.listen(env.engine, "before_cursor_execute", at_insert)
    refusal = None
    try:
        env.audit.append("must-refuse")
    except AuditIntegrityError as exc:
        refusal = type(exc).__name__
    finally:
        event.remove(env.engine, "before_cursor_execute", at_insert)
    env.observations.update(
        fault=fault, refusal=refusal, injected=injected, final_head=env.audit.head()[0],
        final_audit_ok=env.other.verify_chain().ok,
        damaged_meta_preserved=_meta(env) == damaged,
    )
    assert injected and env.observations["fault_visible_to_independent_verifier"]
    assert refusal == "AuditIntegrityError", env.observations
    assert env.audit.head()[0] == 1 and _meta(env) == damaged
    assert not env.other.verify_chain().ok


@pytest.mark.parametrize("transaction", ["rollback", "savepoint-rollback"])
def test_uncommitted_verification_must_not_poison_floor(env, transaction):
    seed = env.audit.append("fixture-seed")
    with env.sf() as session, session.begin():
        if transaction == "savepoint-rollback":
            assert env.audit.verify_session(session).ok  # Explicit outer DB transaction.
            nested = session.begin_nested()
        env.audit.append_in(session, "uncommitted")
        assert env.audit.verify_session(session).ok
        during_floor = env.audit._observed_head
        if transaction == "savepoint-rollback":
            nested.rollback()
        else:
            session.rollback()
    current = env.audit.verify_chain()
    independent = env.other.verify_chain()
    env.observations.update(
        transaction=transaction, seed_head=seed.seq, during_floor=during_floor,
        committed_head=env.audit.head()[0], shared_verifier_ok=current.ok,
        shared_verifier_error=current.error, independent_verifier_ok=independent.ok,
    )
    assert independent.ok and env.audit.head()[0] == seed.seq
    assert current.ok, env.observations


def test_edgar_completion_rollback_must_preserve_valid_shared_verifier(env, monkeypatch):
    from ingest.edgar_pipeline import _record_poll_completions

    env.audit.append("fixture-seed")
    original = env.audit.append_in
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("bounded second-CIK persistence fault")
        return original(*args, **kwargs)

    monkeypatch.setattr(env.audit, "append_in", fail_second)
    with pytest.raises(RuntimeError, match="second-CIK"):
        _record_poll_completions(
            env.sf, env.audit, default_clock, ["1", "2"], process_epoch="fixture-only",
        )
    result = env.audit.verify_chain()
    env.observations.update(
        completion_append_calls=calls, committed_head=env.audit.head()[0],
        floor=env.audit._observed_head, shared_verifier_ok=result.ok,
        shared_verifier_error=result.error, independent_verifier_ok=env.other.verify_chain().ok,
    )
    assert calls == 2 and env.other.verify_chain().ok and env.audit.head()[0] == 1
    assert result.ok, env.observations
