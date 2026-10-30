"""Critical execution-side and failed-protection tests."""

import unittest
from unittest.mock import MagicMock

from src.exchange.bingx_client import BingXClient
from src.execution.protection import PositionFlattenFailure, protect_or_flatten


class BingXProtectiveSideTests(unittest.TestCase):
    def setUp(self):
        self.client = object.__new__(BingXClient)
        self.client.exchange = MagicMock()
        self.client._assert_execution_gate = MagicMock()
        self.client._gate_lock = MagicMock()
        self.client._gate_lock.__enter__ = MagicMock(return_value=None)
        self.client._gate_lock.__exit__ = MagicMock(return_value=None)
        self.client.exchange.create_order.return_value = {"id": "protect-1"}

    def test_long_uses_sell_for_stop_and_take_profit(self):
        self.client.place_stop_loss("BTC/USDT:USDT", "sell", 90, 1)
        self.client.place_take_profit("BTC/USDT:USDT", "sell", 120, 1)
        self.assertEqual(self.client.exchange.create_order.call_args_list[0].kwargs["side"], "sell")
        self.assertEqual(self.client.exchange.create_order.call_args_list[1].kwargs["side"], "sell")
        for call in self.client.exchange.create_order.call_args_list:
            self.assertTrue(call.kwargs["params"]["reduceOnly"])

    def test_short_uses_buy_for_stop_and_take_profit(self):
        self.client.place_stop_loss("BTC/USDT:USDT", "buy", 110, 1)
        self.client.place_take_profit("BTC/USDT:USDT", "buy", 80, 1)
        self.assertEqual(self.client.exchange.create_order.call_args_list[0].kwargs["side"], "buy")
        self.assertEqual(self.client.exchange.create_order.call_args_list[1].kwargs["side"], "buy")


class ProtectionFailureTests(unittest.TestCase):
    def test_stop_failure_flattens_without_attempting_target(self):
        exchange = MagicMock()
        exchange.place_stop_loss.side_effect = RuntimeError("failed")
        exchange.create_market_order.return_value = {"id": "flatten-1"}
        result = protect_or_flatten(exchange, symbol="BTC/USDT:USDT", position_side="long",
                                    amount=1, stop_price=90, target_price=120)
        self.assertFalse(result)
        exchange.place_take_profit.assert_not_called()
        exchange.create_market_order.assert_called_once_with(
            symbol="BTC/USDT:USDT", side="sell", amount=1, params={"reduceOnly": True})

    def test_target_failure_cancels_stop_then_flattens(self):
        exchange = MagicMock()
        exchange.place_stop_loss.return_value = {"id": "stop-1"}
        exchange.place_take_profit.side_effect = RuntimeError("failed")
        exchange.create_market_order.return_value = {"id": "flatten-1"}
        result = protect_or_flatten(exchange, symbol="BTC/USDT:USDT", position_side="short",
                                    amount=2, stop_price=110, target_price=80)
        self.assertFalse(result)
        exchange.cancel_order.assert_called_once_with("stop-1", "BTC/USDT:USDT")
        exchange.create_market_order.assert_called_once_with(
            symbol="BTC/USDT:USDT", side="buy", amount=2, params={"reduceOnly": True})

    def test_still_open_after_flatten_raises_emergency_stop(self):
        exchange = MagicMock()
        exchange.place_stop_loss.side_effect = RuntimeError("failed")
        exchange.create_market_order.return_value = {"id": "flatten-1"}
        exchange.fetch_positions.return_value = [{"contracts": 1}]
        with self.assertRaises(PositionFlattenFailure):
            protect_or_flatten(exchange, symbol="BTC/USDT:USDT", position_side="long",
                               amount=1, stop_price=90, target_price=120)

    def test_flatten_failure_raises_emergency_stop(self):
        exchange = MagicMock()
        exchange.place_stop_loss.side_effect = RuntimeError("failed")
        exchange.create_market_order.side_effect = RuntimeError("flatten failed")
        with self.assertRaises(PositionFlattenFailure):
            protect_or_flatten(exchange, symbol="BTC/USDT:USDT", position_side="long",
                               amount=1, stop_price=90, target_price=120)

    def test_missing_stop_order_id_is_treated_as_failure(self):
        exchange = MagicMock()
        exchange.place_stop_loss.return_value = {"id": None}
        exchange.create_market_order.return_value = {"id": "flatten-1"}
        result = protect_or_flatten(exchange, symbol="BTC/USDT:USDT", position_side="long",
                                    amount=1, stop_price=90, target_price=120)
        self.assertFalse(result)
        exchange.place_take_profit.assert_not_called()
        exchange.create_market_order.assert_called_once()


if __name__ == "__main__":
    unittest.main()
