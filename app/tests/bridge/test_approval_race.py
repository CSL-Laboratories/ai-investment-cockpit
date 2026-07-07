"""WP6 approval hardening — the reproduced duplicate-execution race, now CLOSED (SEC-07).

The 4c pass reproduced an idempotency race: two concurrent deliveries of the SAME Discord
interaction_id (a Discord retry / concurrent callbacks) both passed the check-then-act read
before either committed, minting TWO ApprovalRecords + TWO single-use tokens from ONE operator
click — driving a duplicate real order. record_decision now runs the read->persist->mint under
the shared lock (in-process atomicity) with a UNIQUE constraint on interaction_id as the
cross-process backstop.
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bridge.approval import record_decision
from brokers.capability import TokenRegistry
from config.settings import Mode
from orchestrator.state import EVENT_ADJUDICATION
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry

from tests.bridge.conftest import OPERATOR, make_order, make_risk


def _approve_adjudications(st):
    with st.session_factory() as s:
        return [e for e in s.execute(select(AuditEntry)).scalars().all()
                if e.event_type == EVENT_ADJUDICATION and (e.payload or {}).get("decision") == "approve"]


def test_concurrent_same_interaction_id_yields_one_record_one_token(st):
    rd = make_risk()
    order = make_order()
    reg = TokenRegistry()
    results, errors = [], []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()                             # maximize the race window
            results.append(record_decision(
                st, proposal_id="prop-1", risk_decision=rd, resolved_order=order,
                actor_user_id=OPERATOR, user_decision="approve", surface="discord",
                interaction_id="i-REPLAY", token_registry=reg, mode=Mode.review_live))
        except Exception as e:                         # noqa: BLE001 - surface any thread error
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert reg.live_count() == 1                       # ONE token, not two
    with st.session_factory() as s:
        apprs = s.execute(select(ApprovalRecord)).scalars().all()
    assert len(apprs) == 1                             # ONE ApprovalRecord
    assert len(_approve_adjudications(st)) == 1        # ONE covering adjudication
    assert len({r.id for r in results}) == 1          # both callers got the SAME record


def test_interaction_id_unique_constraint_enforced_at_db(st):
    """The cross-process backstop: two ApprovalRecords with the same non-null interaction_id
    cannot both persist (SQLite UNIQUE), while null interaction_ids stay unconstrained."""
    now = st.clock.now()

    def _row(rid, interaction_id):
        return ApprovalRecord(
            id=rid, proposal_id="p1", risk_decision_id="rd1", actor_user_id=OPERATOR,
            user_decision="approve", surface="discord", interaction_id=interaction_id,
            order_tuple_hash="h", single_use_consumed=False, decided_at=now, created_at=now)

    with st.session_factory() as s, s.begin():
        s.add(_row("a1", "dup"))
    with pytest.raises(IntegrityError):
        with st.session_factory() as s, s.begin():
            s.add(_row("a2", "dup"))                   # same interaction_id -> rejected
    # two desktop-fallback approvals (interaction_id is NULL) are allowed (NULLs are distinct)
    with st.session_factory() as s, s.begin():
        s.add(_row("a3", None))
        s.add(_row("a4", None))
