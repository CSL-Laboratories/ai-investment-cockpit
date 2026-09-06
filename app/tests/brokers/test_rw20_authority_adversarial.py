"""Independent RW-20 authority attacks against an isolated, deliberately synthetic setup.

The existing broker fixture uses AAPL only to isolate risk/approval wiring; it creates no
merger evidence, watchlist configuration, real proposal, real account, or production edge.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from brokers.paper_execution import PaperExecutionError
from orchestrator.breakers import BRK_CONSECUTIVE_LOSSES, BreakerEngine
from orchestrator.limits import EVENT_LIMITS_CHANGE
from schemas.models.proposal_record import ProposalRecord
from tests.brokers.test_paper_execution import OPERATOR_ID, _service
from tests.brokers.test_paper_performance import _filled


def _approve(service, scenario, request_id):
    return service.decide(
        scenario.order.id, actor=OPERATOR_ID, decision="approve", confirm_symbol="AAPL",
        request_id=request_id,
    )


@pytest.mark.parametrize("mutation", [
    {"quantity": "0.01", "notional": "1.50"},
    {"quantity": "0.01", "notional": None},
    {"quantity": "1", "notional": "1.50", "side": "sell"},
])
def test_unsigned_proposal_size_cannot_launder_signed_order_over_cap(st, mutation):
    service, scenario, client = _service(st)
    st.audit.append(
        EVENT_LIMITS_CHANGE, actor="isolated-test-operator",
        payload={"limits": {"max_notional": "100"}},
    )
    with st.session_factory() as session, session.begin():
        proposal = session.get(ProposalRecord, "p1")
        proposal.payload = {**proposal.payload, **mutation}
    # Domain-row substitution leaves the actual signed $150 Order and audit chain intact.
    assert Decimal(str(scenario.order.qty)) * Decimal(str(scenario.order.limit_price)) == Decimal("150")
    assert st.audit.verify_chain().ok

    with pytest.raises(PaperExecutionError) as refused:
        _approve(service, scenario, "rw20-mutated-proposal-size")
    assert refused.value.code in {"fresh_risk_failed", "order_authority_unavailable"}
    assert client.submitted == []
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("quote,cap", [("100", "125"), ("300", "200")])
def test_signed_order_risk_uses_larger_of_limit_and_fresh_quote(st, quote, cap):
    service, scenario, client = _service(st)
    service._quote_fn = lambda _symbol: {"price": quote, "age_s": 0, "spread_pct": "0.1"}
    st.audit.append(
        EVENT_LIMITS_CHANGE, actor="isolated-test-operator",
        payload={"limits": {"max_notional": cap}},
    )
    with pytest.raises(PaperExecutionError, match="fresh deterministic risk"):
        _approve(service, scenario, "rw20-canonical-limit-or-quote")
    assert client.submitted == []

def test_three_new_losing_round_trips_after_review_refuse_final_submit(st, monkeypatch):
    service, scenario, client = _service(st)
    st.paper_performance_required = True
    assert service.sample_performance()["ok"]
    original_review = scenario.gateway.review_equity_order
    reached_final_boundary = False

    def losses_after_review(*args, **kwargs):
        nonlocal reached_final_boundary
        reviewed = original_review(*args, **kwargs)
        assert reviewed.ok, "the pre-loss control must reach gateway review"
        reached_final_boundary = True
        for index in range(3):
            _filled(st, f"rw20-buy-{index}", symbol="TEST", side="buy", qty="1", price="100")
            _filled(st, f"rw20-sell-{index}", symbol="TEST", side="sell", qty="1", price="99")
        # This is isolated ledger input, not a real or simulated broker order call.
        assert st.paper_performance.consecutive_losses == 0
        return reviewed

    monkeypatch.setattr(scenario.gateway, "review_equity_order", losses_after_review)
    with pytest.raises(PaperExecutionError) as refused:
        _approve(service, scenario, "rw20-consecutive-loss-final-gateway")
    assert reached_final_boundary
    assert refused.value.code.startswith("gateway_")
    assert client.submitted == []
    assert BRK_CONSECUTIVE_LOSSES in BreakerEngine(st).active_trips()
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("arrival", ["before_approval", "after_approval"])
def test_fresh_equity_loss_cannot_hide_behind_a_green_performance_cache(
    st, monkeypatch, arrival,
):
    service, scenario, client = _service(st)
    st.paper_performance_required = True
    assert service.sample_performance()["ok"]
    assert st.paper_performance.equity_drawdown_today == Decimal("0")
    if arrival == "before_approval":
        client.equity = "99000"
    else:
        from brokers import paper_execution

        original_decision = paper_execution.record_decision

        def loss_after_adjudication(*args, **kwargs):
            adjudication = original_decision(*args, **kwargs)
            client.equity = "99000"
            return adjudication

        monkeypatch.setattr(paper_execution, "record_decision", loss_after_adjudication)

    # A fresh account read will sign the loss, but the scheduled cache is still green.
    with pytest.raises(PaperExecutionError) as refused:
        _approve(service, scenario, f"rw20-equity-loss-{arrival}")
    assert refused.value.code == "fresh_risk_failed" or refused.value.code.startswith("gateway_")
    assert client.submitted == []
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("missing", [
    "proposal_payload_hash", "order_tuple_hash", "flagged", "side",
])
def test_legacy_signed_proposal_missing_binding_is_not_backfilled(st, monkeypatch, missing):
    original_record = st.audit.record_state_change

    def legacy_fixture(row, event_type, actor=None, payload=None):
        if event_type == "trade_proposal":
            payload = {key: value for key, value in payload.items() if key != missing}
        return original_record(row, event_type, actor=actor, payload=payload)

    # Construct a genuine legacy-shaped event in the NEW fixture ledger, not a migrated
    # or edited historical deployment ledger. The original signature remains valid.
    monkeypatch.setattr(st.audit, "record_state_change", legacy_fixture)
    service, scenario, client = _service(st)
    monkeypatch.setattr(st.audit, "record_state_change", original_record)
    assert st.audit.verify_chain().ok
    before = st.audit.head()
    with pytest.raises(PaperExecutionError) as refused:
        _approve(service, scenario, "rw20-legacy-binding-refusal")
    assert refused.value.code == "order_authority_unavailable"
    assert client.submitted == []
    assert st.audit.head() == before, "refusal must not backfill a missing authority binding"


@pytest.mark.parametrize("changed_field", ["flagged", "side"])
def test_mutable_proposal_flag_or_side_cannot_replace_signed_values(st, monkeypatch, changed_field):
    original_record = st.audit.record_state_change

    def signed_flagged_fixture(row, event_type, actor=None, payload=None):
        if event_type == "trade_proposal" and changed_field == "flagged":
            row.flagged = True
            payload = {**payload, "flagged": True}
        return original_record(row, event_type, actor=actor, payload=payload)

    monkeypatch.setattr(st.audit, "record_state_change", signed_flagged_fixture)
    service, scenario, client = _service(st)
    monkeypatch.setattr(st.audit, "record_state_change", original_record)
    with st.session_factory() as session, session.begin():
        proposal = session.get(ProposalRecord, "p1")
        setattr(proposal, changed_field, False if changed_field == "flagged" else "sell")
    assert st.audit.verify_chain().ok
    with pytest.raises(PaperExecutionError) as refused:
        _approve(service, scenario, "rw20-mutated-proposal-field")
    assert refused.value.code == "order_authority_unavailable"
    assert client.submitted == []
