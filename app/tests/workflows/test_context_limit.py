"""CSL-42 — context cap, Haiku decision routing, and the per-call cost log.

Paper/sim only. No broker, no OrderGateway, no live capital.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from agent_runner.result import OK
from agent_runner.transport import ProcResult
from config.models import (
    DEFAULT_DECISION_MODEL,
    job_class_for,
    resolve_decision_claude,
    runtime_for,
)
from config.settings import DecisionRuntime, Mode, Settings
from orchestrator.breakers import BreakerEngine
from orchestrator.mode_ladder import ModeLadder
from schemas.models.audit_entry import AuditEntry
from tests.workflows.conftest import NOW, FakeTransport, envelope
from workflows.agent_calls import call_agent
from workflows.constants import EVENT_AGENT_JOB
from workflows.context_limit import estimate_tokens, fit_packet
from workflows.cost_log import parse_vendor_usage, quote_cost
from workflows.packets import Packet, PacketItem

VALID = {
    "question": "q?",
    "answer": "grounded answer",
    "pattern": "fresh",
    "session_id": None,
    "generated_at": NOW.isoformat(),
}


def _item(n: int, chars: int) -> PacketItem:
    return PacketItem(
        event_id=f"e{n}",
        raw_ref=f"ref-{n}",
        symbol="AAPL",
        text="x" * chars,
    )


def _call(st, transport, **kw):
    base = dict(
        alias="triage",
        prompt_ref="interrogation_fresh.v1",
        prompt_hash="h" * 16,
        prompt="answer the question",
        schema_name="InterrogationAnswer",
        input_refs={"raw_refs": ["parquet://x"]},
        transport=transport,
    )
    base.update(kw)
    return call_agent(st, **base)


def _events(st):
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_AGENT_JOB)
        ).scalars().all()
    return [e for e in entries if st.audit.verify_entry(e)]


def test_estimate_is_at_least_the_dense_tokenizer_rule():
    text = "AAPL filed an 8-K. " * 50
    # Claude 4.7+ is about chars/3.08 on English. bytes/3 must not under-count that.
    assert estimate_tokens(text) >= len(text.encode("utf-8")) // 4
    assert estimate_tokens("") == 0


def test_fit_packet_drops_the_tail_and_keeps_a_prefix():
    packet = Packet(items=tuple(_item(i, 300) for i in range(5)))
    reserved = 10
    two = "\n\n".join(item.text for item in packet.items[:2])
    cap = reserved + estimate_tokens(two)
    fit = fit_packet(packet, reserved_tokens=reserved, cap=cap)
    assert fit.fits
    assert [item.event_id for item in fit.packet.items] == ["e0", "e1"]
    assert [item.event_id for item in fit.dropped] == ["e2", "e3", "e4"]
    assert fit.packet.dispatched_refs.isdisjoint(item.raw_ref for item in fit.dropped)


def test_fit_packet_is_identity_under_the_cap():
    packet = Packet(items=(_item(1, 20),))
    fit = fit_packet(packet, reserved_tokens=5, cap=10_000)
    assert fit.fits and fit.dropped == ()
    assert fit.packet.dispatched_refs == packet.dispatched_refs


def test_fit_packet_refuses_when_the_template_alone_exceeds_the_cap():
    packet = Packet(items=(_item(1, 50),))
    fit = fit_packet(packet, reserved_tokens=100, cap=40)
    assert fit.fits is False
    assert fit.packet.items == ()
    assert fit.dropped == packet.items


def test_haiku_55_short_decision_matches_csl98_example():
    # 8k in + 1k out at the short Haiku 5.5 rate is about $0.0013.
    quote = quote_cost(
        "claude-haiku-5-5", input_tokens=8_000, output_tokens=1_000,
        input_token_source="vendor",
    )
    assert quote.rate_tier == "haiku-5.5-short"
    assert quote.usd == "0.00130000"
    assert Decimal(quote.usd) == Decimal("0.00130000")


def test_haiku_45_decision_matches_csl98_example():
    quote = quote_cost(
        "claude-haiku-4-5-20251001", input_tokens=8_000, output_tokens=1_000,
        input_token_source="vendor",
    )
    assert quote.rate_tier == "haiku-4.5"
    assert quote.usd == "0.01300000"


def test_haiku_55_long_context_uses_the_five_x_rate():
    short = quote_cost(
        "claude-haiku-5-5", input_tokens=100_000, output_tokens=0,
        input_token_source="estimate",
    )
    long = quote_cost(
        "claude-haiku-5-5", input_tokens=100_001, output_tokens=0,
        input_token_source="estimate",
    )
    assert short.rate_tier == "haiku-5.5-short"
    assert long.rate_tier == "haiku-5.5-long"
    assert Decimal(long.usd) == Decimal(short.usd) / Decimal("100000") * Decimal("100001") * 5


def test_unknown_model_is_unpriced():
    quote = quote_cost(
        "claude-opus-4-8", input_tokens=100, output_tokens=10, input_token_source="vendor",
    )
    assert quote.usd is None
    assert quote.rate_tier == "unpriced"


def test_vendor_usage_reads_claude_envelope_and_ignores_prose():
    raw = json.dumps({"usage": {"input_tokens": 12, "output_tokens": 3}, "result": "capacity"})
    assert parse_vendor_usage(raw) == (12, 3)
    assert parse_vendor_usage("the model said input_tokens 999") is None


def test_settings_cap_cannot_reach_the_surcharge_tier():
    assert Settings().context_token_cap == 90_000
    assert Settings().decision_model == DEFAULT_DECISION_MODEL
    with pytest.raises(ValidationError):
        Settings(context_token_cap=100_000)
    with pytest.raises(ValidationError):
        Settings(decision_model="opus")
    with pytest.raises(ValidationError):
        Settings(decision_model="gpt-5.5")


def test_summary_is_cheap_haiku_and_decision_stays_claude():
    assert job_class_for("summary") == "summary"
    assert job_class_for("triage") == "classification"
    assert job_class_for("decision") == "decision"
    assert runtime_for("summary") == "claude"
    assert runtime_for("decision") == "claude"
    assert runtime_for("decision", "codex") == "codex"
    cli, pinned = resolve_decision_claude("claude-haiku-5-5")
    assert cli == "claude-haiku-5-5" and pinned == "claude-haiku-5-5"
    cli, pinned = resolve_decision_claude("claude-haiku-4-5")
    assert pinned == "claude-haiku-4-5-20251001"


def test_call_under_cap_logs_estimated_haiku_cost(st):
    transport = FakeTransport([envelope(VALID, session_id="sess-cost")])
    result, job = _call(st, transport)
    assert result.status == OK
    assert job.usage_estimate is not None
    assert job.model_pinned == "claude-haiku-4-5-20251001"
    covered = _events(st)
    assert len(covered) == 1
    cost = covered[0].payload["cost"]
    assert cost["billed"] is True
    assert cost["rate_tier"] == "haiku-4.5"
    assert cost["input_token_source"] == "estimate"
    assert cost["context_token_cap"] == 90_000
    assert Decimal(cost["usd"]) == job.usage_estimate
    assert covered[0].payload["job_class"] == "classification"


def test_vendor_usage_prices_input_and_output(st):
    body = json.dumps(VALID)
    raw = json.dumps({
        "session_id": "sess-u",
        "usage": {"input_tokens": 8_000, "output_tokens": 1_000},
        "result": body,
    })
    transport = FakeTransport([ProcResult(0, raw, "", False)])
    result, job = _call(st, transport, alias="summary")
    assert result.status == OK
    cost = _events(st)[0].payload["cost"]
    assert cost["input_token_source"] == "vendor"
    assert cost["usd_scope"] == "input_output"
    assert cost["usd"] == "0.01300000"
    assert job.usage_estimate == Decimal("0.01300000")
    assert _events(st)[0].payload["job_class"] == "summary"


def test_over_cap_does_not_spawn_or_open_watch_only(st):
    transport = FakeTransport([])
    st.settings.context_token_cap = 30
    result, job = _call(st, transport, prompt="x" * 500)
    assert transport.calls == []
    assert result.status == "error" and result.error == "context_limit"
    assert job.status == "failed"
    assert job.usage_estimate == Decimal("0")
    assert BreakerEngine(st).is_watch_only("claude") is False
    cost = _events(st)[0].payload["cost"]
    assert cost["billed"] is False
    assert cost["refused"] == "context_limit"
    assert cost["usd_scope"] == "not_sent"
    assert ModeLadder(st).current() is Mode.read_only


def test_decision_defaults_to_haiku_45_and_opts_into_55(st):
    transport = FakeTransport([envelope(VALID)])
    _call(st, transport, alias="decision")
    argv = transport.calls[0]["argv"]
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    assert _events(st)[0].payload["model_pinned"] == "claude-haiku-4-5-20251001"
    assert _events(st)[0].payload["job_class"] == "decision"

    st.settings.decision_model = "claude-haiku-5-5"
    transport55 = FakeTransport([envelope(VALID)])
    _call(st, transport55, alias="decision", prompt="short decision")
    argv55 = transport55.calls[0]["argv"]
    assert argv55[argv55.index("--model") + 1] == "claude-haiku-5-5"
    covered = _events(st)
    decision_costs = [e.payload["cost"] for e in covered if e.payload["job_class"] == "decision"]
    assert any(
        cost["rate_tier"] == "haiku-5.5-short" and cost["model_pinned"] == "claude-haiku-5-5"
        for cost in decision_costs
    )


def test_classification_stays_on_cheap_haiku_when_decisions_use_55(st):
    st.settings.decision_model = "claude-haiku-5-5"
    transport = FakeTransport([envelope(VALID)])
    _call(st, transport, alias="triage")
    argv = transport.calls[0]["argv"]
    assert argv[argv.index("--model") + 1] == "haiku"


def test_codex_paper_decision_is_allowed_and_unpriced(st):
    st.settings.decision_runtime = DecisionRuntime.codex
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "item.completed", "item": {
            "id": "item-1", "type": "agent_message", "text": json.dumps(VALID),
        }},
        {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}},
    ]
    raw = "\n".join(json.dumps(event) for event in events)
    transport = FakeTransport([ProcResult(0, raw, "", False)])
    result, job = _call(st, transport, alias="decision")
    assert result.status == OK
    assert job.runtime == "codex"
    assert job.model_pinned == "gpt-5.5"
    assert _events(st)[0].payload["cost"]["rate_tier"] == "unpriced"
    assert _events(st)[0].payload["cost"]["usd"] is None


def test_codex_decision_refuses_before_spawn_in_live_mode(st):
    st.settings.decision_runtime = DecisionRuntime.codex
    ladder = ModeLadder(st)
    ladder.set_mode(Mode.paper, actor="op")
    st.settings.decision_runtime = DecisionRuntime.claude
    ladder.set_mode(Mode.review_live, actor="op")
    st.settings.decision_runtime = DecisionRuntime.codex
    transport = FakeTransport([])
    with pytest.raises(RuntimeError, match="not allowed"):
        _call(st, transport, alias="decision")
    assert transport.calls == []


def test_illegal_decision_model_fails_closed(st):
    st.settings.decision_model = "claude-opus-4-8"
    transport = FakeTransport([])
    with pytest.raises(ValueError, match="allowlisted"):
        _call(st, transport, alias="decision")
    assert transport.calls == []
