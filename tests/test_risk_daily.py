"""Daily loss circuit breaker tests."""

import unittest
from src.risk.manager import RiskManager


class DailyLossTests(unittest.TestCase):
    def setUp(self):
        self.risk = RiskManager()
        self.risk.set_session_start_balance(10_000.0)

    def test_loss_over_limit_trips(self):
        self.risk.set_daily_pnl(-500.0)
        self.assertTrue(self.risk.check_daily_loss())

    def test_loss_below_limit_does_not_trip(self):
        self.risk.set_daily_pnl(-499.0)
        self.assertFalse(self.risk.check_daily_loss())

    def test_positive_pnl_does_not_trip(self):
        self.risk.set_daily_pnl(900.0)
        self.assertFalse(self.risk.check_daily_loss())

    def test_equity_snapshots_track_daily_delta(self):
        self.risk.update_daily_equity(10_000.0)
        self.risk.update_daily_equity(9_400.0)
        self.assertEqual(self.risk.daily_pnl, -600.0)
        self.assertTrue(self.risk.check_daily_loss())


if __name__ == "__main__":
    unittest.main()
