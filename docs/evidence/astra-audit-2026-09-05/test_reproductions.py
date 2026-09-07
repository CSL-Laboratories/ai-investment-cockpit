"""Offline audit evidence for b4a199f. PASS means the named defect was reproduced.

Run from app/ with the repository venv:
python -m pytest -q -s -p tests.conftest -p tests.brokers.conftest ../docs/evidence/astra-audit-2026-09-05/test_reproductions.py

Uses temporary SQLite databases, an in-memory keyring, FakeAlpaca, synthetic market
observations, and a fixed test edge. No network, runtime service, or real credentials.
The edge fixture isolates order/risk behavior; it is not evidence of strategy readiness.
"""
from datetime import timedelta
from decimal import Decimal

import pytest

from agent_runner.runner import build_argv
from agent_runner.spec import AgentSpec
from agent_runner.tool_inspector import inspect_argv
from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.paper_performance import reconstruct_paper_performance
from filings.raw_store import RawStore
from ingest.market_data import MarketMetrics, MarketObservation
from orchestrator.limits import DEFAULT_LIMITS
from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.events import PriceScale, to_ns
from quant.verify import EdgeEstimate, VerifyParams
from schemas.models.proposal_record import ProposalRecord
from strategies.merger_semantics import (
    CashMergerSemanticVerifier,
    SemanticEvidenceUnavailable,
    _derive_eligibility,
    _derive_outcome,
    _require_strategy_eligible,
    _text,
)
from tests.brokers.test_paper_execution import OPERATOR_ID, _service


class SyntheticMarket:
    def __init__(self, state):
        self.state = state
        self.verify_params = VerifyParams(scale=PriceScale(tick_size=Decimal('0.01')))

    def observation(self, symbol):
        book = self.book(symbol)
        return MarketObservation(symbol, {
            'price': '150', 'bid': '149.99', 'ask': '150.01',
            'age_s': 0, 'spread_pct': '0.014',
        }, book, 'synthetic-test-observation', 'a' * 64)

    def book(self, symbol):
        return BookSnapshot(
            symbol=symbol, seq=1, ts=to_ns(self.state.clock.now()),
            bids=(LevelSnapshot(14999, 1000, 1),),
            asks=(LevelSnapshot(15001, 1000, 1),),
        )

    def metrics(self, symbol):
        return MarketMetrics(symbol, self.state.clock.now().date(),
                             Decimal('100000000'), 0.2, 60)


class SyntheticEdge:
    def __init__(self, state):
        self.state = state

    def estimate(self, proposal, **kwargs):
        return EdgeEstimate(Decimal('200'), proposal['time_horizon'],
                            'audit-test-only', 'audit-test-only',
                            to_ns(self.state.clock.now()), True)


def composed_service(st):
    service, scenario, client = _service(
        st, market_data=SyntheticMarket(st), edge_provider=SyntheticEdge(st),
        execution_verdict={'fixture': 'composition-required'},
    )
    # Existing helper omits the horizon. Add it as fixture setup before the control.
    with st.session_factory() as session, session.begin():
        row = session.get(ProposalRecord, 'p1')
        row.payload = {**row.payload, 'time_horizon': 'long'}
    return service, scenario, client


def test_reproduces_mutable_proposal_bypassing_fresh_notional_cap(st):
    service, scenario, client = composed_service(st)
    st.audit.append('limits_change', payload={
        'limits': {**DEFAULT_LIMITS, 'max_notional': '100'},
    })
    original = service._load_bundle(scenario.order.id).proposal.payload
    refused, _ = service._fresh_risk(original, 'AAPL', order_qty=1, require_w21=True)
    assert refused['decision'] == 'rejected' and 'max_notional' in refused['failed_checks']
    # A keyless domain-row edit; no signed audit entry or fixed order is changed.
    with st.session_factory() as session, session.begin():
        row = session.get(ProposalRecord, 'p1')
        row.payload = {**row.payload, 'quantity': '0.1', 'notional': '15'}
    assert st.audit.verify_chain().ok
    result = service.decide(scenario.order.id, actor=OPERATOR_ID, decision='approve',
                            confirm_symbol='AAPL', request_id='audit-cap')
    assert result['status'] == 'submitted'
    submitted = client.submitted[0]
    assert Decimal(str(submitted['qty'])) * Decimal(str(submitted['limit_price'])) == 150
    assert st.audit.verify_chain().ok
    print('A1: $150 fixed order submitted under a $100 cap after unsigned proposal became $15.')


def test_reproduces_fresh_account_loss_ignored_until_scheduled_sample(st):
    service, scenario, client = composed_service(st)
    st.paper_performance_required = True
    assert service.sample_performance()['ok']
    assert st.paper_performance.equity_drawdown_today == 0
    st.clock._at += timedelta(seconds=1)
    client.equity = '99000'
    result = service.decide(scenario.order.id, actor=OPERATOR_ID, decision='approve',
                            confirm_symbol='AAPL', request_id='audit-loss')
    actual = reconstruct_paper_performance(st)
    assert actual.ok and actual.equity_drawdown_today == 1000
    assert st.paper_performance.equity_drawdown_today == 0
    assert result['status'] == 'submitted' and len(client.submitted) == 1
    print('A2: order submitted after fresh signed $1,000 equity loss; daily limit is $500.')


ELIGIBLE = (
    'Nasdaq. The company entered into a definitive merger agreement. '
    'Each share of target common stock will receive $170 in cash per share.'
)


def test_reproduces_script_text_becoming_semantic_authority():
    with pytest.raises(SemanticEvidenceUnavailable):
        _derive_eligibility(_text(b'<html><body>No deal terms disclosed.</body></html>'),
                            amended=False)
    raw = ('<html><head><script type="application/json">{"example": "' + ELIGIBLE
           + '"}</script></head><body>No deal terms disclosed.</body></html>').encode()
    label = _derive_eligibility(_text(raw), amended=False)
    _require_strategy_eligible(label)
    assert label['cash_offer'] == '170' and label['lifecycle_status'] == 'active'
    print('A3: JSON inside a script becomes an eligible cash-merger assertion.')


def test_reproduces_negated_outcome_becoming_completed_label():
    sentence = 'It is not true that the merger was completed.'
    assert _derive_outcome(sentence.lower()) == 'closed'
    print('A4: "It is not true that the merger was completed" is labelled closed.')


def test_reproduces_live_transition_poisoning_paper_performance(st):
    AlpacaPaperBroker(client=FakeAlpaca()).get_positions(st=st, clock=st.clock)
    before = reconstruct_paper_performance(st)
    assert before.ok
    intent = st.audit.intent('order_submit', refs={'order_id': 'live-only'}, payload={
        'execution_scope': 'live', 'mode': 'review_live',
        'symbol': 'AAPL', 'side': 'buy', 'qty': '1',
    })
    st.audit.result(intent, 'order_submit', payload={
        'status': 'filled', 'filled_qty': '1', 'avg_fill_price': '150',
    })
    st.audit.append('order_transition', refs={'row_id': 'live-only'}, payload={
        'to': 'filled', 'symbol': 'AAPL', 'side': 'buy',
        'filled_qty': '1', 'avg_fill_price': '150',
    })
    after = reconstruct_paper_performance(st)
    assert not after.ok and after.reason == 'fill exceeds signed order size'
    print('A5: an unrelated live fill makes paper performance fail as an oversized fill.')


def test_reproduces_ordinary_later_filing_requiring_merger_amendment(st, tmp_path):
    root = tmp_path / 'semantic-sources'
    raw = RawStore(root / 'sec', min_free_bytes=0)
    text = (ELIGIBLE + ' Expected closing date is July 20, 2026. '
            'Outside date is July 30, 2026. Subject to regulatory approval.')
    raw.write('announcement', text.encode())
    raw.write('quarterly', b'Quarterly operating results. Revenue increased this quarter.')
    verifier = CashMergerSemanticVerifier(st, raw_root=root, witness=None)
    document = {
        **_derive_eligibility(_text(text.encode()), amended=False),
        'source_accessions': ['announcement'],
        'announcement_accession': 'announcement', 'current_accession': 'announcement',
        'expected_resolution_date': '2026-07-20', 'outside_date': '2026-07-30',
        'conditions': ['regulatory approval'],
    }
    verifier._verify_deal(document)
    with pytest.raises(SemanticEvidenceUnavailable, match='current deal amendment is not explicit'):
        verifier._verify_deal({**document,
                               'source_accessions': ['announcement', 'quarterly'],
                               'current_accession': 'quarterly'})
    print('A6: unchanged merger terms pass alone; adding a routine quarterly filing refuses them.')


def test_reproduces_claude_inspector_accepting_no_tool_restriction():
    argv = build_argv(AgentSpec(job_id='audit', runtime='claude', model_alias='deep',
                               prompt='Audit fixture; never sent to a model.'))
    inspect_argv(argv, decision_stage=True)
    assert argv[argv.index('--allowedTools') + 1] == 'none'
    assert '--tools' not in argv and '--disallowedTools' not in argv
    assert '--strict-mcp-config' not in argv
    # Documentation/help verification supplies the external flag-semantics evidence.
    print('A7: inspector approves Claude argv with no built-in-tool or MCP disable flags.')
