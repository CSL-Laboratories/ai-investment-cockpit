"""RW-01 bring-up — vendor health may NOT be decided by the model's own output.

Found live, and it is the third defect of this shape in a row: a healthy 187-second triage run
over 100 real news events was reported as a vendor usage limit, which tripped the breaker and
degraded the cockpit to watch-only.

The cause is one line. ``runner`` scanned ``stdout + stderr`` with ``detect.looks_like_limit``,
whose patterns include ``\\bcapacity\\b``, ``quota``, ``overloaded`` and ``too long``. That is
ordinary market vocabulary, and stdout is the MODEL'S payload — derived from untrusted feed
text. The batch contained a Reuters headline about an airline trimming its capacity outlook, the
model echoed "capacity" in its rationale, and the runner read that as Anthropic being out of
capacity.

Two properties are under test here:
  * a healthy run whose CONTENT mentions limit vocabulary must succeed (the false positive), and
  * a real vendor signal on the CLI's own channels must still fail closed (no weakened gate).

The second matters most: the fix must not buy a green run by loosening SEC-23. Detection of
every channel a vendor actually speaks through is asserted below.
"""
from __future__ import annotations

import json

import pytest

from agent_runner.detect import looks_like_limit
from agent_runner.result import LIMIT_HIT, OK
from agent_runner.runner import run_agent
from agent_runner.spec import AgentSpec
from agent_runner.transport import ProcResult
from agent_runner.usage import UsageTracker

SCHEMA = {
    "type": "object",
    "required": ["scored"],
    "properties": {"scored": {"type": "array"}},
}

# The actual headline from the live window that degraded the cockpit.
LIVE_HEADLINE = "British Airways-owner IAG trims capacity outlook; jet fuel bill eases - Reuters"


class FakeTransport:
    def __init__(self, results):
        self._results = list(results)

    def run(self, argv, input_text, timeout_s, cwd):
        return self._results.pop(0)


def envelope(body, session_id="s1"):
    return ProcResult(0, json.dumps({"session_id": session_id, "result": json.dumps(body)}),
                      "", False)


def spec(**kw):
    base = dict(job_id="j1", runtime="claude", model_alias="triage", prompt="triage this",
                schema=SCHEMA, decision_stage=True)
    base.update(kw)
    return AgentSpec(**base)


def test_the_live_headline_still_matches_the_detector():
    """PIN THE PREMISE. If this ever stops matching, the regression below is testing nothing —
    it would pass for the wrong reason and the real defect could come back unnoticed."""
    assert looks_like_limit(LIVE_HEADLINE), (
        "detect.py no longer matches the headline that caused the outage; the test below has "
        "quietly stopped covering the defect"
    )


def test_model_output_mentioning_limit_vocabulary_is_not_a_vendor_limit():
    """THE regression. Before the fix this returned LIMIT_HIT and tripped the breaker."""
    body = {"scored": [{"article_id": "finnhub:1", "classification": "material", "score": 61,
                        "rationale": LIVE_HEADLINE}]}
    u = UsageTracker()
    r = run_agent(spec(), transport=FakeTransport([envelope(body)]), usage=u)

    assert r.status == OK, "a healthy run was misread as a vendor limit because of its CONTENT"
    assert r.validated == body
    assert u.window("claude").state == "ok", "the vendor breaker tripped on model output"
    assert u.window("claude").generation == 0


@pytest.mark.parametrize("word", ["capacity", "quota", "overloaded", "too long", "rate limit",
                                  "usage limit", "429", "service unavailable"])
def test_no_limit_word_in_model_output_can_trip_the_breaker(word):
    """Injection boundary. Feed text is DATA, never control — and a breaker IS control. Without
    this, any feed could force the cockpit into watch-only by publishing the right headline."""
    body = {"scored": [{"article_id": "finnhub:1", "classification": "watch", "score": 30,
                        "rationale": f"the filing discusses {word} at length"}]}
    u = UsageTracker()
    r = run_agent(spec(), transport=FakeTransport([envelope(body)]), usage=u)
    assert r.status == OK and u.window("claude").state == "ok"


# ---------------------------------------------------------------------------------------
# NOT A WEAKENED GATE: every channel a vendor actually speaks through still fails closed
# ---------------------------------------------------------------------------------------
def test_a_limit_on_stderr_still_fails_closed():
    u = UsageTracker()
    t = FakeTransport([ProcResult(0, "", "Claude AI usage limit reached", False)])
    r = run_agent(spec(), transport=t, usage=u)
    assert r.status == LIMIT_HIT
    assert u.window("claude").state == "exhausted"
    assert u.window("claude").generation == 1      # SEC-23 generation bump


def test_raw_non_json_limit_text_on_stdout_still_fails_closed():
    """The CLI can talk on stdout — but only when it is NOT emitting a model payload. Unparseable
    stdout is the vendor, not the model, so it stays in scope."""
    u = UsageTracker()
    t = FakeTransport([ProcResult(0, "Error: rate limit exceeded, retry later", "", False)])
    r = run_agent(spec(), transport=t, usage=u)
    assert r.status == LIMIT_HIT
    assert u.window("claude").state == "exhausted"


def test_a_nonzero_exit_still_fails_closed_whatever_the_output_says():
    u = UsageTracker()
    t = FakeTransport([ProcResult(1, json.dumps({"result": "{}"}), "", False)])
    r = run_agent(spec(), transport=t, usage=u)
    assert r.status == LIMIT_HIT
    assert u.window("claude").state == "exhausted"


def test_an_error_envelope_still_fails_closed():
    """A limit can arrive as a well-formed envelope flagged is_error while exiting 0 — that check
    is separate from the string scan and must keep carrying the load."""
    u = UsageTracker()
    payload = json.dumps({"is_error": True, "subtype": "error_during_execution",
                          "result": "usage limit reached"})
    r = run_agent(spec(), transport=FakeTransport([ProcResult(0, payload, "", False)]), usage=u)
    assert r.status == LIMIT_HIT
    assert u.window("claude").state == "degraded"


def test_max_turns_envelope_still_fails_closed():
    u = UsageTracker()
    payload = json.dumps({"subtype": "error_max_turns", "result": "{}"})
    r = run_agent(spec(), transport=FakeTransport([ProcResult(0, payload, "", False)]), usage=u)
    assert r.status == LIMIT_HIT


def test_the_repair_attempt_uses_the_same_boundary():
    """The repair path had the identical bug. A repaired output that mentions capacity must be
    accepted, and a real limit during repair must still fail closed."""
    bad = envelope({"nope": True})                       # schema violation -> triggers repair
    good = {"scored": [{"article_id": "finnhub:1", "classification": "ignore", "score": 2,
                        "rationale": "refinery capacity commentary"}]}
    u = UsageTracker()
    r = run_agent(spec(), transport=FakeTransport([bad, envelope(good)]), usage=u)
    assert r.status == OK and r.repaired is True
    assert u.window("claude").state == "ok"


def test_a_real_limit_during_repair_still_fails_closed():
    """BUG-065: a vendor limit is a vendor limit on either round. This used to return
    NEEDS_HUMAN('repair_failed'), which call_agent mapped to a 'quarantined' job — a
    SCHEMA label — and the audited watch-only entry never fired. LIMIT_HIT routes it
    through the same EVENT_WATCH_ONLY path as a first-round limit."""
    bad = envelope({"nope": True})
    u = UsageTracker()
    t = FakeTransport([bad, ProcResult(0, "", "Error: usage limit reached", False)])
    r = run_agent(spec(), transport=t, usage=u)
    assert r.status == LIMIT_HIT and r.error == "repair_limit_or_error"
    assert r.repaired is True
    assert u.window("claude").state == "exhausted"


# =======================================================================================
# The CODEX runtime — the hole rw01's version could not have known about
# =======================================================================================
def codex_jsonl(body, *, session_id="t1") -> ProcResult:
    """Codex's promised protocol: a JSONL event stream, not one envelope. The assistant's
    answer arrives as an ``item.completed`` event whose item is an ``agent_message``."""
    lines = [
        {"type": "thread.started", "thread_id": session_id},
        {"type": "item.completed",
         "item": {"type": "agent_message", "text": json.dumps(body)}},
        {"type": "turn.completed", "status": "completed"},
    ]
    return ProcResult(0, "\n".join(json.dumps(line) for line in lines), "", False)


def test_codex_model_output_mentioning_limit_vocabulary_is_not_a_vendor_limit():
    """NEITHER PRIOR VERSION HANDLED THIS.

    rw01 decided "is this the model talking?" with ``json.loads`` over the WHOLE stdout
    buffer. That is right for Claude's single envelope and wrong for Codex, whose JSONL never
    parses as one object — so the guard fell through and scanned the model's own
    ``agent_message`` text, reopening SEC-25 for that runtime. rw01 predates Codex support.

    main had the opposite problem: stderr-only, so it never scanned stdout at all and could
    not be injected — at the cost of missing genuine limits printed there (covered by
    ``test_raw_non_json_limit_text_on_stdout_still_fails_closed``).
    """
    body = {"scored": [{"article_id": "finnhub:1", "classification": "material", "score": 61,
                        "rationale": LIVE_HEADLINE}]}
    result = run_agent(spec(runtime="codex"),
                       transport=FakeTransport([codex_jsonl(body)]),
                       usage=UsageTracker())
    assert result.status == OK, (
        f"the model's own text tripped the vendor breaker on codex: {result.error}"
    )
    assert result.validated == body


@pytest.mark.parametrize("word", ["usage limit reached", "rate limit", "quota exceeded",
                                  "overloaded", "capacity"])
def test_no_limit_word_inside_a_codex_agent_message_can_trip_the_breaker(word):
    """The injection form: a FEED could put any of these in a headline, and the model
    faithfully echoes it into its rationale. Feed text is data, never control."""
    body = {"scored": [{"article_id": "finnhub:1", "classification": "material", "score": 61,
                        "rationale": f"the airline {word} on the London route"}]}
    result = run_agent(spec(runtime="codex"),
                       transport=FakeTransport([codex_jsonl(body)]),
                       usage=UsageTracker())
    assert result.status == OK, f"{word!r} in a codex agent_message tripped the breaker"


def test_a_real_limit_on_codex_stderr_still_fails_closed():
    """CONTROL. The runtime-aware payload check must not make codex runs unstoppable — the
    vendor's own channels still speak."""
    body = {"scored": []}
    proc = codex_jsonl(body)
    result = run_agent(spec(runtime="codex"),
                       transport=FakeTransport([ProcResult(0, proc.stdout,
                                                           "Error: usage limit reached", False)]),
                       usage=UsageTracker())
    assert result.status == LIMIT_HIT


def test_a_malformed_codex_stream_is_not_treated_as_a_model_payload():
    """Half-JSONL, half-garbage is not the promised protocol, so it is the CLI talking and the
    scan applies. Fail closed: this is the case where we genuinely cannot attribute the bytes.
    """
    stdout = json.dumps({"type": "thread.started"}) + "\nusage limit reached, retry later\n"
    result = run_agent(spec(runtime="codex"),
                       transport=FakeTransport([ProcResult(0, stdout, "", False)]),
                       usage=UsageTracker())
    assert result.status == LIMIT_HIT


# =======================================================================================
# BUG-063: stderr channel attribution — a prompt echo is not vendor speech
# =======================================================================================
def test_prompt_echo_on_stderr_cannot_trip_the_breaker():
    """transport.py documents that 'a CLI can echo its prompt back on a parse error, and
    the prompt carries untrusted feed text' — yet stderr was scanned with no attribution,
    so the echoed IAG headline tripped LIMIT_HIT from the supposedly-excluded channel."""
    from agent_runner.runner import _limit_signal

    prompt = ("triage this packet:\n"
              "British Airways-owner IAG trims capacity outlook for the summer\n"
              "second headline about oil quota decisions\n")
    echoed = ("parse error near line 2:\n"
              "British Airways-owner IAG trims capacity outlook for the summer\n")
    assert _limit_signal("", echoed, prompt) is False


def test_a_real_vendor_limit_on_stderr_still_trips_with_a_prompt_present():
    from agent_runner.runner import _limit_signal

    prompt = "triage this packet:\nsome ordinary headline\n"
    assert _limit_signal("", "Error: usage limit reached", prompt) is True


def test_engineered_vendor_phrase_in_the_prompt_is_suppressed_not_obeyed():
    """A headline that CONTAINS the vendor phrase must not become control when echoed —
    feed text is data (M2/M3). The other channels (exit code, envelope) stay live."""
    from agent_runner.runner import _limit_signal

    prompt = "packet:\nBREAKING: usage limit reached says analyst note\n"
    echoed = "BREAKING: usage limit reached says analyst note"
    assert _limit_signal("", echoed, prompt) is False


# =======================================================================================
# BUG-064: one malformed JSONL line must not re-expose the model's text to the scan
# =======================================================================================
def test_mixed_jsonl_scans_only_the_cli_lines():
    """A truncated tail / vendor banner amid valid JSONL used to downgrade the WHOLE
    stream to 'not a model payload', letting the model's own agent_message words
    ('capacity') trip the breaker — SEC-25 reopened for codex by a single stray line."""
    from agent_runner.runner import _limit_signal

    model_line = json.dumps({"type": "item.completed",
                             "item": {"type": "agent_message",
                                      "text": "IAG trims capacity outlook; quota chatter"}})
    stray = "warning: telemetry upload skipped"
    assert _limit_signal(model_line + "\n" + stray, "") is False


def test_a_real_limit_line_amid_jsonl_still_trips():
    from agent_runner.runner import _limit_signal

    model_line = json.dumps({"type": "item.completed",
                             "item": {"type": "agent_message", "text": "all quiet today"}})
    assert _limit_signal(model_line + "\nError: usage limit reached", "") is True
