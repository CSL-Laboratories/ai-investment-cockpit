"""BUG-034: whole-string Markdown fences are formatting, not schema bypasses."""
from __future__ import annotations

import json

from agent_runner.result import NEEDS_HUMAN, OK
from agent_runner.runner import run_agent
from agent_runner.spec import AgentSpec
from agent_runner.transport import ProcResult
from agent_runner.usage import UsageTracker

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["classification", "score"],
    "properties": {
        "classification": {
            "type": "string",
            "enum": ["ignore", "watch", "material", "urgent"],
        },
        "score": {"type": "integer"},
    },
}
VALID_BODY = {"classification": "material", "score": 70}


class FakeTransport:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def run(self, argv, input_text, timeout_s, cwd):
        self.calls.append({"argv": argv, "input": input_text})
        return self.results.pop(0)


def _spec(runtime="claude", **changes):
    values = {
        "job_id": "fence-job",
        "runtime": runtime,
        "model_alias": "triage" if runtime == "claude" else "codex",
        "prompt": "classify",
        "schema": SCHEMA,
        "decision_stage": runtime == "claude",
    }
    values.update(changes)
    return AgentSpec(**values)


def _fence(value):
    return f"```json\n{json.dumps(value)}\n```"


def _claude_fenced_body(body):
    return ProcResult(
        0,
        json.dumps({"session_id": "s1", "result": _fence(body)}),
        "",
        False,
    )


def _codex_fenced_body(body):
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": _fence(body)},
        },
        {"type": "turn.completed"},
    ]
    return ProcResult(0, "\n".join(json.dumps(event) for event in events), "", False)


def test_fenced_claude_result_is_accepted_without_repair():
    response = _claude_fenced_body(VALID_BODY)
    transport = FakeTransport([response, response])

    result = run_agent(_spec(), transport=transport, usage=UsageTracker())

    assert result.status == OK
    assert result.validated == VALID_BODY
    assert result.repaired is False
    assert len(transport.calls) == 1


def test_fenced_claude_envelope_is_accepted():
    envelope = {"session_id": "s9", "result": json.dumps(VALID_BODY)}
    response = ProcResult(0, _fence(envelope), "", False)

    result = run_agent(
        _spec(),
        transport=FakeTransport([response, response]),
        usage=UsageTracker(),
    )

    assert result.status == OK
    assert result.validated == VALID_BODY
    assert result.session_id == "s9"


def test_fenced_codex_agent_message_is_accepted_without_repair():
    response = _codex_fenced_body(VALID_BODY)
    transport = FakeTransport([response, response])

    result = run_agent(
        _spec(runtime="codex"),
        transport=transport,
        usage=UsageTracker(),
    )

    assert result.status == OK
    assert result.validated == VALID_BODY
    assert result.repaired is False
    assert len(transport.calls) == 1


def test_trailing_prose_is_not_treated_as_a_whole_string_fence():
    body = _fence(VALID_BODY) + "\nHope that helps!"
    response = ProcResult(0, json.dumps({"session_id": "s1", "result": body}), "", False)

    result = run_agent(
        _spec(),
        transport=FakeTransport([response, response]),
        usage=UsageTracker(),
    )

    assert result.status == NEEDS_HUMAN


def test_fenced_schema_violation_still_quarantines():
    response = _claude_fenced_body(
        {"classification": "material", "score": 70, "execute_now": True}
    )

    result = run_agent(
        _spec(),
        transport=FakeTransport([response, response]),
        usage=UsageTracker(),
    )

    assert result.status == NEEDS_HUMAN
