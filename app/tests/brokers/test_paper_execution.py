"""Real-paper composition seam: only stored, approved, human-confirmed orders may submit."""
from __future__ import annotations

from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.paper_execution import PaperExecutionError, PaperExecutionService
from config.settings import DecisionRuntime, Mode
from core.ids import new_id
from schemas.models.agent_job import AgentJob
from schemas.models.proposal_record import ProposalRecord
from tests.brokers.conftest import build_scenario

OPERATOR_ID = "123456789012345678"


def _service(st, *, runtime="claude", selected_runtime="claude"):
    from config.settings import Settings

    # Production freezes the operator allowlist during Settings construction. Build the same
    # shape here rather than relying on a mutable env fallback after the state already exists.
    st.settings = Settings(
        decision_runtime=DecisionRuntime(selected_runtime),
        operator_ids_config=OPERATOR_ID,
        _env_file=None,
    )
    client = FakeAlpaca(cash="100000", buying_power="200000")
    broker = AlpacaPaperBroker(client=client)
    registry = TokenRegistry()
    gateway = OrderGateway(
        broker, st=st, token_registry=registry, execution_scope="paper"
    )
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, qty="1",
        limit_price="150", notional="150", mint=False,
    )
    job = AgentJob(
        id=new_id("job"), runtime=runtime, model="deep", model_pinned="pinned",
        prompt_template="trade_proposal.v1", prompt_hash="h" * 16,
        input_refs={}, schema_id="TradeProposal", status="done", created_at=st.clock.now(),
    )
    st.audit.record_state_change(
        job, "agent_job", actor="agent_calls", payload={"status": "done", "runtime": runtime}
    )
    proposal = ProposalRecord(
        id="p1", symbol="AAPL", side="buy", status="draft", flagged=False,
        payload={
            "proposal_id": "p1", "symbol": "AAPL", "side": "buy",
            "asset_type": "equity", "quantity": "1", "notional": "150",
            "order_type": "limit", "sizing_basis": "capped",
        },
        job_id=job.id, risk_decision_id=scenario.risk_decision.id,
        order_id=scenario.order.id, created_at=st.clock.now(), updated_at=st.clock.now(),
    )
    st.audit.record_state_change(
        proposal, "proposal", actor="proposals", payload={"status": "draft", "symbol": "AAPL"}
    )
    service = PaperExecutionService(
        st, broker=broker, gateway=gateway, token_registry=registry,
        quote_fn=lambda _symbol: {"price": "150", "age_s": 0, "spread_pct": "0.1"},
        watchlist_fn=lambda: {"AAPL"},
    )
    return service, scenario, client


def test_exact_symbol_and_operator_are_required(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st)

    try:
        service.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve",
            confirm_symbol="MSFT", request_id="paper-wrong-symbol",
        )
    except PaperExecutionError as exc:
        assert exc.code == "symbol_confirmation_required"
    else:  # pragma: no cover - explicit safety assertion
        raise AssertionError("wrong-symbol confirmation must fail closed")
    assert client.submitted == []

    try:
        service.decide(
            scenario.order.id, actor="mallory", decision="approve",
            confirm_symbol="AAPL", request_id="paper-foreign-actor",
        )
    except PaperExecutionError as exc:
        assert exc.code == "unauthorized"
    else:  # pragma: no cover - explicit safety assertion
        raise AssertionError("foreign actor must fail closed")
    assert client.submitted == []


def test_claude_proposal_round_trips_through_paper_gateway(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st)

    out = service.decide(
        scenario.order.id, actor=OPERATOR_ID, decision="approve",
        confirm_symbol="AAPL", request_id="paper-approved-1",
    )

    assert out["status"] == "submitted" and out["broker_order_id"]
    assert len(client.submitted) == 1
    assert client.submitted[0]["symbol"] == "AAPL"
    assert client.submitted[0]["client_order_id"] == f"cockpit-{scenario.order.id}"
    assert st.audit.verify_chain().ok


def test_codex_proposal_round_trips_when_codex_is_configured(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, runtime="codex", selected_runtime="codex")

    out = service.decide(
        scenario.order.id, actor=OPERATOR_ID, decision="approve",
        confirm_symbol="AAPL", request_id="paper-codex-approved",
    )
    assert out["status"] == "submitted"
    assert len(client.submitted) == 1


def test_runtime_mismatch_cannot_enter_paper_order_path(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, runtime="claude", selected_runtime="codex")

    try:
        service.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve",
            confirm_symbol="AAPL", request_id="paper-runtime-mismatch",
        )
    except PaperExecutionError as exc:
        assert exc.code == "runtime_not_allowed"
    else:  # pragma: no cover - explicit safety assertion
        raise AssertionError("a proposal from the non-selected runtime must not reach paper broker")
    assert client.submitted == []
