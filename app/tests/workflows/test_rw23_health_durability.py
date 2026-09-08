"""BUG-147: every vendor health fault survives a composed restart on either runtime."""
import json

import pytest
from sqlalchemy import select

from agent_runner.result import LIMIT_HIT, NEEDS_HUMAN, OK, TIMEOUT
from agent_runner.transport import ProcResult
from config.settings import DecisionRuntime
from orchestrator.breakers import BreakerEngine, StaleAnalysisError
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_WATCH_ONLY, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from tests.workflows.conftest import KEY, NOW, FakeTransport, envelope
from tests.workflows.test_agent_calls import VALID, _call, _codex_envelope


def _response(runtime, body):
    return _codex_envelope(body) if runtime == "codex" else envelope(body)


def _failure(runtime, kind):
    if kind == "empty":
        return ProcResult(0, " \n", "", False)
    if kind == "timeout":
        return ProcResult(124, "", "[timeout]", True)
    if kind == "limit":
        return ProcResult(0, "", "usage limit reached", False)
    value = ({"type": "turn.failed", "error": {"message": "failed"}}
             if runtime == "codex" else {"is_error": True, "subtype": "error_max_turns"})
    return ProcResult(0, json.dumps(value), "", False)


def _watch_rows(st):
    with st.session_factory() as session:
        return session.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_WATCH_ONLY
        )).scalars().all()


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("kind", ["empty", "vendor", "timeout", "limit"])
def test_health_fault_persists_and_invalidates_old_analysis(st, runtime, repair, kind):
    st.settings.decision_runtime = DecisionRuntime(runtime)
    _, old_job = _call(st, FakeTransport([_response(runtime, VALID)]))
    responses = [_response(runtime, {"invalid": True})] if repair else []
    responses.append(_failure(runtime, kind))
    result, job = _call(st, FakeTransport(responses))
    assert result.status == (TIMEOUT if kind == "timeout" else LIMIT_HIT)
    assert result.repaired is repair
    assert job.status == "failed"
    rows = _watch_rows(st)
    assert len(rows) == 1 and st.audit.verify_entry(rows[0])
    generation = st.usage.window(runtime).generation
    assert generation > old_job.input_refs["_generation"]

    restarted = OrchestratorState.build(st.session_factory, hmac_key=KEY,
        settings=st.settings, clock=st.clock)
    breaker = BreakerEngine(restarted)
    assert breaker.is_watch_only(runtime)
    assert restarted.usage.window(runtime).generation == generation
    with pytest.raises(StaleAnalysisError):
        breaker.assert_fresh_analysis(runtime, old_job.input_refs["_generation"])
    blocked, _ = _call(restarted, FakeTransport([]))
    assert blocked.status == LIMIT_HIT and blocked.error == "breaker_open"
    assert len(_watch_rows(restarted)) == 1

    breaker.recover_from_watch_only(runtime, ModeLadder(restarted).current())
    recovered = OrchestratorState.build(st.session_factory, hmac_key=KEY,
        settings=st.settings, clock=st.clock)
    result, new_job = _call(recovered, FakeTransport([_response(runtime, VALID)]))
    assert result.status == OK
    assert new_job.input_refs["_generation"] > generation
    assert recovered.audit.verify_chain().ok


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_schema_quarantine_does_not_manufacture_health_fault(st, runtime):
    st.settings.decision_runtime = DecisionRuntime(runtime)
    bad = _response(runtime, {"generated_at": NOW.isoformat(), "invalid": True})
    result, job = _call(st, FakeTransport([bad, bad]))
    assert result.status == NEEDS_HUMAN and job.status == "quarantined"
    assert not BreakerEngine(st).is_watch_only()
    assert _watch_rows(st) == []
