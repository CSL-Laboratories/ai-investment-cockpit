"""Offline review probes for main c231dc2; never reads operator credentials or runtime state."""
import os
import sys
from pathlib import Path
from tempfile import mkdtemp

os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['TMP']=os.environ['TEMP']='C:/t'
for key in list(os.environ):
    if key.startswith('COCKPIT_'):
        del os.environ[key]
sys.path.insert(0, 'C:/Users/drfoo/OneDrive/Documents/ChatGPT/ai-investment-cockpit/app')
from decimal import Decimal
from quant.events import BookAction, BookUpdate, PriceScale, Side, Trade
from quant.execution.autoloop import AutoLoop
from quant.execution.sim_venue import SimVenue, LatencyModel
from quant.oms import NewOrder
from quant.strategies.base import Strategy
from strategies.merger_semantics import _text, _derive_eligibility, _require_strategy_eligible

class BuyOnce(Strategy):
    def __init__(self):
        super().__init__(name='buy', symbols=('S',))
        self.calls=[]
    def on_book_update(self, ctx):
        self.calls.append(('book',ctx.ts))
        if len(self.calls)==1:
            return [NewOrder(client_id='a',symbol='S',side=Side.BUY,qty=1,price_ticks=100)]
        return []
    def on_fill(self,fill,ctx):
        self.calls.append(('fill',ctx.ts))
        return []
    def describe(self): return {'name':self.name}

def event(action,seq,ex,ing):
    return BookUpdate(symbol='S',seq=seq,exchange_ts=ex,recv_ts=ing,ingest_ts=ing,
                      action=action,side=Side.SELL,price_ticks=100,qty=1,order_ref=1)

s=BuyOnce()
l=AutoLoop(venue=SimVenue('S',LatencyModel(order_entry_ns=50,market_data_ns=0,cancel_ns=0)),strategies=[s],scale=PriceScale(Decimal('.01')))
r=l.run([event(BookAction.ADD,1,1,100),event(BookAction.CANCEL,2,120,200)])
print('chronology:',{'position':r.final_position,'fills':[(f.qty,f.exchange_ts) for f in r.fills],'desync_cancels':r.desync_cancels,'valid':r.accounting_valid,'calls':s.calls})

s=BuyOnce()
l=AutoLoop(venue=SimVenue('S',LatencyModel(order_entry_ns=0,market_data_ns=100,cancel_ns=0)),strategies=[s],scale=PriceScale(Decimal('.01')))
r=l.run([event(BookAction.ADD,1,1,1),Trade(symbol='S',seq=2,exchange_ts=2,recv_ts=2,ingest_ts=2,price_ticks=101,qty=1,aggressor=Side.BUY)])
print('early fill report:',{'position':r.final_position,'fills':[(f.recv_ts,f.ingest_ts) for f in r.fills],'calls':s.calls})

cash='each share of common stock listed on Nasdaq will receive $120 in cash per share'
for separator in ['', '<!-- page marker -->', '<br>', '<span hidden>layout</span>']:
    raw=('The registrant entered into a definitive merger. It is not true that '+separator+cash+'.').encode()
    try:
        label=_derive_eligibility(_text(raw),amended=False)
        _require_strategy_eligible(label)
        print('denied cash separator',repr(separator),'ACCEPTED',label['cash_offer'])
    except Exception as e: print('denied cash separator',repr(separator),'REFUSED',type(e).__name__)

import keyring
from tests.conftest import InMemoryKeyring
keyring.set_keyring(InMemoryKeyring())
from tests.strategies import test_merger_semantics as fixtures
case = Path(mkdtemp(prefix='quant-semantic-',dir='C:/t/cockpit-audit-20260907'))
os.environ['COCKPIT_AUDIT_HEAD_FILE'] = str(case/'audit-head.json')
original=fixtures._deal_text()
original_cash=('Each'+cash[len('each'):]+'.').encode()
assert original_cash in original
raw=original.replace(original_cash, ('It is not true that <!-- page marker --> '+cash+'.').encode())
fixtures._deal_text=lambda:raw
st,root,client,market_data,provider,*_=fixtures._semantic_system(case)
estimate=provider.estimate(fixtures._proposal(),quote=market_data.quote(fixtures.SYMBOL))
print('signed fixture denied cash:',{'verified':estimate is not None and estimate.verified,'gross_return_bps':str(estimate.expected_gross_return_bps) if estimate else None,'audit_valid':st.audit.verify_chain().ok,'isolated_case':str(case)})
