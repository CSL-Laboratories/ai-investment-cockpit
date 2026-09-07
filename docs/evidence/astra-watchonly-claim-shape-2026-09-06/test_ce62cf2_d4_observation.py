"""Research-only D4 side-effect observation; refusal assertion is expected red.

Run against an isolated ce62cf2 app archive with fake leaves and temporary state.
This supplements the unchanged original fixture, whose post-call observation is
interrupted by the stricter FSM's AuditIntegrityError after the broker call.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from config.settings import Settings
from core.clock import FrozenClock
from orchestrator.state import OrchestratorState
from schemas.models.audit_entry import AuditEntry
from sqlalchemy import select
from storage.audit import AuditIntegrityError
from storage.engine import create_all, make_engine, make_session_factory
from tests.brokers.test_paper_execution import OPERATOR_ID, _service

pytest_plugins = ["tests.conftest"]


@pytest.fixture
def st(tmp_path, monkeypatch, _no_ambient_cockpit_env):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "HALT"))
    monkeypatch.setenv("COCKPIT_AUDIT_HEAD_FILE", str(tmp_path / "audit-head.json"))
    engine = make_engine(f"sqlite:///{(tmp_path / 'diagnosis.db').as_posix()}")
    create_all(engine)
    state = OrchestratorState.build(
        make_session_factory(engine),
        hmac_key=b"astra-durability-synthetic-fixture",
        settings=Settings(_env_file=None),
        clock=FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)),
    )
    yield state
    engine.dispose()


def test_d4_invalid_audit_before_broker_must_produce_zero_submissions(st, monkeypatch):
    service, scenario, client = _service(st)
    original_intent = st.audit.intent
    original_submit = service._broker.submit_order
    observed = {"broker_entries": 0}

    def injected_intent(op, *args, **kwargs):
        entry = original_intent(op, *args, **kwargs)
        if op == "order_submit":
            with st.session_factory() as session, session.begin():
                row = session.execute(
                    select(AuditEntry).order_by(AuditEntry.seq)
                ).scalars().first()
                row.payload = {**row.payload, "synthetic_keyless_tamper": True}
            observed["audit_ok_after_intent"] = st.audit.verify_chain().ok
        return entry

    def counted_submit(request):
        observed["broker_entries"] += 1
        observed["audit_ok_at_broker_entry"] = st.audit.verify_chain().ok
        return original_submit(request)

    monkeypatch.setattr(st.audit, "intent", injected_intent)
    monkeypatch.setattr(service._broker, "submit_order", counted_submit)
    try:
        outcome = service.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve",
            confirm_symbol="AAPL", request_id="astra-d4-observation",
        )["status"]
    except AuditIntegrityError:
        outcome = "AuditIntegrityError"

    observed.update(
        case="ce62cf2_d4_post_broker_fsm_exception",
        outcome=outcome,
        fake_submissions=len(client.submitted),
        audit_ok_after_call=st.audit.verify_chain().ok,
    )
    print("OBSERVATION " + json.dumps(observed, sort_keys=True))
    assert observed["audit_ok_after_intent"] is False
    assert client.submitted == [], "invalid audit authority reached the fake broker before FSM refusal"
