import unittest
from datetime import datetime, timezone
from src.signals.translator import translate

class TranslatorTests(unittest.TestCase):
    def test_ckp_blocks_become_validated_intents_with_levels(self):
        text='LONG OPPORTUNITIES\n1. BTCUSDT\n Entry: 100 | SL: 95 | TP1: 110 | TP2: 120\nSHORT OPPORTUNITIES\n1. ETHUSDT\n Entry: 200 | SL: 210 | TP: 180'
        intents=translate(text,'-100:55',datetime.now(timezone.utc))
        btc=next(i for i in intents if i.symbol=='BTCUSDT')
        eth=next(i for i in intents if i.symbol=='ETHUSDT')
        self.assertEqual((btc.side.value,btc.entry,btc.stop,btc.targets),('long',100,95,[110,120]))
        self.assertEqual((eth.side.value,eth.entry,eth.stop,eth.targets),('short',200,210,[180]))
    def test_missing_prices_are_left_for_market_strategy(self):
        i=translate('LONG OPPORTUNITIES\n1. SOLUSDT','channel:1')[0]
        self.assertIsNone(i.entry); self.assertIsNone(i.stop)
