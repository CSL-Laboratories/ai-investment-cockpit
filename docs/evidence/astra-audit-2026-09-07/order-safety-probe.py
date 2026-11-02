import os
from pathlib import Path
for key in tuple(os.environ):
    if key.startswith('COCKPIT_'): os.environ.pop(key)
os.environ['TMP'] = os.environ['TEMP'] = 'C:/t'
os.environ['COCKPIT_HALT_PATH'] = 'C:/t/cockpit-audit-20260907/order-HALT-absent'
os.environ['COCKPIT_AUDIT_HEAD_FILE'] = 'C:/t/cockpit-audit-20260907/order-head-isolated.json'
import sys
sys.path.insert(0, str(Path.cwd()))
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from storage.engine import make_engine, create_all, make_session_factory
from config.settings import Settings, Mode
from orchestrator.state import OrchestratorState
from core.clock import FrozenClock
from tests.brokers.conftest import KEY, OPEN_UTC, build_scenario
from bridge.anchor import InMemoryAnchorStore, post_head_anchor
from brokers.alpaca_paper import AlpacaPaperBroker, FakeAlpaca
from brokers.paper_execution import PaperExecutionService
from orchestrator.gate import compute_gate
from orchestrator.mode_ladder import ModeLadder
from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.events import PriceScale, to_ns
from quant.verify import VerifyParams
from ingest.market_data import MarketMetrics, MarketObservation

def fresh_state():
    engine = make_engine('sqlite:///:memory:'); create_all(engine)
    return OrchestratorState.build(make_session_factory(engine), hmac_key=KEY, settings=Settings(_env_file=None), clock=FrozenClock(OPEN_UTC))

st = fresh_state()
sc=build_scenario(st, broker=AlpacaPaperBroker(client=FakeAlpaca()))
st.anchor_store=InMemoryAnchorStore(); post_head_anchor(st,record=False)
before=compute_gate(st,'auto_veto',now=OPEN_UTC+timedelta(weeks=5))
out=sc.gateway.submit(sc.order,**sc.submit_kwargs())
post_head_anchor(st,record=False)
after=compute_gate(st,'auto_veto',now=OPEN_UTC+timedelta(weeks=5))
print('GATE: gateway_submit',out.ok)
print('GATE: before', {k:before.metrics[k] for k in ('adjudicated','approvals','approval_rate')})
print('GATE: after', {k:after.metrics[k] for k in ('adjudicated','approvals','approval_rate')})
print('GATE: chain_ok',st.audit.verify_chain().ok)

st = fresh_state()
ModeLadder(st).set_mode(Mode.paper, actor='operator')
positions=[{'symbol':'AAPL', 'qty':'100', 'market_value':'1000', 'current_price':'10.00'}]
client=FakeAlpaca(cash='100000', buying_power='100000', equity='101010', positions=positions)
broker=AlpacaPaperBroker(client=client)
quote={'price':'9.90','age_s':1,'spread_pct':'0.2','bid':'9.89','ask':'9.91'}
book=BookSnapshot(symbol='AAPL',seq=1,ts=to_ns(OPEN_UTC),bids=(LevelSnapshot(price_ticks=989,qty=10000,order_count=1),),asks=(LevelSnapshot(price_ticks=991,qty=10000,order_count=1),))
obs=MarketObservation('AAPL',quote,book,'isolated-probe-quote','a'*64)
metric=MarketMetrics('AAPL',OPEN_UTC.date(),Decimal('100000000'),0.10,30)
market=SimpleNamespace(observation=lambda symbol:obs,book=lambda symbol:book,metrics=lambda symbol:metric,verify_params=VerifyParams(scale=PriceScale(tick_size=Decimal('0.01'))))
service=PaperExecutionService(st,broker=broker,gateway=None,token_registry=None,quote_fn=lambda symbol:quote,watchlist_fn=lambda:{'AAPL'},market_data=market,require_performance=True)
proposal={'symbol':'AAPL','side':'sell','asset_type':'equity','quantity':'101','notional':None,'order_type':'limit','sizing_basis':'capped','time_horizon':'short'}
resolved={'symbol':'AAPL','side':'sell','qty':'101','order_type':'limit','limit_price':'9.90','tif':'day'}
verdict,snapshot=service._fresh_risk(proposal,'AAPL',order_qty=Decimal('101'),resolved_order=resolved,require_w21=True)
print('SELL: holding',snapshot.holdings if snapshot else None)
print('SELL: proposed_qty',resolved['qty'])
print('SELL: decision',verdict['decision'],'failed',verdict['failed_checks'],'warnings',verdict.get('warnings'))
print('SELL: checks',[c for c in verdict.get('checks',[]) if c['name'] in ('sell_backed','execution_quality','net_edge_available')])
print('SELL: chain_ok',st.audit.verify_chain().ok)


