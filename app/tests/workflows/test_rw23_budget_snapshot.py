"""BUG-154: budget authority shares one SQLite snapshot, including the spend write."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from orchestrator.limits import EVENT_LIMITS_CHANGE
from orchestrator.state import OrchestratorState
from schemas.models.audit_entry import AuditEntry
from storage.audit import audit_meta
from tests.workflows.conftest import KEY, NOW
from workflows.budget import BudgetIntegrityError, DeepBudget
from workflows.constants import EVENT_DEEP_ESCALATION

DAY = NOW.date()


def _consume(budget, event_id="event"):
    return budget.try_consume(day=DAY, event_id=event_id, symbol="AAPL",
                              reason="quote_move:4.0", corroborated=True)


def _spends(st):
    with st.session_factory() as session:
        return session.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION
        )).scalars().all()


def _after_verification(st, monkeypatch, write):
    """Commit with a second real connection while the verified WAL snapshot is open."""
    real = st.audit.verify_session
    fired = False

    def verify(session):
        nonlocal fired
        result = real(session)
        if result.ok and not fired:
            fired = True
            write()
        return result

    monkeypatch.setattr(st.audit, "verify_session", verify)


@pytest.mark.parametrize("read", ["spent", "remaining", "consume"])
def test_consumption_deleted_after_verify_cannot_inflate_budget(st, monkeypatch, read):
    budget = DeepBudget(st, limit=1)
    assert _consume(budget)
    st.audit.append("surviving_later_entry")

    def erase():
        with st.session_factory() as writer, writer.begin():
            writer.execute(delete(AuditEntry).where(
                AuditEntry.event_type == EVENT_DEEP_ESCALATION
            ))

    _after_verification(st, monkeypatch, erase)
    if read == "spent":
        assert budget.spent(DAY) == 1
    elif read == "remaining":
        assert budget.remaining(DAY) == 0
    else:
        assert not _consume(budget, "second")
    assert not st.audit.verify_chain().ok
    with pytest.raises(BudgetIntegrityError):
        _consume(budget, "third")


def test_allowed_spend_rechecks_damage_before_committing(st, monkeypatch):
    budget = DeepBudget(st, limit=2)
    assert _consume(budget)
    st.audit.append("surviving_later_entry")

    def erase():
        with st.session_factory() as writer, writer.begin():
            writer.execute(delete(AuditEntry).where(
                AuditEntry.event_type == EVENT_DEEP_ESCALATION
            ))

    _after_verification(st, monkeypatch, erase)
    # The pinned read still has one remaining unit, but its write cannot commit over
    # the newer database image. Retrying sees corruption and refuses before append.
    with pytest.raises(BudgetIntegrityError):
        _consume(budget, "second")
    assert _spends(st) == []


def test_new_tightening_wins_when_spend_snapshot_retries(st, monkeypatch):
    st.audit.append("baseline")
    budget = DeepBudget(st)
    _after_verification(st, monkeypatch, lambda: st.audit.append(
        EVENT_LIMITS_CHANGE, actor="operator",
        payload={"limits": {"deep_model_daily_budget": 0}},
    ))
    assert not _consume(budget)
    assert _spends(st) == []
    assert budget.limit == 0
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("read", ["spent", "remaining", "consume"])
def test_benign_concurrent_append_preserves_consistent_budget(st, monkeypatch, read):
    st.audit.append("baseline")
    budget = DeepBudget(st)
    _after_verification(st, monkeypatch, lambda: st.audit.append("ordinary_feed_event"))
    if read == "spent":
        assert budget.spent(DAY) == 0
    elif read == "remaining":
        assert budget.remaining(DAY) == st.settings.deep_model_daily_budget
    else:
        assert _consume(budget)
    assert st.audit.verify_chain().ok


def test_independent_writers_cannot_both_take_last_unit(st, monkeypatch):
    st.audit.append("baseline")
    other = OrchestratorState.build(st.session_factory, hmac_key=KEY,
        settings=st.settings, clock=st.clock)
    ready = Barrier(2)

    def synchronize(state):
        real = state.audit.verify_session
        first = True

        def verify(session):
            nonlocal first
            result = real(session)
            if first:
                first = False
                assert result.ok
                ready.wait(timeout=5)
            return result

        monkeypatch.setattr(state.audit, "verify_session", verify)

    for state in (st, other):
        synchronize(state)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda state: _consume(DeepBudget(state, limit=1)), (st, other)))
    assert sorted(results) == [False, True]
    assert len(_spends(st)) == 1
    assert st.audit.verify_chain().ok


def test_postcommit_observed_head_refuses_replayed_old_watermark(st):
    st.audit.append("baseline")
    with st.session_factory() as session:
        old_meta = [dict(row._mapping) for row in session.execute(select(audit_meta))]
    assert _consume(DeepBudget(st, limit=1))
    with st.session_factory() as writer, writer.begin():
        writer.execute(delete(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION
        ))
        writer.execute(audit_meta.delete())
        writer.execute(audit_meta.insert(), old_meta)
    with pytest.raises(BudgetIntegrityError, match="observed head"):
        DeepBudget(st, limit=1).spent(DAY)


@pytest.mark.parametrize("read", ["spent", "remaining", "consume"])
def test_budget_checks_external_anchor_in_the_same_snapshot(st, read):
    st.audit.append("baseline")
    seq, digest = st.audit.head()
    st.anchor_store = SimpleNamespace(latest=lambda: (seq, digest))
    budget = DeepBudget(st)
    assert budget.remaining(DAY) > 0
    st.anchor_store = SimpleNamespace(latest=lambda: (seq, "0" * 64))
    with pytest.raises(BudgetIntegrityError):
        if read == "spent":
            budget.spent(DAY)
        elif read == "remaining":
            budget.remaining(DAY)
        else:
            _consume(budget)
    assert _spends(st) == []
