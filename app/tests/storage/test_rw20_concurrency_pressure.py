"""Independent RW-20 pressure tests; every ledger is an isolated temporary SQLite DB."""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select

from schemas.models.audit_entry import AuditEntry
from storage.audit import AuditLog
from storage.engine import create_all, make_engine, make_session_factory


@pytest.fixture
def writers(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'rw20-pressure.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    key = b"rw20-isolated-concurrency-test-key"
    yield AuditLog(sf, key), AuditLog(sf, key), sf
    engine.dispose()


def test_independent_writers_cannot_resolve_one_intent_twice(writers, monkeypatch):
    """A second writer commits after the first writer's duplicate-result SELECT.

    Independent AuditLog objects represent the separate in-process locks of separate
    processes. The interleaving is deterministic and needs neither thread timing nor I/O.
    Unlike a seq collision, the stale guard can then allocate against a NEW valid head.
    """
    first, second, sf = writers
    intent = first.intent("isolated_no_external_effect")
    original_insert = first._insert
    injected = False

    def competing_commit(session, event_type, actor, refs, payload):
        nonlocal injected
        if not injected:
            injected = True
            second.result(intent, "isolated_no_external_effect", payload={"writer": 2})
        return original_insert(session, event_type, actor, refs, payload)

    monkeypatch.setattr(first, "_insert", competing_commit)
    with pytest.raises(ValueError, match="already resolved"):
        first.result(intent, "isolated_no_external_effect", payload={"writer": 1})

    with sf() as session:
        results = [
            row for row in session.execute(select(AuditEntry)).scalars()
            if (row.payload or {}).get("_phase") == "result"
            and (row.refs or {}).get("intent_seq") == intent.seq
        ]
    assert injected and len(results) == 1
    assert first.verify_chain().ok


def test_append_in_commit_after_watermark_read_keeps_one_chain(writers, monkeypatch):
    """BUG-114 must stay closed even with separate AuditLog locks and stale watermark."""
    first, second, sf = writers
    first.append("seed")
    original_read = first._read_hwm
    injected = False

    def commit_after_watermark_read(session):
        nonlocal injected
        watermark = original_read(session)
        if not injected:
            injected = True
            with sf() as other, other.begin():
                second.append_in(other, "caller_owned_transaction")
        return watermark

    monkeypatch.setattr(first, "_read_hwm", commit_after_watermark_read)
    first.append("standalone")
    verified = first.verify_chain()
    assert injected and verified.ok and verified.head_seq == 3


def test_virgin_chain_commit_between_hwm_and_head_keeps_both_rows(writers, monkeypatch):
    """sqlite3 legacy SELECT does not pin a snapshot. A concurrent first-append can
    commit rows+watermark between ``_read_hwm`` and the head SELECT, which has the
    same shape as SEC-11 truncation. Allocation must re-read a consistent pair
    rather than refuse a healthy chain. Stable missing watermarks still refuse.
    """
    first, second, sf = writers
    original_read = first._read_hwm
    injected = False

    def commit_between_allocation_reads(session):
        nonlocal injected
        hwm = original_read(session)
        if not injected:
            injected = True
            second.append("competitor")
        return hwm

    monkeypatch.setattr(first, "_read_hwm", commit_between_allocation_reads)
    first.append("standalone")
    monkeypatch.setattr(first, "_read_hwm", original_read)
    assert injected
    verified = first.verify_chain()
    assert verified.ok and verified.head_seq == 2
    with sf() as session:
        rows = session.execute(select(AuditEntry)).scalars().all()
    assert {row.event_type for row in rows} == {"competitor", "standalone"}


def test_independent_append_writers_preserve_all_committed_rows(writers):
    """Bounded mixed-writer load; no model, broker, production DB, or external witness."""
    first, second, sf = writers
    start = threading.Barrier(2)
    errors = []

    def write(audit, actor):
        try:
            start.wait(timeout=5)
            for index in range(15):
                audit.append("pressure", actor=actor, payload={"index": index})
        except Exception as exc:  # propagate worker failures into the test result
            errors.append(exc)

    threads = [
        threading.Thread(target=write, args=(first, "first")),
        threading.Thread(target=write, args=(second, "second")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    assert first.verify_chain().ok
    with sf() as session:
        rows = session.execute(select(AuditEntry)).scalars().all()
    assert len(rows) == 30
    assert {(row.actor, row.payload["index"]) for row in rows} == {
        (actor, index) for actor in ("first", "second") for index in range(15)
    }


def test_later_append_cannot_heal_a_valid_hmac_fork(writers):
    """Recreate BUG-114's structural output in a NEW test DB, then preserve its refusal.

    The test-only HMAC key constructs the historic wrong-parent shape. No deployment
    ledger is opened or rewritten, and no production key is read.
    """
    first, second, sf = writers
    original = first.append("seed")
    first.append("middle")
    tail = first.append("tail")
    with sf() as session, session.begin():
        fork = session.get(AuditEntry, tail.id)
        fork.prev_hash = original.entry_hash
        fork.entry_hash = first._hash(
            fork.id, fork.seq, fork.prev_hash, fork.event_type, fork.actor,
            fork.refs, fork.payload,
        )
        assert first.verify_entry(fork), "fixture must isolate structural fork from bad HMAC"
    assert not first.verify_chain().ok
    second.append("subsequent_ordinary_append")
    after = second.verify_chain()
    assert not after.ok and "prev_hash" in after.error
