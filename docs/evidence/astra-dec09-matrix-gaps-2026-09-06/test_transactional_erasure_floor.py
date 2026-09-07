"""Missing same-process erasure refusals beside BUG-114, using fresh test DBs.

No production ledger, credential, anchor, process or order is used. The assertions
are deliberately red on c5ef0ad and must not be converted to xfail or acceptance.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from schemas.models.audit_entry import AuditEntry
from schemas.models.mode_state import ModeState
from storage.audit import AuditLog, audit_meta
from storage.engine import create_all, make_engine, make_session_factory


@pytest.fixture
def ledger(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'fixture.db').as_posix()}")
    create_all(engine)
    sessions = make_session_factory(engine)
    yield AuditLog(sessions, b"dec09-test-only-key"), sessions
    engine.dispose()


def _erase_audit_only(sessions):
    with sessions() as session, session.begin():
        session.execute(AuditEntry.__table__.delete())
        session.execute(audit_meta.delete())


@pytest.mark.parametrize("write_api", ["append_in", "record_state_change"])
def test_committed_transactional_append_cannot_be_erased_into_a_virgin_chain(ledger, write_api):
    audit, sessions = ledger
    if write_api == "append_in":
        with sessions() as session, session.begin():
            audit.append_in(session, "fixture-event")
    else:
        audit.record_state_change(
            ModeState(id="fixture-mode", mode="read_only", created_at=datetime.now(timezone.utc)),
            "fixture-event",
        )
    # Prove commit from an independent connection. Calling verify_chain here
    # would prime the process floor and conceal the missing write-side behavior.
    with sessions() as session:
        assert len(session.execute(select(AuditEntry)).scalars().all()) == 1
    _erase_audit_only(sessions)
    result = audit.verify_chain()
    assert not result.ok, "a committed same-process write disappeared without an integrity refusal"


def test_rolled_back_append_in_does_not_poison_a_virgin_chain(ledger):
    audit, sessions = ledger
    with sessions() as session:
        audit.append_in(session, "fixture-rolled-back-event")
        session.rollback()
    result = audit.verify_chain()
    assert result.ok and result.head_seq == 0


def test_standalone_commit_already_refuses_the_same_erasure(ledger):
    audit, sessions = ledger
    audit.append("fixture-event")
    _erase_audit_only(sessions)
    result = audit.verify_chain()
    assert not result.ok and "erasure" in result.error


def test_transactional_commit_verified_before_erasure_already_refuses(ledger):
    audit, sessions = ledger
    with sessions() as session, session.begin():
        audit.append_in(session, "fixture-event")
    assert audit.verify_chain().ok
    _erase_audit_only(sessions)
    assert not audit.verify_chain().ok
