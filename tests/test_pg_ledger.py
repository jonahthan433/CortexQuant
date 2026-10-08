import os, unittest, uuid
from datetime import datetime, timezone
from src.ledger import PortfolioLedger

@unittest.skipUnless(os.getenv('DATABASE_URL'), 'requires Postgres integration DSN')
class PostgresLedgerTests(unittest.TestCase):
    def test_telegram_dedup_and_order_persist_in_postgres(self):
        ledger=PortfolioLedger(); ledger.initialize(); ch=-987654321; mid=int(uuid.uuid4().int%1000000000)
        self.assertTrue(ledger.enqueue(ch,mid,'LONG BTCUSDT',datetime.now(timezone.utc)))
        self.assertFalse(ledger.enqueue(ch,mid,'LONG BTCUSDT duplicate',datetime.now(timezone.utc)))
        self.assertTrue(any(x['channel_id']==ch and x['message_id']==mid for x in ledger.pending()))
        ledger.mark_processed(ch,mid)
        self.assertFalse(any(x['channel_id']==ch and x['message_id']==mid for x in ledger.pending()))
        with ledger.connect() as c: c.execute('DELETE FROM telegram_inbox WHERE channel_id=%s AND message_id=%s',(ch,mid))
    def test_kill_switch_is_persistent_state(self):
        ledger=PortfolioLedger(); ledger.initialize(); before=ledger.get_state('kill_switch',{'enabled':False})
        ledger.set_kill_switch(True,'test')
        self.assertTrue(PortfolioLedger().kill_switch())
        ledger.set_state('kill_switch',before)
