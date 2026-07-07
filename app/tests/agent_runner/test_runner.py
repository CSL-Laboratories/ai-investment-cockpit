"""WP1b G1b — hermetic mocked-subprocess tests for the agent runner (no real claude/codex)."""
from __future__ import annotations

import json

import pytest

from agent_runner.result import LIMIT_HIT, NEEDS_HUMAN, OK, TIMEOUT
from agent_runner.runner import build_argv, continue_session, run_agent
from agent_runner.spec import AgentSpec
from agent_runner.transport import ProcResult
from agent_runner.usage import UsageTracker

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["classification", "score"],
    "properties": {
        "classification": {"type": "string", "enum": ["ignore", "watch", "material", "urgent"]},
        "score": {"type": "integer"},
    },
}
VALID_BODY = {"classification": "material", "score": 70}


class FakeTransport:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def run(self, argv, input_text, timeout_s, cwd):
        self.calls.append({"argv": argv, "input": input_text})
        return self._results.pop(0)


def envelope(body, session_id="s1"):
    # mimic `claude -p --output-format json`: result carried as a JSON string
    return ProcResult(0, json.dumps({"session_id": session_id, "result": json.dumps(body)}), "", False)


def spec(**kw):
    base = dict(job_id="j1", runtime="claude", model_alias="triage", prompt="classify this",
                schema=SCHEMA, decision_stage=True)
    base.update(kw)
    return AgentSpec(**base)


def test_happy_path():
    t = FakeTransport([envelope(VALID_BODY)])
    r = run_agent(spec(), transport=t, usage=UsageTracker())
    assert r.status == OK
    assert r.validated == VALID_BODY
    assert r.session_id == "s1"
    assert r.repaired is False


def test_repair_success():
    bad = envelope({"classification": "nope", "score": 70})   # enum violation, valid JSON
    t = FakeTransport([bad, envelope(VALID_BODY)])
    r = run_agent(spec(), transport=t, usage=UsageTracker())
    assert r.status == OK and r.repaired is True
    assert len(t.calls) == 2


def test_repair_fails_quarantine():
    bad = envelope({"classification": "nope", "score": 70})
    t = FakeTransport([bad, bad])
    r = run_agent(spec(), transport=t, usage=UsageTracker())
    assert r.status == NEEDS_HUMAN and r.repaired is True


def test_malformed_json_quarantines():
    garbage = ProcResult(0, "{ this is not json", "", False)
    t = FakeTransport([garbage, garbage])
    r = run_agent(spec(), transport=t, usage=UsageTracker())
    assert r.status == NEEDS_HUMAN


def test_timeout_fails_closed_and_trips_breaker():
    u = UsageTracker()
    t = FakeTransport([ProcResult(124, "", "[timeout]", True)])
    r = run_agent(spec(), transport=t, usage=u)
    assert r.status == TIMEOUT
    assert r.validated is None
    assert u.window("claude").state == "exhausted"   # a timeout opens the breaker (Inv 7)
    assert u.window("claude").generation == 1         # SEC-23 generation bump


def test_limit_hit_opens_breaker():
    u = UsageTracker()
    t = FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)])
    r = run_agent(spec(), transport=t, usage=u)
    assert r.status == LIMIT_HIT
    w = u.window("claude")
    assert w.state == "exhausted"
    assert w.generation == 1          # SEC-23 generation bump


def test_empty_output_fails_closed():
    u = UsageTracker()
    t = FakeTransport([ProcResult(0, "   ", "", False)])
    r = run_agent(spec(), transport=t, usage=u)
    assert r.status == LIMIT_HIT
    assert u.window("claude").state == "degraded"


def test_decision_stage_must_be_tool_starved():
    # SEC-06: refused before any subprocess runs
    with pytest.raises(ValueError, match="SEC-06"):
        build_argv(spec(allowed_tools=["Bash"]))


def test_decision_stage_argv_passes_none():
    argv = build_argv(spec())
    assert "--allowedTools" in argv
    assert argv[argv.index("--allowedTools") + 1] == "none"
    assert not ({"Bash", "WebFetch", "Read"} & set(argv))


def test_no_secret_in_argv_or_prompt():
    # Inv 4: a secret in the prompt is caught before spawning
    t = FakeTransport([envelope(VALID_BODY)])
    with pytest.raises(ValueError, match="Inv 4"):
        run_agent(spec(prompt="the key is sk-LEAK123456"), transport=t,
                  usage=UsageTracker(), secret_values=["sk-LEAK123456"])


def test_continue_session_resumes():
    t = FakeTransport([envelope(VALID_BODY, session_id="s1")])
    r = continue_session(spec(session_id="s1"), "why this size?", transport=t, usage=UsageTracker())
    assert r.status == OK
    argv = t.calls[0]["argv"]
    assert "--resume" in argv and argv[argv.index("--resume") + 1] == "s1"


def test_non_decision_codex_job_builds():
    argv = build_argv(spec(runtime="codex", model_alias="codex", decision_stage=False,
                           allowed_tools=["Read"]))
    assert argv[:2] == ["codex", "exec"]
    assert "--sandbox" in argv and argv[argv.index("--sandbox") + 1] == "read-only"


def test_codex_decision_stage_rejected():
    # SEC-06 / Inv 2: codex may never run a decision-stage job
    with pytest.raises(ValueError, match="Inv2"):
        build_argv(spec(runtime="codex", model_alias="codex", decision_stage=True))


def test_decision_job_requires_schema():
    # Inv 3: a decision-stage job without a schema is a misconfiguration, not an OK path
    with pytest.raises(ValueError, match="schema"):
        run_agent(spec(schema=None), transport=FakeTransport([]), usage=UsageTracker())


def test_is_error_envelope_fails_closed():
    # claude envelope flags an errored turn while exiting 0 -> health fault (SEC-23)
    env = ProcResult(
        0,
        json.dumps({"session_id": "s1", "is_error": True,
                    "subtype": "error_during_execution", "result": "{}"}),
        "", False,
    )
    u = UsageTracker()
    r = run_agent(spec(), transport=FakeTransport([env]), usage=u)
    assert r.status == LIMIT_HIT
    assert u.window("claude").state == "degraded"


def test_nan_infinity_quarantined():
    # NaN/Infinity must not validate as "number" — reject at decode -> quarantine
    nan = ProcResult(0, '{"session_id":"s1","result":{"classification":"material","score":NaN}}', "", False)
    r = run_agent(spec(), transport=FakeTransport([nan, nan]), usage=UsageTracker())
    assert r.status == NEEDS_HUMAN


def test_open_breaker_short_circuits():
    # once the vendor breaker is open, the runner refuses (watch-only) before spawning
    u = UsageTracker()
    u.trip("claude", "exhausted")
    t = FakeTransport([])          # must not be called
    r = run_agent(spec(), transport=t, usage=u)
    assert r.status == LIMIT_HIT and r.error == "breaker_open"
    assert t.calls == []


def test_repair_empty_output_fails_closed():
    bad = envelope({"classification": "nope", "score": 70})
    u = UsageTracker()
    t = FakeTransport([bad, ProcResult(0, "   ", "", False)])
    r = run_agent(spec(), transport=t, usage=u)
    assert r.status == NEEDS_HUMAN
    assert u.window("claude").state == "degraded"
