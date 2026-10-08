import asyncio, unittest
from datetime import datetime, timezone
from src.models import TradeIntent
from src.decision import DecisionEngine
from src.execution.paper_broker import PaperBroker

class FakeLedger:
    def __init__(self): self.ps={}; self.orders=[]; self.fills=[]; self.events=[]; self.state={}; self.killed=False
    def positions(self): return list(self.ps.values())
    def kill_switch(self): return self.killed
    def day_pnl(self): return (0,10000)
    def event(self,*x): self.events.append(x)
    def get_state(self,*a): return 10000
    def record_order(self,*args,**kw): self.orders.append(args)
    def record_fill(self,*args,**kw): self.fills.append(args)
    def upsert_position(self,symbol,side,qty,entry,stop=None,target=None): self.ps[symbol]={'symbol':symbol,'side':side,'quantity':qty,'entry_price':entry,'stop_price':stop,'target_price':target}
    def remove_position(self,symbol): self.ps.pop(symbol,None)

class FlowTests(unittest.TestCase):
    def test_signal_to_paper_fill_and_ledger_protections(self):
        ledger=FakeLedger(); broker=PaperBroker(ledger)
        intent=TradeIntent(symbol='BTC',side='long',source_id='-100:42',timestamp=datetime.now(timezone.utc),raw_text='LONG BTC')
        class Market:
            def fetch(self,*a,**kw):
                import pandas as pd
                return pd.DataFrame([{'timestamp':datetime.now(timezone.utc),'open':100,'high':101,'low':99,'close':100,'volume':1}]*3)
        class Kronos:
            status='degraded'; version='NeoQuasar/Kronos-small'
        engine=DecisionEngine(ledger,Market(),Kronos(),broker)
        asyncio.run(engine.process([intent]))
        self.assertIn('BTCUSDT',ledger.ps); self.assertEqual(len(ledger.orders),3); self.assertEqual(len(ledger.fills),1)
        self.assertEqual(ledger.orders[1][2],'sell'); self.assertTrue(ledger.orders[1][6]=='open')
    def test_short_has_buy_stop_and_target(self):
        ledger=FakeLedger(); broker=PaperBroker(ledger)
        intent=TradeIntent(symbol='ETH',side='short',source_id='c:9',raw_text='SHORT ETH')
        result=broker.submit_entry(intent,1,100,101,98)
        self.assertEqual(result['status'],'filled')
        self.assertEqual(ledger.orders[1][2],'buy'); self.assertEqual(ledger.orders[2][2],'buy')
    def test_paper_fill_idempotent_when_position_exists(self):
        ledger=FakeLedger(); broker=PaperBroker(ledger); i=TradeIntent(symbol='BTC',side='long',source_id='x',raw_text='x')
        broker.submit_entry(i,1,100,99,102); second=broker.submit_entry(i,1,100,99,102)
        self.assertEqual(second['status'],'duplicate_position'); self.assertEqual(len(ledger.fills),1)
