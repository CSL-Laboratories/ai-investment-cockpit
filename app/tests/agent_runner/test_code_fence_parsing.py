"""BUG-034: whole-string Markdown fences are formatting, not schema bypasses."""
from __future__ import annotations

import json

import pytest

from agent_runner.result import NEEDS_HUMAN, OK
from agent_runner.runner import _strip_code_fence, run_agent
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


# ---------------------------------------------------------------------------------------
# salvaged from rw01 — the controls, the negative cases, and the unwrapper itself
# ---------------------------------------------------------------------------------------
def test_unfenced_output_still_works():
    """POSITIVE CONTROL. Every other test here proves a FENCED body is handled; without this,
    an unwrapper that broke the common path would still pass all of them."""
    plain = ProcResult(0, json.dumps({"session_id": "s1", "result": json.dumps(VALID_BODY)}),
                       "", False)
    r = run_agent(_spec(), transport=FakeTransport([plain]), usage=UsageTracker())
    assert r.status == OK and r.validated == VALID_BODY


@pytest.mark.parametrize("lang", ["json", "JSON", "", "json5"])
def test_a_fenced_body_is_accepted_whatever_the_language_tag(lang):
    """Models are inconsistent about the tag, and the tag carries no meaning here — the body
    is revalidated against the schema either way."""
    body = json.dumps(VALID_BODY)
    payload = json.dumps({"session_id": "s1", "result": f"```{lang}\n{body}\n```"})
    r = run_agent(_spec(), transport=FakeTransport([ProcResult(0, payload, "", False)]),
                  usage=UsageTracker())
    assert r.status == OK and r.validated == VALID_BODY


def test_a_fenced_body_that_violates_an_enum_still_quarantines():
    """NOT A WEAKENED GATE (Inv 3). Unwrapping the fence must not become a way to smuggle a
    body past the schema — main's existing case covers additionalProperties, this covers the
    enum, and they fail through different jsonschema paths."""
    bad = _claude_fenced_body({"classification": "definitely-not-an-enum-member", "score": 70})
    r = run_agent(_spec(), transport=FakeTransport([bad, bad]), usage=UsageTracker())
    assert r.status == NEEDS_HUMAN and r.repaired is True


def test_garbage_inside_a_fence_is_still_garbage():
    """A fence is not a promise that the contents parse."""
    fenced_junk = json.dumps({"session_id": "s1", "result": "```json\n{ not json at all\n```"})
    r = run_agent(_spec(), transport=FakeTransport([ProcResult(0, fenced_junk, "", False)] * 2),
                  usage=UsageTracker())
    assert r.status == NEEDS_HUMAN


def test_a_fence_marker_inside_the_json_does_not_break_extraction():
    """A rationale quoting backticks must not confuse the unwrapper — and if it ever did, the
    result must be a parse failure, never a WRONG parse."""
    body = {"classification": "watch", "score": 5}
    payload = json.dumps({"session_id": "s1",
                          "result": "```json\n" + json.dumps(body) + "\n```"})
    r = run_agent(_spec(), transport=FakeTransport([ProcResult(0, payload, "", False)]),
                  usage=UsageTracker())
    assert r.status == OK and r.validated == body


@pytest.mark.parametrize("raw,expected", [
    ("```json\n{}\n```", "{}"),
    ("```\n{}\n```", "{}"),                                        # bare fence
    ("  ```json\r\n{}\r\n```  ", "{}"),                            # CRLF + surrounding space
    ("{}", "{}"),                                                  # unfenced passes through
    ("```json\n{}\n``` trailing", "```json\n{}\n``` trailing"),     # not whole-string, untouched
    ("no fence here", "no fence here"),
    ("", ""),
])
def test_strip_code_fence_cases(raw, expected):
    """The unwrapper as a unit. The integration tests above cannot distinguish 'the fence was
    stripped correctly' from 'the JSON happened to parse anyway'."""
    assert _strip_code_fence(raw) == expected
