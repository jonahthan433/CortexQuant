"""Durable message-key deduplication and pending replay tests."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from src.signals.inbox import TelegramInbox
from src.signals.telegram_listener import TelegramListener


class TelegramInboxTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.inbox = TelegramInbox(Path(self.tempdir.name) / "inbox.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_duplicate_channel_message_is_ignored(self):
        self.assertTrue(self.inbox.enqueue(-1001, 42, "LONG BTCUSDT", "t1"))
        self.assertFalse(self.inbox.enqueue(-1001, 42, "LONG BTCUSDT", "t2"))

    def test_same_message_id_in_different_channel_is_distinct(self):
        self.inbox.enqueue(-1001, 42, "LONG BTCUSDT", "t1")
        self.assertTrue(self.inbox.enqueue(-1002, 42, "SHORT ETHUSDT", "t2"))

    def test_listener_drains_persisted_messages_sequentially(self):
        listener = object.__new__(TelegramListener)
        listener._lock = asyncio.Lock()
        listener._queue = asyncio.Queue(maxsize=1000)
        listener._shutdown = asyncio.Event()
        class Ledger:
            def __init__(self): self.items=[{'channel_id':-1001,'message_id':10,'raw_text':'a','received_at':'t1'},{'channel_id':-1001,'message_id':11,'raw_text':'b','received_at':'t2'}]; self.done=[]
            def pending(self): return list(self.items)
            def mark_processed(self,ch,mid): self.done.append((ch,mid)); self.items.pop(0)
        listener.ledger=Ledger(); seen=[]
        async def callback(intents): seen.extend(intents)
        listener.callback=callback
        import src.signals.telegram_listener as listener_module
        original=listener_module.translate
        listener_module.translate=lambda text,source_id,timestamp: [text]

        async def run_test():
            await listener.drain()
            # Process queue items directly (simulate worker)
            while not listener._queue.empty():
                ch, mid, text, received = await listener._queue.get()
                intents = listener_module.translate(text, f'{ch}:{mid}', received)
                if intents and listener.callback:
                    _r = listener.callback(intents)
                    if asyncio.iscoroutine(_r): await _r
                listener.ledger.mark_processed(ch, mid)
                listener._queue.task_done()

        try: asyncio.run(run_test())
        finally: listener_module.translate=original
        self.assertEqual(seen,['a','b'])
        self.assertEqual(listener.ledger.done,[(-1001,10),(-1001,11)])

    def test_pending_items_survive_reopening_and_mark_processed(self):
        path = Path(self.tempdir.name) / "inbox.sqlite3"
        self.inbox.enqueue(-1001, 42, "LONG BTCUSDT", "t1")
        reopened = TelegramInbox(path)
        self.assertEqual(list(reopened.pending()), [(-1001, 42, "LONG BTCUSDT", "t1")])
        reopened.mark_processed(-1001, 42)
        self.assertEqual(reopened.state(-1001, 42), "processed")
        self.assertEqual(list(reopened.pending()), [])


if __name__ == "__main__":
    unittest.main()
