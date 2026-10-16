"""Real-paper composition seam: only stored, approved, human-confirmed orders may submit."""
from __future__ import annotations

from decimal import Decimal

from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.paper_execution import PaperExecutionError, PaperExecutionService
from config.settings import DecisionRuntime, Mode
from core.ids import new_id
from core.proposal_authority import proposal_payload_hash
from orchestrator.breakers import BreakerEngine
from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.events import PriceScale, to_ns
from quant.verify import VerifyParams
from sqlalchemy import select
from schemas.models.approval_record import ApprovalRecord
from schemas.models.agent_job import AgentJob
from schemas.models.order import Order
from schemas.models.proposal_record import ProposalRecord
from tests.brokers.conftest import build_scenario

OPERATOR_ID = "123456789012345678"


class _MarketData:
    def __init__(self, st, *, metric):
        self._st = st
        self._metric = metric
        self.verify_params = VerifyParams(
            scale=PriceScale(tick_size=Decimal("0.01")),
            max_staleness_ns=120_000_000_000,
        )

    def book(self, _symbol):
        return BookSnapshot(
            symbol="AAPL", seq=7, ts=to_ns(self._st.clock.now()),
            bids=(LevelSnapshot(price_ticks=14_999, qty=1, order_count=1),),
            asks=(LevelSnapshot(price_ticks=15_001, qty=1, order_count=1),),
        )

    def metrics(self, _symbol):
        return self._metric


def _service(st, *, runtime="claude", selected_runtime="claude", market_data=None,
             execution_verdict=None, edge_provider=None, generation=0, input_refs=None):
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
        broker, st=st, token_registry=registry, execution_scope="paper",
        require_analysis_generation=True,
    )
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, qty="1",
        limit_price="150", notional="150", execution_verdict=execution_verdict,
        mint=False,
    )
    job = AgentJob(
        id=new_id("job"), runtime=runtime, model="deep", model_pinned="pinned",
        prompt_template="trade_proposal.v1", prompt_hash="h" * 16,
        input_refs={"_generation": generation, **(input_refs or {})},
        schema_id="TradeProposal", status="done",
        created_at=st.clock.now(),
    )
    st.audit.record_state_change(
        job, "agent_job", actor="agent_calls",
        payload={"status": "done", "runtime": runtime, "generation": generation},
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
        proposal, "trade_proposal", actor="proposals",
        payload={
            "proposal_id": proposal.id,
            "status": "draft",
            "symbol": "AAPL",
            "job_id": job.id,
            "risk_decision_id": scenario.risk_decision.id,
            "order_id": scenario.order.id,
            "side": proposal.side, "flagged": proposal.flagged,
            "proposal_payload_hash": proposal_payload_hash(proposal.payload),
            "order_tuple_hash": scenario.order.order_tuple_hash,
        },
    )
    service = PaperExecutionService(
        st, broker=broker, gateway=gateway, token_registry=registry,
        quote_fn=lambda _symbol: {"price": "150", "age_s": 0, "spread_pct": "0.1"},
        watchlist_fn=lambda: {"AAPL"}, market_data=market_data,
        edge_provider=edge_provider,
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


def test_jev_proposal_round_trips_when_jev_is_configured(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, runtime="jev", selected_runtime="jev")

    out = service.decide(
        scenario.order.id, actor=OPERATOR_ID, decision="approve",
        confirm_symbol="AAPL", request_id="paper-jev-approved",
    )
    assert out["status"] == "submitted"
    assert len(client.submitted) == 1


def test_auto_accepts_the_runtime_that_produced_the_proposal(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, runtime="jev", selected_runtime="auto")

    out = service.decide(
        scenario.order.id, actor=OPERATOR_ID, decision="approve",
        confirm_symbol="AAPL", request_id="paper-auto-jev",
    )
    assert out["status"] == "submitted"
    assert len(client.submitted) == 1


def test_jev_selection_refuses_an_older_claude_proposal(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(st, runtime="claude", selected_runtime="jev")

    try:
        service.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve",
            confirm_symbol="AAPL", request_id="paper-jev-stale-claude",
        )
    except PaperExecutionError as exc:
        assert exc.code == "runtime_not_allowed"
    else:  # pragma: no cover - explicit safety assertion
        raise AssertionError("an older Claude proposal must not run under explicit jev")
    assert client.submitted == []


def test_jev_fallback_claude_proposal_can_enter_paper(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(
        st, runtime="claude", selected_runtime="jev",
        input_refs={"decision_route": "jev_fallback"},
    )

    out = service.decide(
        scenario.order.id, actor=OPERATOR_ID, decision="approve",
        confirm_symbol="AAPL", request_id="paper-jev-fallback",
    )
    assert out["status"] == "submitted"
    assert len(client.submitted) == 1


def test_approval_refuses_stale_agent_generation(st):
    service, scenario, client = _service(st)
    breaker = BreakerEngine(st)
    breaker.enter_watch_only("claude", "test exhaustion", actor="test")
    breaker.recover_from_watch_only("claude", Mode.paper, actor="test")
    assert breaker.current_generation("claude") == 2

    try:
        service.decide(
            scenario.order.id,
            actor=OPERATOR_ID,
            decision="approve",
            confirm_symbol="AAPL",
            request_id="paper-stale-generation",
        )
    except PaperExecutionError as exc:
        assert exc.code == "stale_analysis"
    else:  # pragma: no cover - explicit order-authority assertion
        raise AssertionError("pre-blackout analysis must not regain paper authority")
    assert client.submitted == []


def test_approval_refuses_missing_generation(st):
    missing_service, missing_scenario, missing_client = _service(st, generation=None)
    try:
        missing_service.decide(
            missing_scenario.order.id,
            actor=OPERATOR_ID,
            decision="approve",
            confirm_symbol="AAPL",
            request_id="paper-missing-generation",
        )
    except PaperExecutionError as exc:
        assert exc.code == "analysis_authority_unavailable"
    else:  # pragma: no cover
        raise AssertionError("a missing signed generation must fail closed")
    assert missing_client.submitted == []


def test_approval_refuses_future_generation(st):
    future_service, future_scenario, future_client = _service(st, generation=99)
    try:
        future_service.decide(
            future_scenario.order.id,
            actor=OPERATOR_ID,
            decision="approve",
            confirm_symbol="AAPL",
            request_id="paper-future-generation",
        )
    except PaperExecutionError as exc:
        assert exc.code == "stale_analysis"
    else:  # pragma: no cover
        raise AssertionError("a future generation must not skip signed authority")
    assert future_client.submitted == []


def test_mutable_proposal_job_swap_cannot_replace_signed_generation(st):
    service, scenario, client = _service(st)
    replacement = AgentJob(
        id=new_id("job"), runtime="claude", model="deep", model_pinned="pinned",
        prompt_template="trade_proposal.v1", prompt_hash="r" * 16,
        input_refs={"_generation": 0}, schema_id="TradeProposal", status="done",
        created_at=st.clock.now(),
    )
    st.audit.record_state_change(
        replacement,
        "agent_job",
        actor="agent_calls",
        payload={"status": "done", "runtime": "claude", "generation": 0},
    )
    with st.session_factory() as session, session.begin():
        proposal = session.get(ProposalRecord, "p1")
        proposal.job_id = replacement.id
    assert st.audit.verify_chain().ok

    try:
        service.decide(
            scenario.order.id,
            actor=OPERATOR_ID,
            decision="approve",
            confirm_symbol="AAPL",
            request_id="paper-swapped-job",
        )
    except PaperExecutionError as exc:
        assert exc.code == "analysis_authority_unavailable"
    else:  # pragma: no cover
        raise AssertionError("a mutable job-id swap must not replace signed lineage")
    assert client.submitted == []


def test_gateway_rechecks_generation_after_approval_mint(st, monkeypatch):
    import brokers.paper_execution as paper_module

    service, scenario, client = _service(st)
    original = paper_module.record_decision

    def change_generation(*args, **kwargs):
        adjudication = original(*args, **kwargs)
        if kwargs.get("user_decision") == "approve":
            # Simulate a vendor-state transition in the narrow interval after the service's
            # signed-lineage check but before OrderGateway.review_equity_order.
            st.usage.window("claude").generation += 1
        return adjudication

    monkeypatch.setattr(paper_module, "record_decision", change_generation)
    try:
        service.decide(
            scenario.order.id,
            actor=OPERATOR_ID,
            decision="approve",
            confirm_symbol="AAPL",
            request_id="paper-generation-race",
        )
    except PaperExecutionError as exc:
        assert exc.code == "gateway_analysis_freshness"
    else:  # pragma: no cover
        raise AssertionError("gateway must recheck generation after approval mint")
    assert client.submitted == []


def test_composed_decide_fails_closed_when_rw14_metrics_are_unavailable(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(
        st, market_data=_MarketData(st, metric=None),
    )

    try:
        service.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve",
            confirm_symbol="AAPL", request_id="paper-w21-missing-metrics",
        )
    except PaperExecutionError as exc:
        assert exc.code == "fresh_risk_failed"
    else:  # pragma: no cover - explicit order-path safety assertion
        raise AssertionError("missing W-21 metrics must fail at the decide surface")
    assert client.submitted == []


def test_persisted_w21_evidence_keeps_fresh_risk_armed_if_service_loses_market_data(
    st, monkeypatch,
):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, client = _service(
        st,
        execution_verdict={
            "verdict": "ok", "net_edge_state": "sufficient", "qty": 1,
        },
    )

    try:
        service.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="approve",
            confirm_symbol="AAPL", request_id="paper-w21-detached-market-data",
        )
    except PaperExecutionError as exc:
        assert exc.code == "fresh_risk_failed"
    else:  # pragma: no cover - explicit order-path safety assertion
        raise AssertionError("a composed order must not fall back to legacy additive risk")
    assert client.submitted == []


def test_discord_veto_terminally_rejects_the_order(st):
    service, scenario, client = _service(st)

    out = service.decide(
        scenario.order.id,
        actor=OPERATOR_ID,
        decision="veto",
        confirm_symbol=None,
        request_id="discord-veto-1",
        surface="discord",
    )

    assert out["status"] == "vetoed"
    with st.session_factory() as session:
        order = session.get(Order, scenario.order.id)
        approval = session.execute(
            select(ApprovalRecord).where(ApprovalRecord.id == out["approval_id"])
        ).scalar_one()
        assert order.state == "rejected"
        assert approval.surface == "discord" and approval.user_decision == "veto"
    assert client.submitted == []
    assert st.audit.verify_chain().ok


def test_reused_interaction_id_cannot_terminalize_a_different_order(st):
    service, scenario, client = _service(st)
    conflicting = ApprovalRecord(
        id=new_id("appr"), proposal_id="different-proposal",
        risk_decision_id=scenario.risk_decision.id, actor_user_id=OPERATOR_ID,
        user_decision="veto", surface="discord", interaction_id="reused-interaction",
        message_id=None, device="discord", order_tuple_hash=scenario.tuple_hash,
        single_use_consumed=False, notes=None, decided_at=st.clock.now(),
        created_at=st.clock.now(),
    )
    st.audit.record_state_change(
        conflicting, "proposal_decision", actor=OPERATOR_ID,
        payload={
            "decision": "veto", "proposal_id": "different-proposal",
            "risk_decision_id": scenario.risk_decision.id,
            "order_tuple_hash": scenario.tuple_hash,
        },
    )

    try:
        service.decide(
            scenario.order.id, actor=OPERATOR_ID, decision="veto", confirm_symbol=None,
            request_id="reused-interaction", surface="discord",
        )
    except PaperExecutionError as exc:
        assert exc.code == "interaction_conflict"
    else:  # pragma: no cover - explicit terminalization safety assertion
        raise AssertionError("a reused interaction id must not bind another order")
    with st.session_factory() as session:
        assert session.get(Order, scenario.order.id).state == "proposed"
    assert client.submitted == []


def test_equity_drawdown_trip_blocks_the_gateway_before_broker_submit(st):
    service, scenario, client = _service(st)
    assert service.sample_performance()["ok"]
    client.equity = "99400"

    sampled = service.sample_performance()
    assert Decimal(sampled["equity_drawdown_today"]) == Decimal("600")

    try:
        service.decide(
            scenario.order.id,
            actor=OPERATOR_ID,
            decision="approve",
            confirm_symbol="AAPL",
            request_id="paper-drawdown-blocked",
        )
    except PaperExecutionError as exc:
        assert exc.code == "fresh_risk_failed"
    else:  # pragma: no cover - explicit safety assertion
        raise AssertionError("fresh risk must consume the equity drawdown")

    # Independent backstop: even a caller supplying an approved risk recheck cannot bypass
    # the active signed breaker at OrderGateway.
    scenario.gateway.mint_submit_token(
        nonce=scenario.nonce,
        proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id,
        order_tuple_hash=scenario.tuple_hash,
    )
    blocked = scenario.gateway.submit(
        scenario.order,
        **scenario.submit_kwargs(analysis_vendor="claude", analysis_generation=0),
    )
    assert not blocked.ok and blocked.stage == "breaker"
    assert "daily_equity_drawdown" in blocked.reason
    assert client.submitted == []


def test_approval_prices_the_persisted_order_quantity_not_a_new_notional_size(
    st, monkeypatch,
):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    service, scenario, _client = _service(st)
    seen = []

    def recording_builder(*args, quantity_override=None, **kwargs):
        seen.append(quantity_override)
        return None, None, int(Decimal(str(quantity_override))), ()

    monkeypatch.setattr("brokers.paper_execution.build_execution_verdict", recording_builder)
    out = service.decide(
        scenario.order.id, actor=OPERATOR_ID, decision="approve",
        confirm_symbol="AAPL", request_id="paper-w21-fixed-qty",
    )

    assert out["status"] == "submitted"
    assert len(seen) >= 2
    assert all(Decimal(str(qty)) == Decimal("1") for qty in seen)


def test_approval_reuses_the_composed_edge_provider_on_every_fresh_gate(st, monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR_ID)
    provider = object()
    service, scenario, _client = _service(st, edge_provider=provider)
    seen = []

    def recording_builder(*args, quantity_override=None, **kwargs):
        seen.append((kwargs.get("edge_provider"), kwargs.get("quote"), quantity_override))
        return None, None, int(Decimal(str(quantity_override))), ()

    monkeypatch.setattr("brokers.paper_execution.build_execution_verdict", recording_builder)
    out = service.decide(
        scenario.order.id, actor=OPERATOR_ID, decision="approve",
        confirm_symbol="AAPL", request_id="paper-w23-provider",
    )
    assert out["status"] == "submitted" and len(seen) >= 2
    assert all(item[0] is provider and item[1]["price"] == "150" for item in seen)
    assert all(Decimal(str(item[2])) == Decimal("1") for item in seen)
