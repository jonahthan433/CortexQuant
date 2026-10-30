"""BingX client wrapper using ccxt.

Supports both live and demo (VST) modes.
"""

import ccxt
import threading
from datetime import timezone
from loguru import logger
from config.settings import settings
from src.paper_policy import resolve_execution_target


class BingXClient:
    def __init__(self):
        self._assert_execution_gate()
        # Route strictly by resolved target: only bingx_demo uses the VST sandbox.
        self.demo = resolve_execution_target() == "bingx_demo"
        if self.demo and not settings.BINGX_DEMO:
            raise RuntimeError("BingX order adapter blocked: EXECUTION_TARGET=bingx_demo requires BINGX_DEMO=true")
        self.exchange = self._create_exchange()
        mode = "DEMO (VST)" if self.demo else "LIVE"
        logger.info(f"BingX client initialized in {mode} mode")
        # Lock to prevent TOCTOU between gate check and order placement
        self._gate_lock = threading.Lock()

    @staticmethod
    def _assert_execution_gate():
        # Keep the guard inside the adapter as well as the runtime router.
        from src.paper_policy import resolve_execution_target
        target = resolve_execution_target()
        if target == "paper":
            raise RuntimeError("BingX order adapter blocked: EXECUTION_TARGET=paper (internal ledger only)")
        if target == "live" and settings.LIVE_MODE != "true":
            raise RuntimeError("BingX order adapter blocked: real money requires EXECUTION_TARGET=live AND LIVE_MODE=true")
        from src.ledger import PortfolioLedger
        stamp = PortfolioLedger().get_state('paper_start_ts')
        if not stamp:
            raise RuntimeError("BingX order adapter blocked: paper start timestamp is missing")
        from datetime import datetime
        started = datetime.fromisoformat(stamp) if isinstance(stamp, str) else datetime.fromisoformat(stamp['ts'])
        if (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds() < settings.PAPER_DAYS * 86400:
            raise RuntimeError("BingX order adapter blocked: mandatory paper period has not elapsed")

    def _create_exchange(self) -> ccxt.bingx:
        exchange = ccxt.bingx({
            "apiKey": settings.BINGX_API_KEY,
            "secret": settings.BINGX_API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",  # perpetual futures
            },
            # TLS verification settings
            "verify": getattr(settings, 'BINGX_VERIFY_SSL', True),
            "cert": getattr(settings, 'BINGX_CLIENT_CERT', None),
        })

        # Custom CA bundle for certificate pinning
        ca_bundle = getattr(settings, 'BINGX_CA_BUNDLE', None)
        if ca_bundle:
            exchange.verify = ca_bundle

        if self.demo:
            exchange.set_sandbox_mode(True)
            exchange.urls["api"]["swap"] = "https://open-api-vst.bingx.com/openApi"

        return exchange

    # ------------------------------------------------------------------
    # Core market data
    # ------------------------------------------------------------------

    def fetch_balance(self):
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            raise

    def _balance_value(self, field: str) -> float:
        balance = self.fetch_balance()
        value = balance.get("USDT", {}).get(field)
        if value is None:
            value = balance.get(field, {}).get("USDT")
        if value is None:
            raise RuntimeError(f"BingX balance response has no USDT {field} value")
        return float(value)

    def get_free_balance(self) -> float:
        """Return available USDT; this method was previously missing from the adapter."""
        return self._balance_value("free")

    def get_total_balance(self) -> float:
        """Return total USDT equity used for conservative daily drawdown checks."""
        return self._balance_value("total")

    def fetch_positions(self, symbol: str = None):
        try:
            positions = self.exchange.fetch_positions(symbols=[symbol] if symbol else None)
            return [p for p in positions if float(p.get("contracts", 0)) != 0]
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            raise

    def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 200):
        try:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as e:
            logger.error(f"Failed to fetch OHLCV for {symbol}: {e}")
            raise

    # ------------------------------------------------------------------
    # Symbol validation
    # ------------------------------------------------------------------

    def symbol_exists(self, symbol: str) -> bool:
        """Check whether a ccxt-format symbol is tradeable on BingX."""
        try:
            markets = self.exchange.load_markets()
            return symbol.upper() in {k.upper() for k in markets}
        except Exception as e:
            logger.error(f"symbol_exists check failed for {symbol}: {e}")
            return False

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def _with_gate(self, func, *args, **kwargs):
        """Execute function with gate check immediately before the call."""
        with self._gate_lock:
            self._assert_execution_gate()
            return func(*args, **kwargs)

    def create_market_order(self, symbol: str, side: str, amount: float, params: dict = None):
        return self._with_gate(self._create_market_order_impl, symbol, side, amount, params)

    def _create_market_order_impl(self, symbol: str, side: str, amount: float, params: dict = None):
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=amount,
                params=params or {},
            )
            logger.info(f"Market order placed: {side} {amount} {symbol} -> {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
            raise

    def place_stop_loss(self, symbol: str, side: str, sl_price: float, amount: float) -> dict:
        return self._with_gate(self._place_stop_loss_impl, symbol, side, sl_price, amount)

    def _place_stop_loss_impl(self, symbol: str, side: str, sl_price: float, amount: float) -> dict:
        """
        Place a stop-loss order.
        side: 'buy' for short SL (cover), 'sell' for long SL.
        """
        if side.lower() not in {"buy", "sell"}:
            raise ValueError("protective order side must be buy or sell")
        stop_side = side.lower()  # caller supplies the closing side; do not invert it again
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="stop_loss",
                side=stop_side,
                amount=amount,
                stopPrice=sl_price,
                params={"stopPrice": sl_price, "reduceOnly": True},
            )
            logger.info(f"SL order placed: {stop_side} {amount} {symbol} @ {sl_price}")
            return order
        except Exception as e:
            logger.error(f"Failed to place stop-loss: {e}")
            raise

    def place_take_profit(self, symbol: str, side: str, tp_price: float, amount: float) -> dict:
        return self._with_gate(self._place_take_profit_impl, symbol, side, tp_price, amount)

    def _place_take_profit_impl(self, symbol: str, side: str, tp_price: float, amount: float) -> dict:
        """
        Place a take-profit order.
        side: 'buy' for short TP (cover), 'sell' for long TP.
        """
        if side.lower() not in {"buy", "sell"}:
            raise ValueError("protective order side must be buy or sell")
        tp_side = side.lower()  # caller supplies the closing side; do not invert it again
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="take_profit",
                side=tp_side,
                amount=amount,
                stopPrice=tp_price,
                params={"stopPrice": tp_price, "reduceOnly": True},
            )
            logger.info(f"TP order placed: {tp_side} {amount} {symbol} @ {tp_price}")
            return order
        except Exception as e:
            logger.error(f"Failed to place take-profit: {e}")
            raise

    def cancel_order(self, order_id: str, symbol: str):
        """Cancel one known protective order."""
        return self._with_gate(self.exchange.cancel_order, order_id, symbol)

    # ------------------------------------------------------------------
    # Leverage
    # ------------------------------------------------------------------

    def set_leverage(self, symbol: str, leverage: int):
        return self._with_gate(self._set_leverage_impl, symbol, leverage)

    def _set_leverage_impl(self, symbol: str, leverage: int):
        try:
            self.exchange.set_leverage(leverage, symbol)
            logger.info(f"Leverage set to {leverage}x for {symbol}")
        except Exception as e:
            logger.warning(f"Could not set leverage (may already be set): {e}")

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def test_connection(self) -> bool:
        try:
            balance = self.fetch_balance()
            free = balance.get("USDT", {}).get("free", 0) or balance.get("free", {}).get("USDT", 0)
            logger.success(f"Connection OK | Free USDT (or VST): {free}")
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    def get_client_order_id(self, prefix: str, intent) -> str:
        """Generate deterministic client order ID for idempotency."""
        from uuid import uuid5, NAMESPACE_URL
        base = f"{intent.source_id}:{intent.symbol}:{prefix}"
        return str(uuid5(NAMESPACE_URL, base))

    async def set_leverage_async(self, symbol: str, leverage: int) -> bool:
        """Async wrapper for set_leverage."""
        return self.set_leverage(symbol, leverage)
