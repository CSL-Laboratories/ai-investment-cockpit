"""Defensive contract checks: only fresh fixtures, no application transport or soak.

Research assertions remain red when the unchanged application misses the contract.
The existing storage/FSM fixtures are reused; no production authority is created.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import DetachedInstanceError

from brokers.order_state_machine import IllegalOrderTransition, transition
from config.settings import Mode
from orchestrator.mode_ladder import ModeLadder
from schemas.models.audit_entry import AuditEntry
from schemas.models.mode_state import ModeState
from schemas.models.order import Order
from storage.audit import AuditIntegrityError, AuditLog, audit_meta
from tests.brokers import test_order_state_machine as fsm_tests
from tests.storage import test_audit as storage_tests

pytest_plugins = ["tests.conftest"]
st = fsm_tests.st
env = storage_tests.env
_new_order = fsm_tests._new_order


class InterruptedWrite(BaseException):
    """Model process interruption unwinding a transaction, without killing a process."""


def _state(sf, model):
    with sf() as session:
        models = [
            {column.name: getattr(row, column.name) for column in model.__table__.columns}
            for row in session.execute(select(model).order_by(model.id)).scalars()
        ]
        ledger = [
            (row.id, row.seq, row.prev_hash, row.entry_hash)
            for row in session.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars()
        ]
        watermark = dict(session.execute(select(audit_meta)).all())
    return models, ledger, watermark


def _interrupt_at(sf, audit, monkeypatch, point, failure):
    if point == "state_flushed":
        def stop(*args, **kwargs):
            raise failure("isolated state flush interruption")
        monkeypatch.setattr(audit, "append_in", stop)
        return lambda: None
    if point == "audit_flushed":
        def stop(*args, **kwargs):
            raise failure("isolated audit flush interruption")
        monkeypatch.setattr(audit, "_bump_hwm", stop)
        return lambda: None
    if point == "signed_watermark":
        original = audit._bump_hwm

        def stop(session, seq):
            original(session, seq)
            raise failure("isolated signed watermark interruption")
        monkeypatch.setattr(audit, "_bump_hwm", stop)
        return lambda: None
    if point == "before_commit":
        original = audit._bump_hwm

        def mark_write(session, seq):
            original(session, seq)
            session.info["fixture_has_audit_write"] = True

        monkeypatch.setattr(audit, "_bump_hwm", mark_write)

        def stop(session):
            if session.info.get("fixture_has_audit_write"):
                raise failure("isolated pre-commit interruption")
        event.listen(sf.class_, "before_commit", stop)
        return lambda: event.remove(sf.class_, "before_commit", stop)
    engine = sf.kw["bind"]

    def stop(conn, cursor, statement, parameters, context, executemany):
        if (point == "watermark_deleted" and statement.startswith("DELETE FROM audit_meta")) or (
            point == "watermark_unsigned" and statement.startswith("INSERT INTO audit_meta")
            and parameters[0] == "head_seq"
        ):
            raise failure("isolated watermark interruption")
    event.listen(engine, "after_cursor_execute", stop)
    return lambda: event.remove(engine, "after_cursor_execute", stop)


@pytest.mark.parametrize("failure", [RuntimeError, InterruptedWrite])
@pytest.mark.parametrize("point", [
    "state_flushed", "audit_flushed", "watermark_deleted", "watermark_unsigned",
    "signed_watermark", "before_commit",
])
def test_mode_change_interruption_refuses_and_rolls_back(st, monkeypatch, point, failure):
    ladder = ModeLadder(st)
    st.audit.append("fixture_seed")
    before = _state(st.session_factory, ModeState)
    remove = _interrupt_at(st.session_factory, st.audit, monkeypatch, point, failure)
    try:
        with pytest.raises(failure, match="isolated"):
            ladder.set_mode(Mode.paper, actor="fixture")
    finally:
        remove()
        monkeypatch.undo()
    assert _state(st.session_factory, ModeState) == before
    assert ladder.current() is Mode.read_only
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("audit_first", [False, True])
def test_caller_owned_transaction_rolls_back_both_directions(env, audit_first):
    audit, sf = env
    audit.append("fixture_seed")
    before = _state(sf, ModeState)
    row = ModeState(id="fixture_mode", mode="paper", created_at=datetime.now(timezone.utc))
    with pytest.raises(InterruptedWrite):
        with sf() as session, session.begin():
            if audit_first:
                audit.append_in(session, "mode_change", refs={"model": "mode_state", "row_id": row.id})
            session.add(row)
            session.flush()
            if not audit_first:
                audit.append_in(session, "mode_change", refs={"model": "mode_state", "row_id": row.id})
            raise InterruptedWrite()
    assert _state(sf, ModeState) == before
    assert audit.verify_chain().ok


def test_standalone_retry_does_not_leave_partial_watermark(env, monkeypatch):
    audit, sf = env
    original = audit._bump_hwm
    calls = 0

    def fail_once(session, seq):
        nonlocal calls
        original(session, seq)
        calls += 1
        if calls == 1:
            raise OperationalError("fixture", {}, RuntimeError("isolated busy"))

    monkeypatch.setattr(audit, "_bump_hwm", fail_once)
    row = audit.append("fixture_retry")
    assert calls == 2 and row.seq == 1
    assert audit.verify_chain().ok
    assert len(_state(sf, ModeState)[1]) == 1


def test_stale_read_transaction_refuses_and_rolls_back_state(env):
    audit, sf = env
    other = AuditLog(sf, b"unit-test-hmac-key")
    audit.append("fixture_seed")
    with sf() as session, session.begin():
        assert audit.verify_session(session).ok
        other.append("fixture_competing_commit")
        before = _state(sf, ModeState)
        with pytest.raises(OperationalError):
            session.add(ModeState(id="stale_mode", mode="paper", created_at=datetime.now(timezone.utc)))
            session.flush()
        session.rollback()
    assert _state(sf, ModeState) == before
    assert audit.verify_chain().ok


def _detached_order(st, order_id):
    with st.session_factory() as session:
        row = session.get(Order, order_id)
        session.expunge(row)
        return row


def test_competing_order_transition_refuses_stale_terminal_source(st):
    """Extend the existing terminal-state test to two legitimate detached readers."""
    original = _new_order(st, state="approved")
    stale = _detached_order(st, original.id)
    transition(st, original, "cancelled", actor="fixture")
    before = _state(st.session_factory, Order)
    refused = False
    try:
        transition(st, stale, "submitted", actor="fixture")
    except IllegalOrderTransition:
        refused = True
    after = _state(st.session_factory, Order)
    assert refused and after == before, (
        "stale detached state must refuse without replacing a committed terminal row; "
        f"refused={refused}, state={after[0][0]['state']}, "
        f"extra_audit_rows={len(after[1]) - len(before[1])}, audit_ok={st.audit.verify_chain().ok}"
    )


def test_competing_fill_transition_preserves_signed_row_agreement(st):
    """A concurrent fill commit must make stale persistence refuse and leave both intact."""
    original = _new_order(st, state="submitted")
    stale = _detached_order(st, original.id)
    transition(st, original, "partially_filled", actor="fixture", filled_qty=Decimal("3"),
               avg_fill_price=Decimal("150"))
    before = _state(st.session_factory, Order)
    refused = False
    try:
        transition(st, stale, "cancelled", actor="fixture")
    except IllegalOrderTransition:
        refused = True
    after = _state(st.session_factory, Order)
    with st.session_factory() as session:
        latest = session.execute(select(AuditEntry).order_by(AuditEntry.seq.desc())).scalars().first()
        signed_fill = latest.payload.get("filled_qty")
    assert refused and after == before, (
        "a stale transition must refuse and roll back; "
        f"refused={refused}, row_fill={after[0][0]['filled_qty']}, signed_fill={signed_fill}, "
        f"extra_audit_rows={len(after[1]) - len(before[1])}, audit_ok={st.audit.verify_chain().ok}"
    )


def test_interrupted_commit_does_not_publish_a_detached_transition(st, monkeypatch):
    """Extend the same transition test with failure after expunge, before COMMIT."""
    order = _new_order(st)
    before = _state(st.session_factory, Order)
    remove = _interrupt_at(st.session_factory, st.audit, monkeypatch, "before_commit", RuntimeError)
    try:
        with pytest.raises(RuntimeError, match="isolated"):
            transition(st, order, "approved", actor="fixture")
    finally:
        remove()
    assert _state(st.session_factory, Order) == before
    assert st.audit.verify_chain().ok
    try:
        caller_state = order.state
    except DetachedInstanceError:
        return  # Invalidating the rolled-back object also prevents false publication.
    assert caller_state == "proposed", (
        "a rolled-back transition must not remain published on the caller's detached row; "
        f"caller_state={caller_state}, durable_state={before[0][0]['state']}"
    )


def test_retry_after_interruption_cannot_skip_an_unsigned_transition(st, monkeypatch):
    """A fresh reader's signed state must govern the caller's next state transition."""
    order = _new_order(st)
    remove = _interrupt_at(st.session_factory, st.audit, monkeypatch, "before_commit", RuntimeError)
    try:
        with pytest.raises(RuntimeError, match="isolated"):
            transition(st, order, "approved", actor="fixture")
    finally:
        remove()
    before = _state(st.session_factory, Order)
    refused = False
    try:
        transition(st, order, "submitted", actor="fixture")
    except (IllegalOrderTransition, DetachedInstanceError):
        refused = True
    after = _state(st.session_factory, Order)
    assert refused and after == before, (
        "resuming a detached row after rollback must not skip unsigned approval state; "
        f"refused={refused}, durable_state={after[0][0]['state']}, "
        f"extra_audit_rows={len(after[1]) - len(before[1])}, audit_ok={st.audit.verify_chain().ok}"
    )


def test_uncommitted_append_is_not_visible_to_other_verifiers(env):
    audit, sf = env
    audit.append("fixture_seed")
    reader = AuditLog(sf, b"unit-test-hmac-key")
    before = _state(sf, ModeState)
    with pytest.raises(InterruptedWrite):
        with sf() as session, session.begin():
            row = ModeState(id="pending_mode", mode="paper", created_at=datetime.now(timezone.utc))
            session.add(row)
            session.flush()
            audit.append_in(session, "mode_change", refs={"model": "mode_state", "row_id": row.id})
            assert _state(sf, ModeState) == before
            verified = reader.verify_chain()
            assert verified.ok and verified.head_seq == 1
            raise InterruptedWrite()
    assert _state(sf, ModeState) == before
    assert reader.verify_chain().ok


def test_exhausted_standalone_retries_leave_no_partial_state(env, monkeypatch):
    audit, sf = env
    audit.append("fixture_seed")
    before = _state(sf, ModeState)
    original = audit._bump_hwm
    calls = 0

    def fail_always(session, seq):
        nonlocal calls
        original(session, seq)
        calls += 1
        raise OperationalError("fixture", {}, RuntimeError("isolated busy"))

    monkeypatch.setattr(audit, "_bump_hwm", fail_always)
    with pytest.raises(OperationalError):
        audit.append("fixture_retry_exhausted")
    assert calls == 6
    assert _state(sf, ModeState) == before
    assert audit.verify_chain().ok


@pytest.mark.parametrize("fault", ["missing", "invalid_signature"])
def test_invalid_watermark_refuses_mode_persistence_and_rolls_back(st, fault):
    st.audit.append("fixture_seed")
    with st.session_factory() as session, session.begin():
        if fault == "missing":
            session.execute(audit_meta.delete())
        else:
            session.execute(update(audit_meta).where(audit_meta.c.k == "head_seq_sig").values(v="invalid"))
    before = _state(st.session_factory, ModeState)
    with pytest.raises(AuditIntegrityError):
        ModeLadder(st).set_mode(Mode.paper, actor="fixture")
    assert _state(st.session_factory, ModeState) == before
    assert not st.audit.verify_chain().ok
    assert ModeLadder(st).current() is Mode.read_only


def test_failed_completion_batch_does_not_publish_uncommitted_verified_head(st, monkeypatch):
    """Use the real per-CIK completion loop; failure must leave the committed floor intact."""
    from ingest.edgar_pipeline import _record_poll_completions, _VerifiedPollBaselineStore
    from schemas.models.pollbaseline import PollBaseline

    baselines = _VerifiedPollBaselineStore(st.session_factory, st.audit)
    ciks = ("0000320193", "0001045810")
    for cik in ciks:
        assert baselines.establish(cik, None, st.clock.now())
    committed = st.audit.verify_chain()
    assert committed.ok and committed.head_seq == 2
    before = _state(st.session_factory, PollBaseline)
    original = st.audit._bump_hwm
    calls = 0

    def interrupt_second_completion(session, seq):
        nonlocal calls
        original(session, seq)
        calls += 1
        if calls == 2:
            raise InterruptedWrite("isolated second completion")

    monkeypatch.setattr(st.audit, "_bump_hwm", interrupt_second_completion)
    with pytest.raises(InterruptedWrite):
        _record_poll_completions(st.session_factory, st.audit, st.clock, ciks,
                                 process_epoch=st.process_epoch)
    assert calls == 2
    assert _state(st.session_factory, PollBaseline) == before
    fresh = AuditLog(st.session_factory, b"fsm-key", st.clock).verify_chain()
    assert fresh.ok and fresh.head_seq == committed.head_seq
    current = st.audit.verify_chain()
    assert current.ok and current.head_seq == committed.head_seq, (
        "rollback must not promote an uncommitted verified prefix into the process floor; "
        f"durable_head={fresh.head_seq}, refusal={current.error}"
    )


@pytest.mark.parametrize("point", [
    "audit_flushed", "watermark_deleted", "watermark_unsigned", "signed_watermark", "before_commit",
])
def test_interrupted_result_preserves_unresolved_intent(env, monkeypatch, point):
    audit, sf = env
    intent = audit.intent("fixture_no_external_effect")
    before = _state(sf, ModeState)
    remove = _interrupt_at(sf, audit, monkeypatch, point, InterruptedWrite)
    try:
        with pytest.raises(InterruptedWrite):
            audit.result(intent, "fixture_no_external_effect")
    finally:
        remove()
        monkeypatch.undo()
    assert _state(sf, ModeState) == before
    assert audit.unresolved_intents() == [intent.seq]
    assert audit.orphan_results() == []
    assert audit.verify_chain().ok


def test_post_commit_notification_failure_preserves_matched_mode_and_audit(st, monkeypatch):
    """After actual COMMIT, preserve both durable records; a same-target retry is a no-op."""
    ladder = ModeLadder(st)
    original = st.audit._bump_hwm

    def mark_write(session, seq):
        original(session, seq)
        session.info["fixture_has_audit_write"] = True

    monkeypatch.setattr(st.audit, "_bump_hwm", mark_write)

    def stop(session):
        if session.info.get("fixture_has_audit_write"):
            raise RuntimeError("isolated acknowledgement after commit")

    event.listen(st.session_factory.class_, "after_commit", stop)
    try:
        with pytest.raises(RuntimeError, match="isolated acknowledgement"):
            ladder.set_mode(Mode.paper, actor="fixture")
    finally:
        event.remove(st.session_factory.class_, "after_commit", stop)
    before = _state(st.session_factory, ModeState)
    assert len(before[0]) == len(before[1]) == 1
    assert before[0][0]["mode"] == "paper"
    with st.session_factory() as session:
        signed = session.execute(select(AuditEntry)).scalar_one()
        assert signed.refs["row_id"] == before[0][0]["id"]
        assert signed.payload["to"] == "paper"
    assert st.audit.verify_chain().ok
    assert ladder.set_mode(Mode.paper, actor="fixture") is Mode.paper
    assert _state(st.session_factory, ModeState) == before
