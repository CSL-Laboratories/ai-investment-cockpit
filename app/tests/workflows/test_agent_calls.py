"""G8 — agent-call choke-point tests (Owner B).

Enforces the WP8 'agent_calls.py' contract of docs/build-scaffold-ultracode.md WP8 and
SEC-06/SEC-08/SEC-23/Inv 3/Inv 7 of docs/security-hardening-backlog.md: every decision
call is tool-starved, persists a provenance AgentJob row via record_state_change (HMAC
covered), stamps the usage-window generation into input_refs, and a limit_hit/timeout
enters L8 watch-only EXACTLY ONCE without changing the mode. Hermetic: FrozenClock, tmp
SQLite, FakeTransport (no real subprocesses).
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from agent_runner.result import LIMIT_HIT, NEEDS_HUMAN, OK
from agent_runner.transport import ProcResult
from config.models import load_model_map
from config.settings import DecisionRuntime, Mode
from orchestrator.breakers import BreakerEngine
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_WATCH_ONLY
from schemas.models.agent_job import AgentJob
from schemas.models.audit_entry import AuditEntry
from tests.workflows.conftest import NOW, FakeTransport, envelope
from workflows.agent_calls import call_agent
from workflows.constants import EVENT_AGENT_JOB

VALID = {"question": "q?", "answer": "grounded answer", "pattern": "fresh",
         "session_id": None, "generated_at": NOW.isoformat()}


def _codex_envelope(body, thread_id="thread-codex"):
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {
            "id": "item-1", "type": "agent_message", "text": json.dumps(body),
        }},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]
    return ProcResult(0, "\n".join(json.dumps(event) for event in events), "", False)


def _call(st, transport, **kw):
    base = dict(alias="triage", prompt_ref="interrogation_fresh.v1", prompt_hash="h" * 16,
                prompt="answer the question", schema_name="InterrogationAnswer",
                input_refs={"raw_refs": ["parquet://2026-07-07/events-x#0"]},
                transport=transport)
    base.update(kw)
    return call_agent(st, **base)


def _jobs(st):
    with st.session_factory() as s:
        return s.execute(select(AgentJob)).scalars().all()


def _events(st, event_type):
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == event_type)
        ).scalars().all()
    return [e for e in entries if st.audit.verify_entry(e)]


def test_ok_persists_provenance_job(st):
    t = FakeTransport([envelope(VALID, session_id="sess-9")])
    result, job = _call(st, t)
    assert result.status == OK and result.validated == VALID

    rows = _jobs(st)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "done"
    assert row.runtime == "claude" and row.model == "triage"
    assert row.model_pinned == load_model_map()["triage"]["pinned"]   # E-06: pin, not alias
    assert row.prompt_template == "interrogation_fresh.v1"
    assert row.prompt_hash == "h" * 16
    assert row.schema_id == "InterrogationAnswer"
    assert row.session_id == "sess-9"
    assert row.input_refs["_generation"] == 0                          # SEC-23 stamp
    assert row.input_refs["raw_refs"] == ["parquet://2026-07-07/events-x#0"]

    covered = _events(st, EVENT_AGENT_JOB)
    assert len(covered) == 1
    assert covered[0].refs["row_id"] == row.id                         # Inv 6 coverage
    assert covered[0].payload["status"] == "done"


def test_codex_selected_runtime_runs_same_job_and_records_codex_pin(st):
    st.settings.decision_runtime = DecisionRuntime.codex
    t = FakeTransport([_codex_envelope(VALID)])
    result, job = _call(st, t)

    assert result.status == OK and result.validated == VALID
    assert job.runtime == "codex"
    assert job.model == "triage"
    assert job.model_pinned == "gpt-5.5"
    argv = t.calls[0]["argv"]
    assert "--ephemeral" in argv and "--ignore-user-config" in argv
    assert argv[argv.index("--output-schema") + 1].endswith("InterrogationAnswer.schema.json")


def test_codex_selected_runtime_refuses_live_mode_before_spawn(st):
    st.settings.decision_runtime = DecisionRuntime.codex
    ladder = ModeLadder(st)
    ladder.set_mode(Mode.paper, actor="op")
    # Simulate a persisted live rung from a previous Claude-configured process. The workflow
    # policy still refuses Codex before the transport is touched; the gateway repeats this pin.
    st.settings.decision_runtime = DecisionRuntime.claude
    ladder.set_mode(Mode.review_live, actor="op")
    st.settings.decision_runtime = DecisionRuntime.codex

    t = FakeTransport([])
    with pytest.raises(RuntimeError, match="not allowed"):
        _call(st, t)
    assert t.calls == []


def test_argv_is_tool_starved_and_never_resumes(st):
    t = FakeTransport([envelope(VALID)])
    _call(st, t)
    argv = t.calls[0]["argv"]
    assert argv[argv.index("--allowedTools") + 1] == "none"            # SEC-06
    assert "--resume" not in argv                                      # SEC-18 posture
    assert "--model" in argv


def test_limit_hit_enters_watch_only_once_and_mode_unchanged(st):
    mode_before = ModeLadder(st).current()
    t = FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)])
    result, job = _call(st, t)
    assert result.status == LIMIT_HIT
    assert job.status == "failed"
    assert BreakerEngine(st).is_watch_only("claude") is True
    assert ModeLadder(st).current() == mode_before == Mode.read_only   # L8 != mode change
    assert len(_events(st, EVENT_WATCH_ONLY)) == 1

    # a second call while the breaker is open short-circuits and does NOT re-enter
    result2, job2 = _call(st, FakeTransport([]))
    assert result2.status == LIMIT_HIT and result2.error == "breaker_open"
    assert result2.dispatched is False
    assert len(_events(st, EVENT_WATCH_ONLY)) == 1, "enter_watch_only must fire exactly once"
    # The breaker refused before any subprocess. That refusal is not a charge.
    refused = [
        e.payload["cost"] for e in _events(st, EVENT_AGENT_JOB)
        if e.payload["cost"].get("refused") == "breaker_open"
    ]
    assert len(refused) == 1
    assert refused[0]["billed"] is False
    assert refused[0]["usd_scope"] == "not_sent"
    assert refused[0]["usd"] == "0.00000000"
    assert job2.usage_estimate == Decimal("0")


def test_timeout_enters_watch_only(st):
    t = FakeTransport([ProcResult(124, "", "[timeout]", True)])
    result, _job = _call(st, t)
    assert result.status == "timeout"
    assert BreakerEngine(st).is_watch_only("claude") is True
    assert len(_events(st, EVENT_WATCH_ONLY)) == 1


def test_still_malformed_output_quarantines_job(st):
    bad = envelope({"question": "q?", "pattern": "nope"})   # schema-invalid twice -> Inv 3
    t = FakeTransport([bad, bad])
    result, job = _call(st, t)
    assert result.status == NEEDS_HUMAN
    assert job.status == "quarantined"
    assert _jobs(st)[0].status == "quarantined"
    assert BreakerEngine(st).is_watch_only("claude") is False  # quarantine is NOT exhaustion


def test_agent_job_audit_records_fixed_result_outcome(st):
    bad = envelope({"question": "q?", "pattern": "nope"})
    result, _job = _call(st, FakeTransport([bad, bad]))

    assert result.status == NEEDS_HUMAN
    payload = _events(st, EVENT_AGENT_JOB)[0].payload
    assert payload["result_error"] == "repair_still_invalid"
    assert payload["repaired"] is True


def test_generation_stamp_survives_a_trip(st):
    # exhaust (generation bumps), recover the window, then a fresh call stamps the new gen
    _call(st, FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)]))
    w = st.usage.window("claude")
    w.state, w.generation = "ok", w.generation + 1
    gen = w.generation
    _, job = _call(st, FakeTransport([envelope(VALID)]))
    assert job.input_refs["_generation"] == gen                        # SEC-23 mint-time stamp


def test_repair_round_limit_enters_watch_only_with_audit(st):
    """BUG-065: a limit on the REPAIR round used to come back NEEDS_HUMAN — job status
    'quarantined' (a schema label), no EVENT_WATCH_ONLY, exhaustion invisible until some
    LATER call observed the open breaker. It must take the same audited path as a
    first-round limit."""
    bad = envelope({"nope": True})                                     # schema-invalid -> repair
    t = FakeTransport([bad, ProcResult(0, "", "Error: usage limit reached", False)])
    result, job = _call(st, t)
    assert result.status == LIMIT_HIT and result.error == "repair_limit_or_error"
    assert job.status == "failed", "a vendor limit is not a schema quarantine"
    assert BreakerEngine(st).is_watch_only("claude") is True
    assert len(_events(st, EVENT_WATCH_ONLY)) == 1, "the degradation must be on the chain"


def test_provisioned_secret_in_the_prompt_is_refused(st):
    """BUG-092: production hardcoded secret_values=None, so the Inv 4 argv/prompt check
    iterated an empty list and could never fire. call_agent now feeds it the provisioned
    keyring values — a prompt carrying one is refused before any spawn."""
    import keyring

    keyring.set_password("ai-investment-cockpit", "finnhub_key", "sekret-value-123")
    try:
        with pytest.raises(ValueError, match="Inv 4"):
            _call(st, FakeTransport([]),
                  prompt="summarize with key sekret-value-123 attached")
    finally:
        keyring.delete_password("ai-investment-cockpit", "finnhub_key")
