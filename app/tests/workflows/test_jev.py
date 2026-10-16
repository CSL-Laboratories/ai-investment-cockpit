"""CSL-191 — Jev as a paper/read-only decision runtime, with Claude fallback.

HTTP is mocked. No live JEV_API_KEY, no broker, no OrderGateway.
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from agent_runner.result import OK
from config.models import runtime_for
from config.settings import DecisionRuntime, Mode, Settings
from orchestrator.breakers import BreakerEngine
from orchestrator.gate import GateResult
from orchestrator.mode_ladder import (
    ExecutionRuntimeError,
    IllegalTransition,
    ModeLadder,
    assert_execution_runtime,
    default_decision_stage_runtimes,
)
from schemas.models.audit_entry import AuditEntry
from tests.workflows.conftest import FakeTransport, envelope
from workflows.agent_calls import call_agent
from workflows.constants import EVENT_AGENT_JOB
from workflows.context_limit import estimate_tokens
from workflows.cost_log import quote_cost
from workflows.jev import decision_questions, evaluate_decision
from workflows.packets import Packet, PacketItem
from workflows.schema_loader import load_schema

JEV_URL = "https://api.typesafe.ai/v1/systemone"
_KEY = "test-jev-token"

CLAUDE_PROPOSAL = {
    "proposal_id": "prop_claude_fallback",
    "symbol": "AAPL",
    "side": "sell",
    "asset_type": "equity",
    "order_type": "market",
    "time_horizon": "long",
    "thesis": "claude fallback thesis",
    "invalidation": "claude fallback invalidation",
    "sizing_basis": "claude fallback sizing",
    "source_refs": ["ref-0"],
}


def _item(text="AAPL filed an 8-K", ref="ref-0"):
    return PacketItem(event_id="e0", raw_ref=ref, symbol="AAPL", text=text)


def _packet(*items):
    return Packet(items=tuple(items) if items else (_item(),))


def _answers(confidence=0.81, side="buy"):
    def choice(value, options):
        probabilities = {option: (0.9 if option == value else 0.1 / (len(options) - 1))
                         for option in options}
        return {
            "type": "choice",
            "choice": value,
            "probabilities": probabilities,
            "confidence": confidence,
        }

    return {
        "side": choice(side, ["buy", "sell"]),
        "asset_type": choice("equity", ["equity", "option"]),
        "order_type": choice("limit", ["market", "limit", "stop", "stop_limit"]),
        "time_horizon": choice("short", ["intraday", "short", "long"]),
    }


def _jev_response(confidence=0.81, side="buy", model="jev-1.14.0", usage=None):
    body = {
        "model": model,
        "answers": _answers(confidence, side),
        "usage": usage or {"input_tokens": 20_000, "output_tokens": 40},
    }
    return httpx.Response(200, json=body)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _call(st, transport, client, **kw):
    base = dict(
        alias="decision",
        prompt_ref="trade_proposal.v1",
        prompt_hash="h" * 16,
        prompt="decide for AAPL",
        schema_name="TradeProposal",
        input_refs={"event_id": "e0", "raw_refs": ["ref-0"], "context_dropped": 0},
        transport=transport,
        jev_packet=_packet(),
        jev_symbol="AAPL",
        jev_client=client,
        decision_weight=60,
    )
    base.update(kw)
    return call_agent(st, **base)


def _events(st):
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_AGENT_JOB)
        ).scalars().all()
    return [e for e in entries if st.audit.verify_entry(e)]


def test_jev_price_tier_charges_input_only():
    quote = quote_cost(
        "jev-1.14.0", input_tokens=20_000, output_tokens=1_000,
        input_token_source="vendor",
    )
    assert quote.usd == "0.00084000"
    assert quote.rate_tier == "jev"
    assert quote.usd_scope == "input_output"
    other = quote_cost(
        "jev-1.13.0", input_tokens=20_000, output_tokens=0,
        input_token_source="vendor",
    )
    assert other.usd == "0.00084000"
    assert quote_cost(
        "jev-latest", input_tokens=20_000, output_tokens=0,
        input_token_source="vendor",
    ).rate_tier == "unpriced"


def test_jev_model_alias_is_rejected(monkeypatch):
    monkeypatch.setenv("COCKPIT_JEV_MODEL", "jev-latest")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.delenv("COCKPIT_JEV_MODEL")
    monkeypatch.setenv("COCKPIT_JEV_STATE_TOKEN_CAP", "32000")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.delenv("COCKPIT_JEV_STATE_TOKEN_CAP")
    monkeypatch.setenv("COCKPIT_JEV_REQUEST_TOKEN_CAP", "64000")
    with pytest.raises(ValidationError):
        Settings()


def test_runtime_map_keeps_non_decisions_on_claude():
    assert runtime_for("decision", "jev") == "jev"
    assert runtime_for("triage", "jev") == "claude"
    assert runtime_for("summary", "auto") == "claude"
    assert runtime_for("decision", "auto") == "claude"
    assert runtime_for("decision", "codex") == "codex"


def test_auto_routes_a_light_paper_decision_to_jev(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.auto
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        body = json.loads(request.content)
        seen["body"] = body
        return _jev_response()

    transport = FakeTransport([envelope(CLAUDE_PROPOSAL)])
    result, job = _call(st, transport, _client(handler), decision_weight=70)
    assert result.status == OK
    assert transport.calls == []
    assert seen["url"] == JEV_URL
    assert seen["auth"] == f"Bearer {_KEY}"
    assert seen["body"]["model"] == "jev-1.14.0"
    assert set(seen["body"]["questions"]) == {
        "side", "asset_type", "order_type", "time_horizon",
    }
    proposal = result.validated
    assert proposal["symbol"] == "AAPL"
    assert proposal["side"] == "buy"
    assert proposal["quantity"] is None and proposal["notional"] is None
    assert proposal["source_refs"] == ["ref-0"]
    assert "filed an 8-K" not in proposal["thesis"]
    assert job.runtime == "jev"
    assert job.model_pinned == "jev-1.14.0"
    assert job.usage_estimate == Decimal("0.00084000")
    assert job.input_refs["decision_route"] == "jev"
    cost = _events(st)[0].payload["cost"]
    assert cost["rate_tier"] == "jev"
    assert cost["usd"] == "0.00084000"
    assert cost["billed"] is True
    assert _KEY not in json.dumps(_events(st)[0].payload)
    assert BreakerEngine(st).is_watch_only("claude") is False
    assert BreakerEngine(st).is_watch_only("jev") is False


def test_auto_stays_on_claude_above_the_weight_threshold(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.auto
    called = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return _jev_response()

    transport = FakeTransport([envelope(CLAUDE_PROPOSAL)])
    result, job = _call(st, transport, _client(handler), decision_weight=71)
    assert called["n"] == 0
    assert result.status == OK
    assert result.validated["proposal_id"] == "prop_claude_fallback"
    assert job.runtime == "claude"
    assert "jev_attempt" not in _events(st)[0].payload


def test_auto_without_a_weight_stays_on_claude(st, monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    st.settings.decision_runtime = DecisionRuntime.auto
    transport = FakeTransport([envelope(CLAUDE_PROPOSAL)])
    result, job = _call(st, transport, _client(lambda _r: _jev_response()), decision_weight=None)
    assert result.status == OK
    assert job.runtime == "claude"


def test_explicit_jev_ignores_weight_and_pins_the_model(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.jev
    st.settings.jev_model = "jev-1.13.0"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["model"] = json.loads(request.content)["model"]
        return _jev_response(model="jev-1.13.0")

    transport = FakeTransport([])
    result, job = _call(st, transport, _client(handler), decision_weight=99)
    assert result.status == OK
    assert seen["model"] == "jev-1.13.0"
    assert job.model_pinned == "jev-1.13.0"
    assert job.runtime == "jev"


def test_low_confidence_falls_back_to_claude_without_watch_only(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.auto
    transport = FakeTransport([envelope(CLAUDE_PROPOSAL)])
    result, job = _call(st, transport, _client(lambda _r: _jev_response(confidence=0.2)))
    assert result.status == OK
    assert result.validated["side"] == "sell"
    assert job.runtime == "claude"
    assert job.input_refs["decision_route"] == "jev_fallback"
    attempt = _events(st)[0].payload["jev_attempt"]
    assert attempt["fallback"] == "jev_low_confidence"
    assert attempt["billed"] is True
    assert attempt["usd"] == "0.00084000"
    assert attempt["rate_tier"] == "jev"
    assert job.usage_estimate > Decimal("0.00084000")
    assert BreakerEngine(st).is_watch_only("claude") is False


def test_http_error_falls_back_and_does_not_open_watch_only(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.jev

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate_limit"})

    transport = FakeTransport([envelope(CLAUDE_PROPOSAL)])
    result, job = _call(st, transport, _client(handler))
    assert result.status == OK
    assert job.runtime == "claude"
    assert _events(st)[0].payload["jev_attempt"]["fallback"] == "jev_error"
    assert _events(st)[0].payload["jev_attempt"]["billed"] is False
    assert BreakerEngine(st).is_watch_only("claude") is False
    assert ModeLadder(st).current() is Mode.read_only


def test_oversized_packet_skips_http_and_uses_claude(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.auto
    st.settings.jev_state_token_cap = 80
    called = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return _jev_response()

    transport = FakeTransport([envelope(CLAUDE_PROPOSAL)])
    packet = _packet(_item(text="x" * 5000))
    result, job = _call(
        st, transport, _client(handler), jev_packet=packet, decision_weight=10,
    )
    assert called["n"] == 0
    assert result.status == OK
    assert job.runtime == "claude"
    assert _events(st)[0].payload["jev_attempt"]["fallback"] == "jev_over_cap"


def test_missing_api_key_falls_back(st, monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    st.settings.decision_runtime = DecisionRuntime.jev
    called = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return _jev_response()

    transport = FakeTransport([envelope(CLAUDE_PROPOSAL)])
    result, job = _call(st, transport, _client(handler))
    assert called["n"] == 0
    assert result.status == OK
    assert job.runtime == "claude"
    assert _events(st)[0].payload["jev_attempt"]["fallback"] == "jev_unconfigured"


def test_invalid_choice_falls_back(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.jev

    def handler(_request: httpx.Request) -> httpx.Response:
        body = _jev_response()
        payload = json.loads(body.content)
        payload["answers"]["side"]["choice"] = "hold"
        return httpx.Response(200, json=payload)

    transport = FakeTransport([envelope(CLAUDE_PROPOSAL)])
    result, job = _call(st, transport, _client(handler))
    assert result.status == OK
    assert job.runtime == "claude"
    assert _events(st)[0].payload["jev_attempt"]["fallback"] == "jev_invalid"
    assert _events(st)[0].payload["jev_attempt"]["billed"] is True


def test_triage_does_not_call_jev(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.jev
    called = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return _jev_response()

    body = {
        "question": "q?",
        "answer": "grounded answer",
        "pattern": "fresh",
        "session_id": None,
        "generated_at": "2026-07-07T14:00:00+00:00",
    }
    transport = FakeTransport([envelope(body)])
    result, job = _call(
        st, transport, _client(handler), alias="triage", schema_name="InterrogationAnswer",
    )
    assert called["n"] == 0
    assert result.status == OK
    assert job.runtime == "claude"
    argv = transport.calls[0]["argv"]
    assert argv[argv.index("--model") + 1] == "haiku"


def test_explicit_jev_is_refused_in_review_live(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    ladder = ModeLadder(st)
    ladder.set_mode(Mode.paper, actor="op")
    ladder.set_mode(Mode.review_live, actor="op")
    st.settings.decision_runtime = DecisionRuntime.jev
    called = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return _jev_response()

    transport = FakeTransport([])
    with pytest.raises(RuntimeError, match="not allowed"):
        _call(st, transport, _client(handler))
    assert called["n"] == 0
    assert transport.calls == []


def test_auto_in_review_live_stays_on_claude(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.auto
    ladder = ModeLadder(st)
    ladder.set_mode(Mode.paper, actor="op")
    ladder.set_mode(Mode.review_live, actor="op")
    called = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return _jev_response()

    transport = FakeTransport([envelope(CLAUDE_PROPOSAL)])
    result, job = _call(st, transport, _client(handler), decision_weight=1)
    assert called["n"] == 0
    assert result.status == OK
    assert job.runtime == "claude"


def test_jev_cannot_enter_review_live_or_arm(st):
    st.settings.decision_runtime = DecisionRuntime.jev
    ladder = ModeLadder(st)
    ladder.set_mode(Mode.paper, actor="op")
    with pytest.raises(IllegalTransition, match="COCKPIT_DECISION_RUNTIME=claude"):
        ladder.set_mode(Mode.review_live, actor="op")
    assert ladder.current() is Mode.paper
    with pytest.raises(ExecutionRuntimeError):
        assert_execution_runtime(Mode.auto_veto, "jev")


def test_auto_can_enter_review_live_and_resolves_to_claude(st):
    st.settings.decision_runtime = DecisionRuntime.auto
    assert default_decision_stage_runtimes(st.settings) == ["claude", "claude", "claude"]
    ladder = ModeLadder(st)
    ladder.set_mode(Mode.paper, actor="op")
    ladder.set_mode(Mode.review_live, actor="op")
    assert ladder.current() is Mode.review_live
    # Arm from a Claude-resolved map. Switch back to jev after review_live is
    # already persisted and the arm gate must still refuse.
    st.settings.decision_runtime = DecisionRuntime.jev
    refused = ladder.arm_auto_mode(
        Mode.auto_veto, gate_result=GateResult(unlockable=True, target="auto_veto"),
        operator="op",
    )
    assert not refused.armed
    assert any("not claude" in reason for reason in refused.reasons)
    assert ladder.current() is Mode.review_live


def test_tail_trim_keeps_the_cited_prefix(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.auto
    largest = max(
        estimate_tokens(json.dumps(question))
        for question in decision_questions("AAPL").values()
    )
    head = _item(text="HEAD-EVIDENCE", ref="ref-head")
    tail = _item(text="T" * 600, ref="ref-tail")
    st.settings.jev_state_token_cap = largest + estimate_tokens(head.text) + 8
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["state"] = json.loads(request.content)["state"]
        return _jev_response()

    transport = FakeTransport([])
    result, job = _call(
        st, transport, _client(handler),
        jev_packet=_packet(head, tail), decision_weight=10,
    )
    assert result.status == OK
    assert "HEAD-EVIDENCE" in seen["state"]
    assert "T" * 40 not in seen["state"]
    assert result.validated["source_refs"] == ["ref-head"]
    assert job.input_refs["context_dropped"] == 1


def test_secret_in_state_is_not_posted(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    called = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return _jev_response()

    attempt = evaluate_decision(
        Settings(),
        _packet(_item(text="prefix SUPERSECRETVALUE suffix")),
        symbol="AAPL",
        schema=load_schema("TradeProposal"),
        client=_client(handler),
        secret_values=["SUPERSECRETVALUE"],
    )
    assert called["n"] == 0
    assert attempt.accepted is False
    assert attempt.reason == "jev_error"
    assert attempt.billed is False


def test_response_model_is_recorded_when_it_differs(st, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", _KEY)
    st.settings.decision_runtime = DecisionRuntime.jev

    def handler(_request: httpx.Request) -> httpx.Response:
        return _jev_response(model="jev-1.13.0")

    transport = FakeTransport([])
    _result, job = _call(st, transport, _client(handler))
    assert job.model_pinned == "jev-1.14.0"
    assert _events(st)[0].payload["cost"]["response_model"] == "jev-1.13.0"
    assert _events(st)[0].payload["cost"]["usd"] == "0.00084000"
