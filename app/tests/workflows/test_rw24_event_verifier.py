"""RW24: one verified triage snapshot, with fresh authority at the next tick/spend."""
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event as sql_event, select, update
from sqlalchemy.exc import OperationalError

from schemas.models.audit_entry import AuditEntry
from storage.audit import audit_meta
from tests.workflows.conftest import NOW
from workflows.budget import BudgetIntegrityError, DeepBudget, make_event_type_verifier
from workflows.constants import EVENT_DEEP_ESCALATION, EVENT_INGEST_BATCH


def _candidate(eid="event", event_type="filing"):
    return SimpleNamespace(id=eid, event_type=event_type)


def _bind(st, **bindings):
    return st.audit.append(EVENT_INGEST_BATCH, payload={"event_types": bindings})


def _tamper(st, row, **values):
    with st.session_factory() as writer, writer.begin():
        writer.execute(update(AuditEntry).where(AuditEntry.id == row.id).values(**values))


def test_distinct_candidates_share_one_lazy_snapshot(st):
    for index in range(30):
        _bind(st, **{f"event-{index}": "filing"})
    engine = st.session_factory.kw["bind"]
    statements = []

    def count(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    sql_event.listen(engine, "before_cursor_execute", count)
    try:
        verify = make_event_type_verifier(st)
        assert statements == []
        assert all(verify(_candidate(f"event-{index}")) for index in range(30))
        assert len(statements) == 3  # full chain, signed watermark, ingest bindings
        assert not verify(_candidate("unknown"))
        assert not verify(_candidate("event-0", "halt"))
        assert verify(_candidate("event-0"))
        assert len(statements) == 3
    finally:
        sql_event.remove(engine, "before_cursor_execute", count)


def test_snapshot_is_lazy_and_next_tick_sees_new_bindings(st):
    old_tick = make_event_type_verifier(st)
    _bind(st, first="filing")
    assert old_tick(_candidate("first"))
    _bind(st, second="filing")
    assert not old_tick(_candidate("second"))
    assert make_event_type_verifier(st)(_candidate("second"))


def test_empty_snapshot_does_not_reload_until_next_tick(st):
    old_tick = make_event_type_verifier(st)
    assert not old_tick(_candidate())
    _bind(st, event="filing")
    assert not old_tick(_candidate())
    assert make_event_type_verifier(st)(_candidate())


def test_binding_read_uses_the_verified_snapshot(st, monkeypatch):
    first = _bind(st, first="filing")
    _bind(st, second="halt")
    real_verify = st.audit.verify_session
    fired = False

    def verify_then_tamper(session):
        nonlocal fired
        result = real_verify(session)
        if not fired:
            fired = True
            assert result.ok
            _tamper(st, first, payload={**first.payload, "event_types": {"first": "halt"}})
        return result

    monkeypatch.setattr(st.audit, "verify_session", verify_then_tamper)
    old_tick = make_event_type_verifier(st)
    assert old_tick(_candidate("first"))  # the independently committed UPDATE is not in this WAL snapshot
    assert old_tick(_candidate("second", "halt"))
    assert not make_event_type_verifier(st)(_candidate("second", "halt"))
    # A stale triage result cannot authorize spending after integrity has failed.
    with pytest.raises(BudgetIntegrityError):
        DeepBudget(st, limit=5).try_consume(
            day=NOW.date(), event_id="first", symbol="AAPL",
            reason="event_type:filing", corroborated=True,
        )
    with st.session_factory() as session:
        assert session.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION
        )).scalars().all() == []


def test_benign_append_after_verification_does_not_reject_pinned_snapshot(st, monkeypatch):
    _bind(st, first="filing")
    real_verify = st.audit.verify_session
    fired = False

    def verify_then_append(session):
        nonlocal fired
        result = real_verify(session)
        if not fired:
            fired = True
            _bind(st, second="halt")
        return result

    monkeypatch.setattr(st.audit, "verify_session", verify_then_append)
    old_tick = make_event_type_verifier(st)
    assert old_tick(_candidate("first"))
    assert not old_tick(_candidate("second", "halt"))
    assert make_event_type_verifier(st)(_candidate("second", "halt"))


@pytest.mark.parametrize("change", ["binding", "filter", "unrelated", "delete", "erase"])
def test_tampered_chain_cannot_supply_any_binding(st, change):
    first = _bind(st, first="filing")
    _bind(st, second="filing")
    unrelated = st.audit.append("unrelated", payload={"value": 1})
    if change == "binding":
        _tamper(st, first, payload={**first.payload, "event_types": {"first": "halt"}})
    elif change == "filter":
        _tamper(st, first, event_type="hidden_from_ingest_query")
    elif change == "unrelated":
        _tamper(st, unrelated, actor="forged")
    else:
        with st.session_factory() as writer, writer.begin():
            if change == "delete":
                writer.execute(delete(AuditEntry).where(AuditEntry.id == unrelated.id))
            else:
                writer.execute(delete(AuditEntry))
                writer.execute(audit_meta.delete())
    assert not make_event_type_verifier(st)(_candidate("second"))


@pytest.mark.parametrize("anchor_kind", ["valid", "missing", "mismatch", "ahead", "unavailable"])
def test_external_anchor_is_consulted_once_per_snapshot(st, anchor_kind):
    _bind(st, event="filing")
    seq, digest = st.audit.head()
    calls = []

    def latest():
        calls.append(True)
        if anchor_kind == "unavailable":
            raise OSError("isolated witness unavailable")
        return {
            "valid": (seq, digest), "missing": None,
            "mismatch": (seq, "0" * 64), "ahead": (seq + 1, digest),
        }[anchor_kind]

    st.anchor_store = SimpleNamespace(latest=latest)
    verify = make_event_type_verifier(st)
    assert verify(_candidate()) is (anchor_kind == "valid")
    assert verify(_candidate()) is (anchor_kind == "valid")
    assert calls == [True]


@pytest.mark.parametrize("types", [[], "filing", {"": "filing"}, {"bad": ""}, {"bad": 3}])
def test_malformed_signed_map_publishes_no_partial_bindings(st, types):
    _bind(st, first="filing")
    st.audit.append(EVENT_INGEST_BATCH, payload={"event_types": types})
    _bind(st, second="filing")
    verify = make_event_type_verifier(st)
    assert not verify(_candidate("first"))
    assert not verify(_candidate("second"))


def test_legacy_missing_map_and_empty_valid_map_preserve_modern_binding(st):
    st.audit.append(EVENT_INGEST_BATCH, payload={})
    st.audit.append(EVENT_INGEST_BATCH, payload={"event_types": None})
    _bind(st)
    _bind(st, event="filing")
    assert make_event_type_verifier(st)(_candidate())
    assert not make_event_type_verifier(st)(_candidate("legacy_unbound"))


@pytest.mark.parametrize("values, expected", [
    (["filing", "filing"], True),
    (["filing", "halt"], False),
    (["halt", "filing"], False),
    (["filing", "halt", "filing"], False),
])
def test_conflicting_id_is_ambiguous_regardless_of_row_order(st, values, expected):
    for value in values:
        _bind(st, event=value)
    _bind(st, independent="calendar:earnings")
    verify = make_event_type_verifier(st)
    assert verify(_candidate()) is expected
    assert verify(_candidate("independent", "calendar:earnings"))


@pytest.mark.parametrize("eid, kind", [(None, "filing"), ([], "filing"), ("", "filing"),
                                     ("event", None), ("event", []), ("event", "")])
def test_malformed_candidate_refuses_without_poisoning_later_valid_lookup(st, eid, kind):
    _bind(st, event="filing")
    verify = make_event_type_verifier(st)
    assert not verify(_candidate(eid, kind))
    assert verify(_candidate())


def test_failed_snapshot_is_not_retried_within_tick(st, monkeypatch):
    _bind(st, event="filing")
    real_verify = st.audit.verify_session
    calls = []

    def fail_once(session):
        calls.append(True)
        if len(calls) == 1:
            raise OperationalError("fixture SELECT", {}, RuntimeError("isolated database busy"))
        return real_verify(session)

    monkeypatch.setattr(st.audit, "verify_session", fail_once)
    verify = make_event_type_verifier(st)
    assert not verify(_candidate())
    assert not verify(_candidate())
    assert len(calls) == 1
    assert make_event_type_verifier(st)(_candidate())
