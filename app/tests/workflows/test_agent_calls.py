"""G8 — agent-call choke-point tests (Owner B).

Enforces the WP8 'agent_calls.py' contract of docs/build-scaffold-ultracode.md WP8 and
SEC-06/SEC-08/SEC-23/Inv 3/Inv 7 of docs/security-hardening-backlog.md: every decision
call is tool-starved, persists a provenance AgentJob row via record_state_change (HMAC
covered), stamps the usage-window generation into input_refs, and a limit_hit/timeout
enters L8 watch-only EXACTLY ONCE without changing the mode. Hermetic: FrozenClock, tmp
SQLite, FakeTransport (no real subprocesses).
"""
from __future__ import annotations

from sqlalchemy import select

from agent_runner.result import LIMIT_HIT, NEEDS_HUMAN, OK
from agent_runner.transport import ProcResult
from config.models import load_model_map
from config.settings import Mode
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
    result2, _ = _call(st, FakeTransport([]))
    assert result2.status == LIMIT_HIT and result2.error == "breaker_open"
    assert len(_events(st, EVENT_WATCH_ONLY)) == 1, "enter_watch_only must fire exactly once"


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


def test_generation_stamp_survives_a_trip(st):
    # exhaust (generation bumps), recover the window, then a fresh call stamps the new gen
    _call(st, FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)]))
    w = st.usage.window("claude")
    w.state, w.generation = "ok", w.generation + 1
    gen = w.generation
    _, job = _call(st, FakeTransport([envelope(VALID)]))
    assert job.input_refs["_generation"] == gen                        # SEC-23 mint-time stamp
