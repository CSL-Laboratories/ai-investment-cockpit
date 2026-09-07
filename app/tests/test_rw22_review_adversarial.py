"""Independent review controls for the RW-22 signed-prefix reader."""
from sqlalchemy import delete, select

from orchestrator.limits import EVENT_LIMITS_CHANGE, current_limits, fail_closed_limits
from schemas.models.audit_entry import AuditEntry
from storage.audit import audit_meta
from tests.test_rw22_limits_recovery import _forged_tail, st as st


def test_earlier_watermark_and_forged_tail_cannot_bypass_observed_floor(st):
    st.audit.append(EVENT_LIMITS_CHANGE, payload={"limits": {"deep_model_daily_budget": 4}})
    with st.session_factory() as session:
        old_mark = [dict(row._mapping) for row in session.execute(select(audit_meta))]
    tighter = st.audit.append(
        EVENT_LIMITS_CHANGE, payload={"limits": {"deep_model_daily_budget": 2}},
    )
    _forged_tail(st)
    with st.session_factory() as session, session.begin():
        session.execute(delete(AuditEntry).where(AuditEntry.id == tighter.id))
        session.execute(audit_meta.delete())
        session.execute(audit_meta.insert(), old_mark)
    # The surviving signed prefix alone looks healthy. The same-process floor must
    # still remember that the erased tightening existed; forged append is no reset.
    assert current_limits(st) == fail_closed_limits(st.settings)
    assert not st.audit.verify_chain().ok


def test_benign_append_during_limits_read_keeps_a_consistent_snapshot(st, monkeypatch):
    st.audit.append(EVENT_LIMITS_CHANGE, payload={"limits": {"deep_model_daily_budget": 4}})
    verify = st.audit.verify_committed_prefix_session

    def append_after_verifying(session):
        checked = verify(session)
        assert checked.ok
        st.audit.append(EVENT_LIMITS_CHANGE,
                        payload={"limits": {"deep_model_daily_budget": 2}})
        return checked

    monkeypatch.setattr(st.audit, "verify_committed_prefix_session", append_after_verifying)
    assert current_limits(st)["deep_model_daily_budget"] == 4
    monkeypatch.setattr(st.audit, "verify_committed_prefix_session", verify)
    assert current_limits(st)["deep_model_daily_budget"] == 2
    assert st.audit.verify_chain().ok
