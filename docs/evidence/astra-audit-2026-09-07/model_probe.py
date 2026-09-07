"""Offline PR80 audit probes. Run from repository app with project .venv Python.
No model/vendor requests or keyring calls; only disposable SQLite databases.
"""
import os
import tempfile
import json
from pathlib import Path
for k in list(os.environ):
    if k.startswith('COCKPIT_'):
        del os.environ[k]
from sqlalchemy import select, delete
from agent_runner.transport import ProcResult
from config.settings import Settings
from core.clock import FrozenClock
from orchestrator.state import OrchestratorState, EVENT_WATCH_ONLY
from storage.engine import create_all, make_engine, make_session_factory
from schemas.models.audit_entry import AuditEntry
from schemas.models.proposal_record import ProposalRecord
from tests.workflows.conftest import FakeTransport, envelope, NOW, KEY
from tests.workflows.test_proposals import _proposal_body
import workflows.agent_calls as calls
calls._provisioned_secret_values = lambda: []
from workflows.budget import DeepBudget
from workflows.constants import EVENT_DEEP_ESCALATION, EVENT_SCRUB
from workflows.proposals import draft_proposal
from workflows.packets import build_packet
from schemas.models.market_event import MarketEvent

results = []
with tempfile.TemporaryDirectory(prefix='a80-', dir='C:/t') as td:
    os.environ['COCKPIT_HALT_PATH'] = str(Path(td)/'HALT')
    os.environ['COCKPIT_AUDIT_HEAD_FILE'] = str(Path(td)/'head.json')
    for label, response in [
        ('empty', ProcResult(0, '', '', False)),
        ('vendor_error', ProcResult(0, json.dumps({'is_error': True,
         'subtype': 'error_max_turns', 'result': 'failed'}), '', False)),
    ]:
        engine = make_engine('sqlite:///' + (Path(td)/(label+'.db')).as_posix())
        create_all(engine)
        sf = make_session_factory(engine)
        st = OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=FrozenClock(NOW))
        result, job = calls.call_agent(st, alias='triage', prompt_ref='probe',
            prompt_hash='f'*64, prompt='answer q', schema_name='InterrogationAnswer',
            input_refs={}, transport=FakeTransport([envelope({'bad': True}), response]))
        before = st.usage.window('claude')
        with sf() as s:
            rows = s.execute(select(AuditEntry).where(AuditEntry.event_type == EVENT_WATCH_ONLY)).scalars().all()
        restarted = OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=FrozenClock(NOW))
        after = restarted.usage.window('claude')
        results.append({'probe': label, 'result': result.status, 'error': result.error,
            'job': job.status, 'watch_rows': len(rows),
            'before': {'state': before.state, 'generation': before.generation},
            'restart': {'state': after.state, 'generation': after.generation},
            'chain_ok': st.audit.verify_chain().ok})
        engine.dispose()

    engine = make_engine('sqlite:///' + (Path(td)/'budget.db').as_posix())
    create_all(engine)
    sf = make_session_factory(engine)
    st = OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=FrozenClock(NOW))
    budget = DeepBudget(st, limit=1)
    assert budget.try_consume(day=NOW.date(), event_id='e1', symbol='AAPL', reason='quote_move:4.0', corroborated=True)
    st.audit.append('benign_survivor')
    real = st.audit.verify_chain
    def verified_then_delete():
        verified = real()
        with sf() as s, s.begin():
            s.execute(delete(AuditEntry).where(AuditEntry.event_type == EVENT_DEEP_ESCALATION))
        st.audit.verify_chain = real
        return verified
    st.audit.verify_chain = verified_then_delete
    consumed = budget.try_consume(day=NOW.date(), event_id='e2', symbol='AAPL', reason='quote_move:4.0', corroborated=True)
    results.append({'probe': 'budget_verify_read_race', 'cap': 1,
        'first_spend': True, 'second_spend': consumed, 'after_chain_ok': real().ok})
    engine.dispose()

    engine = make_engine('sqlite:///' + (Path(td)/'sizeless.db').as_posix())
    create_all(engine)
    sf = make_session_factory(engine)
    st = OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=FrozenClock(NOW))
    event = MarketEvent(id='event', source='finnhub', event_type='news', symbol='AAPL',
        headline='AAPL demand', summary='Details', raw_ref='finnhub:1', content_hash='f'*64,
        ts=NOW, ingested_at=NOW)
    proposal = _proposal_body(quantity=None, notional=None, thesis='Synthetic account 123456789 and TSLA 500 shares')
    outcome = draft_proposal(st, event=event, packet=build_packet([event]),
        transport=FakeTransport([envelope(proposal)]))
    with sf() as s:
        row = s.get(ProposalRecord, outcome.record.id)
        scrubs = s.execute(select(AuditEntry).where(AuditEntry.event_type == EVENT_SCRUB)).scalars().all()
        results.append({'probe': 'sizeless_scrub', 'status': outcome.status,
            'flagged': row.flagged, 'persisted_thesis': row.payload['thesis'], 'scrub_rows': len(scrubs)})
    engine.dispose()

print(json.dumps(results, indent=2))
