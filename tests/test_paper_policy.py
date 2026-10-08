import unittest
from datetime import datetime, timedelta, timezone
from src.paper_policy import execution_mode, days_remaining

class PaperPolicyTests(unittest.TestCase):
    def setUp(self): self.start=datetime(2026,1,1,tzinfo=timezone.utc)
    def test_less_than_seven_days_forces_paper_even_with_live_flag(self):
        self.assertEqual(execution_mode(self.start,now=self.start+timedelta(days=6,hours=23),live_flag='true'), 'paper')
    def test_exact_seven_days_allows_explicit_live(self):
        self.assertEqual(execution_mode(self.start,now=self.start+timedelta(days=7),live_flag='true',bingx_demo=False), 'live')
    def test_expiry_never_auto_enables_live(self):
        for flag in ('false','True','TRUE','1',' true'):
            self.assertEqual(execution_mode(self.start,now=self.start+timedelta(days=8),live_flag=flag,bingx_demo=False),'paper')
    def test_demo_flag_alone_does_not_enable_live(self):
        self.assertEqual(execution_mode(self.start,now=self.start+timedelta(days=8),live_flag='false',bingx_demo=False),'paper')
    def test_demo_adapter_remains_demo_after_expiry_and_opt_in(self):
        self.assertEqual(execution_mode(self.start,now=self.start+timedelta(days=7),live_flag='true',bingx_demo=True),'bingx_demo')
    def test_remaining_days(self):
        self.assertEqual(days_remaining(self.start,now=self.start+timedelta(days=2),paper_days=7),5)
