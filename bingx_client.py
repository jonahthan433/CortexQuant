"""Fixed exchange/bingx_client.py — retry, symbol validation, SL/TP params"""
"""BingX client wrapper using ccxt.
Supports both live and demo (VST) modes.
Adds: retry on transient errors, explicit SL/TP order placement.
"""

import ccxt
from loguru import logger
from config.settings import settings
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import time


class BingXClient:
    def __init__(self):
        self.demo = settings.BINGX_DEMO
        self.exchange = self._create_exchange()
        self.markets_cache: dict | None = None
        mode = "DEMO (VST)" if self.demo else "LIVE"
        logger.info(f"BingX client initialized in {mode} mode")

    def _create_exchange(self) -> ccxt.bingx:
        exchange = ccxt.bingx({
            "apiKey": settings.BINGX_API_KEY,
            "secret": settings.BINGX_API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        })

        if self.demo:
            exchange.set_sandbox_mode(True)
            exchange.urls["api"]["swap"] = "https://open-api-vst.bingx.com/openApi"

        return exchange

    def _load_markets_cached(self) -> dict:
        """Load markets once, cache for symbol validation."""
        if self.markets_cache is None:
            try:
                self.markets_cache = self.exchange.load_markets()
                swap_symbols = {
                    k for k, v in self.markets_cache.items()
                    if v.get("type") == "swap"
                }
                logger.info(f"Loaded {len(swap_symbols)} swap markets from BingX")
            except Exception as e:
                logger.error(f"Failed to load markets: {e}")
                self.markets_cache = {}
        return self.markets_cache

    def symbol_exists(self, symbol: str) -> bool:
        """Check if a ticker symbol exists as a swap market on BingX."""
        markets = self._load_markets_cached()
        # Try common variants
        for fmt in (f"{symbol}/USDT:USDT", f"{symbol}/USDT", f"{symbol}/USD:USDT"):
            if fmt.upper() in {k.upper() for k in markets}:
                return True
        return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ccxt.BaseError, ccxt.ExchangeError, ccxt.NetworkError)),
        reraise=True,
    )
    def fetch_balance(self):
        try:
            balance = self.exchange.fetch_balance()
            return balance
        except Exception as e:
            logger.error(f"fetch_balance failed after retries: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ccxt.BaseError, ccxt.ExchangeError, ccxt.NetworkError)),
        reraise=True,
    )
    def fetch_positions(self, symbol: str = None):
        try:
            positions = self.exchange.fetch_positions(symbols=[symbol] if symbol else None)
            return [p for p in positions if float(p.get("contracts", 0)) != 0]
        except Exception as e:
            logger.error(f"fetch_positions failed after retries: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ccxt.BaseError, ccxt.ExchangeError, ccxt.NetworkError)),
        reraise=True,
    )
    def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 200):
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            return ohlcv
        except Exception as e:
            logger.error(f"fetch_ohlcv failed for {symbol}: {e}")
            raise

    def create_market_order(self, symbol: str, side: str, amount: float, params: dict = None):
        """
        Place a market order. side: 'buy' or 'sell'.
        """
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=amount,
                params=params or {},
            )
            logger.info(f"Market order placed: {side} {amount} {symbol} → {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
            raise

    def place_stop_loss(self, symbol: str, side: str, amount: float, stop_price: float) -> bool:
        """
        Place a stop-loss order. Returns True if placed.
        For BingX perpetuals: uses stop_market order type.
        side = 'buy' for short position SL (cover), 'sell' for long position SL.
        """
        ccxt_side = "sell" if side.lower() == "long" else "buy"
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="stop_market",
                side=ccxt_side,
                amount=amount,
                price=stop_price,
                params={"stopPrice": stop_price},
            )
            logger.success(f"Stop-loss placed: {ccxt_side} {amount} {symbol} @ {stop_price} → {order.get('id')}")
            return True
        except Exception as e:
            logger.warning(f"Stop-loss placement failed for {symbol}: {e}")
            return False

    def place_take_profit(self, symbol: str, side: str, amount: float, tp_price: float) -> bool:
        """
        Place a take-profit limit order. Returns True if placed.
        """
        ccxt_side = "sell" if side.lower() == "long" else "buy"
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=ccxt_side,
                amount=amount,
                price=tp_price,
                params={"price": tp_price},
            )
            logger.success(f"Take-profit placed: {ccxt_side} {amount} {symbol} @ {tp_price} → {order.get('id')}")
            return True
        except Exception as e:
            logger.warning(f"Take-profit placement failed for {symbol}: {e}")
            return False

    def set_leverage(self, symbol: str, leverage: int):
        try:
            self.exchange.set_leverage(leverage, symbol)
            logger.info(f"Leverage set to {leverage}x for {symbol}")
        except Exception as e:
            logger.warning(f"Could not set leverage (may already be set): {e}")

    def test_connection(self) -> bool:
        try:
            balance = self.fetch_balance()
            free = balance.get("USDT", {}).get("free", 0) or balance.get("free", {}).get("USDT", 0)
            logger.success(f"Connection OK | Free USDT (or VST): {free}")
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
