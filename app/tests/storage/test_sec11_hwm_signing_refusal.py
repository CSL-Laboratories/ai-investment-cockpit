"""BUG-124 — refuse missing/invalid established high-water at the signing boundary.

Astra miss 13: ``_bump_hwm`` used to treat a missing or invalid reread as a
replacement watermark (``seq if cur is None or cur < 0 else max(cur, seq)``).
An append that already observed established authority must raise
``AuditIntegrityError``, leave the damaged metadata in place, and let the
caller-owned transaction roll back the attempted audit/state writes.

These are owned application contracts promoted from
``docs/evidence/astra-hwm-resign-claim-shape-2026-09-06/test_refusal_contracts.py``.
Erasure-floor poison (miss 2/8) is deliberately not claimed here.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError

from schemas.models.audit_entry import AuditEntry
from schemas.models.mode_state import ModeState
from storage.audit import AuditIntegrityError, AuditLog, audit_meta
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"bug124-hwm-signing-refusal-key"
FAULTS = ("missing-pair", "missing-value", "missing-signature", "invalid-signature")


@pytest.fixture
def env(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'hwm.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    audit = AuditLog(sf, KEY)
    other = AuditLog(sf, KEY)
    yield {"engine": engine, "sf": sf, "audit": audit, "other": other}
    engine.dispose()


def _now():
    return datetime.now(timezone.utc)


def _meta(env):
    with env["sf"]() as session:
        return dict(session.execute(select(audit_meta.c.k, audit_meta.c.v)).all())


def _fault(env, fault):
    with env["sf"]() as session, session.begin():
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
            raise AssertionError(f"unknown bounded fault: {fault}")
    return _meta(env)


@pytest.mark.parametrize("operation", ["append", "append-in", "intent"])
@pytest.mark.parametrize("fault", FAULTS)
def test_changed_hwm_at_write_boundary_must_refuse(env, monkeypatch, operation, fault):
    audit = env["audit"]
    seed = audit.append("fixture-seed")
    assert audit.verify_chain().ok
    original_hash = audit._hash
    injected = False
    bad_meta = None

    def interleave(*args):
        nonlocal injected, bad_meta
        if not injected:
            injected = True
            bad_meta = _fault(env, fault)
            assert not env["other"].verify_chain().ok
        return original_hash(*args)

    monkeypatch.setattr(audit, "_hash", interleave)
    with pytest.raises(AuditIntegrityError):
        if operation == "append":
            audit.append("must-refuse")
        elif operation == "intent":
            audit.intent("fixture-only-must-refuse")
        else:
            with env["sf"]() as session, session.begin():
                session.add(ModeState(id="must-rollback", mode="read_only", created_at=_now()))
                session.flush()
                audit.append_in(session, "must-refuse")
    monkeypatch.setattr(audit, "_hash", original_hash)

    with env["sf"]() as session:
        state_present = session.get(ModeState, "must-rollback") is not None
    assert injected
    assert audit.head()[0] == seed.seq
    assert not state_present
    assert _meta(env) == bad_meta
    assert not env["other"].verify_chain().ok


@pytest.mark.parametrize("fault", FAULTS)
def test_preexisting_hwm_damage_already_refuses(env, fault):
    audit = env["audit"]
    seed = audit.append("fixture-seed")
    damaged = _fault(env, fault)
    with pytest.raises(AuditIntegrityError):
        audit.append("must-refuse")
    assert audit.head()[0] == seed.seq
    assert _meta(env) == damaged
    assert not env["other"].verify_chain().ok


@pytest.mark.parametrize("operation", ["pinned-append-in", "result"])
@pytest.mark.parametrize("fault", ["missing-pair", "invalid-signature"])
def test_pinned_snapshot_preserves_refusal(env, monkeypatch, operation, fault):
    audit = env["audit"]
    seed = audit.intent("fixture-only")
    original_hash = audit._hash
    injected = False

    def interleave(*args):
        nonlocal injected
        if not injected:
            injected = True
            _fault(env, fault)
        return original_hash(*args)

    monkeypatch.setattr(audit, "_hash", interleave)
    if operation == "result":
        # BUG-118 / PR #70 pins result() before its duplicate guard; retry sees damage.
        with pytest.raises(AuditIntegrityError):
            audit.result(seed, "fixture-only")
    else:
        with env["sf"]() as session, session.begin():
            session.connection().exec_driver_sql("BEGIN")
            session.execute(select(AuditEntry)).all()
            with pytest.raises(OperationalError):
                audit.append_in(session, "must-refuse")
            session.rollback()
    monkeypatch.setattr(audit, "_hash", original_hash)
    assert injected
    assert audit.head()[0] == seed.seq
    assert not env["other"].verify_chain().ok


def test_state_first_transaction_blocks_concurrent_meta_change(env, monkeypatch):
    engine = env["engine"]
    audit = env["audit"]

    @event.listens_for(engine, "checkout")
    def bound_fixture_wait(connection, *_):
        connection.execute("PRAGMA busy_timeout=30")

    audit.append("fixture-seed")
    original_hash = audit._hash
    blocked = False

    def attempt_change(*args):
        nonlocal blocked
        if not blocked:
            with pytest.raises(OperationalError):
                _fault(env, "missing-pair")
            blocked = True
        return original_hash(*args)

    monkeypatch.setattr(audit, "_hash", attempt_change)
    audit.record_state_change(
        ModeState(id="covered", mode="read_only", created_at=_now()),
        "fixture-state",
    )
    monkeypatch.setattr(audit, "_hash", original_hash)
    assert blocked
    assert audit.verify_chain().ok
    assert audit.verify_completeness(ModeState) == []


@pytest.mark.parametrize("fault", ["missing-pair", "invalid-signature"])
def test_sql_write_boundary_with_independent_writer_must_refuse(env, fault):
    """Confirm the interval at SQL execution without replacing any AuditLog method."""
    audit = env["audit"]
    engine = env["engine"]
    audit.append("fixture-seed")
    injected = False
    worker_errors: list[str] = []
    damaged = None

    def at_insert(connection, cursor, statement, parameters, context, executemany):
        nonlocal injected, damaged
        if injected or not str(statement).startswith("INSERT INTO audit_entry "):
            return
        injected = True
        assert connection.connection.driver_connection.in_transaction is False

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
        assert not env["other"].verify_chain().ok

    event.listen(engine, "before_cursor_execute", at_insert)
    try:
        with pytest.raises(AuditIntegrityError):
            audit.append("must-refuse")
    finally:
        event.remove(engine, "before_cursor_execute", at_insert)
    assert injected
    assert audit.head()[0] == 1
    assert _meta(env) == damaged
    assert not env["other"].verify_chain().ok


def test_healthy_first_append_still_initializes_watermark(env):
    audit = env["audit"]
    row = audit.append("genesis")
    assert row.seq == 1
    assert audit.verify_chain().ok
    assert audit.head()[0] == 1
    meta = _meta(env)
    assert meta["head_seq"] == "1"
    assert meta["head_seq_sig"] == audit._sign("1")


def test_legitimate_signed_advancement_stays_monotonic(env):
    audit = env["audit"]
    first = audit.append("one")
    second = audit.append("two")
    third = audit.append("three")
    assert (first.seq, second.seq, third.seq) == (1, 2, 3)
    assert _meta(env)["head_seq"] == "3"
    assert _meta(env)["head_seq_sig"] == audit._sign("3")
    assert audit.verify_chain().ok
