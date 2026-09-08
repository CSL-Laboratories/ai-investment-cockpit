"""Independent RW24 check: cached triage corroboration cannot outlive spend authority."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from schemas.models.audit_entry import AuditEntry
from tests.workflows.conftest import NOW
from workflows.budget import (
    BudgetIntegrityError,
    DeepBudget,
    deterministic_urgency,
    make_event_type_verifier,
)
from workflows.constants import EVENT_DEEP_ESCALATION, EVENT_INGEST_BATCH


@pytest.mark.parametrize("corroboration", ["filing", "calendar"])
@pytest.mark.parametrize("witness_failure", ["missing", "mismatch", "unavailable"])
def test_cached_corroboration_cannot_spend_after_witness_fails(st, corroboration, witness_failure):
    event = SimpleNamespace(id="target", event_type="filing", symbol="AAPL", ts=NOW)
    calendars = []
    binding = event
    if corroboration == "calendar":
        event.event_type = "news"
        binding = SimpleNamespace(id="calendar", event_type="calendar:earnings", symbol="AAPL", ts=NOW)
        calendars = [binding]
    st.audit.append(EVENT_INGEST_BATCH, payload={"event_types": {binding.id: binding.event_type}})
    head = st.audit.head()
    witness = {"head": head, "fail": False}

    def latest():
        if witness["fail"]:
            raise OSError("isolated witness outage")
        return witness["head"]

    st.anchor_store = SimpleNamespace(latest=latest)
    verify = make_event_type_verifier(st)
    ok, reason = deterministic_urgency(event, calendars, now=NOW, verify_event_type=verify)
    assert ok
    if witness_failure == "missing":
        witness["head"] = None
    elif witness_failure == "mismatch":
        witness["head"] = (head[0], "0" * 64)
    else:
        witness["fail"] = True

    # This lifetime intentionally retains its pinned triage snapshot. The next tick
    # fails closed, and both corroboration paths must independently fail at spend.
    assert verify(binding)
    assert not make_event_type_verifier(st)(binding)
    with pytest.raises((BudgetIntegrityError, OSError)):
        DeepBudget(st, limit=5).try_consume(
            day=NOW.date(), event_id=event.id, symbol="AAPL", reason=reason, corroborated=ok,
        )
    with st.session_factory() as session:
        assert session.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_DEEP_ESCALATION,
        )).scalars().all() == []
