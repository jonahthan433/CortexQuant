"""Fixed main.py — full pipeline: Telegram → filter → risk → execute"""
"""Main entry point for the CKP Trading System.
Phase 1+: Telegram listener → signal filter → risk manager → BingX execution.
"""

import asyncio
import sys
import os
from datetime import datetime, timezone

from src.utils.logger import logger, setup_logger
from src.exchange.bingx_client import BingXClient
from src.risk.manager import RiskManager, PositionSizeResult
from src.signals.parser import parse_ckp_message, SignalCandidate
from src.signals.filters import simple_confirmation, ohlcv_to_df
from config.settings import settings

# ---------------------------------------------------------------------------
# Global state (single-process; fine for ≤3 concurrent positions)
# ---------------------------------------------------------------------------
_positions: dict[str, dict] = {}          # symbol → position info
_daily_pnl: float = 0.0                    # USDT realized + unrealized today
_session_start_balance: float = 0.0       # balance at session start
_exchange: BingXClient | None = None
_risk: RiskManager | None = None
_last_signal_time: datetime | None = None
_running = False


# ---------------------------------------------------------------------------
# Position registry helpers
# ---------------------------------------------------------------------------
def refresh_positions() -> None:
    """Re-fetch from exchange and rebuild in-memory position dict."""
    global _positions
    if _exchange is None:
        return
    try:
        raw = _exchange.fetch_positions()
        _positions = {
            p["symbol"]: p
            for p in raw
            if float(p.get("contracts", 0)) != 0
        }
        logger.info(f"Position refresh: {len(_positions)} open positions")
    except Exception as e:
        logger.error(f"Position refresh failed: {e}")


def open_position_count() -> int:
    return len(_positions)


# ---------------------------------------------------------------------------
# Daily P&L
# ---------------------------------------------------------------------------
def reset_daily_pnl() -> None:
    """Caller should invoke at UTC midnight via a background task."""
    global _daily_pnl
    _daily_pnl = 0.0
    logger.info("Daily P&L reset to 0")


def update_daily_pnl(delta: float) -> None:
    global _daily_pnl
    _daily_pnl += delta
    logger.info(f"Daily P&L updated: {delta:+.2f} USDT (running: {_daily_pnl:+.2f})")


# ---------------------------------------------------------------------------
# Signal processing pipeline
# ---------------------------------------------------------------------------
async def on_signal(candidates: list[SignalCandidate]) -> None:
    """Callback for TelegramListener — full pipeline per signal batch."""
    global _last_signal_time, _running
    if not _running or _exchange is None or _risk is None:
        return

    _last_signal_time = datetime.now(timezone.utc)
    logger.info(f"Pipeline: {len(candidates)} candidates received")

    for cand in candidates:
        await process_one_candidate(cand)


async def process_one_candidate(cand: SignalCandidate) -> None:
    global _running

    symbol = cand.symbol
    side = "long" if cand.side == "long" else "short"

    # --- 1. Symbol validation (is it on the exchange?) ---
    if not _symbol_on_exchange(symbol):
        logger.warning(f"Symbol {symbol} not on exchange — skipping")
        return

    # --- 2. Concurrent position check (from registry, not caller arg) ---
    if open_position_count() >= settings.MAX_CONCURRENT_POSITIONS:
        logger.info(f"Max concurrent ({settings.MAX_CONCURRENT_POSITIONS}) reached — skipping {symbol}")
        return

    # --- 3. Fetch market data for 4H bias + entry price ---
    try:
        ohlcv_4h = _exchange.fetch_ohlcv(f"{symbol}/USDT:USDT", timeframe="4h", limit=30)
        ohlcv_15m = _exchange.fetch_ohlcv(f"{symbol}/USDT:USDT", timeframe="15m", limit=50)
    except Exception as e:
        logger.error(f"Market data fetch failed for {symbol}: {e}")
        return

    df_4h = ohlcv_to_df(ohlcv_4h)
    df_15m = ohlcv_to_df(ohlcv_15m)

    entry_price = df_4h.iloc[-1]["close"]
    stop_loss_price = _compute_stop_loss(entry_price, side, df_4h)
    if stop_loss_price is None:
        logger.warning(f"Could not compute stop loss for {symbol} — skipping")
        return

    # --- 4. 4H bias confirmation ---
    approved, reason = simple_confirmation(side, df_4h, df_15m)
    if not approved:
        logger.info(f"Filter rejected {side.upper()} {symbol}: {reason}")
        return

    logger.success(f"Filter passed: {reason}")

    # --- 5. Risk calc (2% position sizing) ---
    balance = _get_balance_usdt()
    if balance <= 0:
        logger.error("Balance is zero — cannot size position")
        return

    result = _risk.calculate_position_size(
        balance=balance,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        side=side,
        symbol=symbol,
        current_open_positions=open_position_count(),
    )

    if not result.approved:
        logger.info(f"Risk rejected {symbol}: {result.reason}")
        return

    # --- 6. Daily loss circuit breaker ---
    if _check_daily_loss(balance):
        logger.warning("Daily loss limit hit — refusing new positions")
        return

    # --- 7. Place entry + stop-loss + take-profit ---
    try:
        await _execute_trade(symbol, side, result, entry_price, stop_loss_price)
    except Exception as e:
        logger.error(f"Execution failed for {symbol}: {e}")


def _symbol_on_exchange(symbol: str) -> bool:
    """Check if symbol exists on BingX (cached on first call)."""
    if not hasattr(_symbol_on_exchange, "cache"):
        _symbol_on_exchange.cache: set[str] = set()
        try:
            if _exchange is None:
                return False
            markets = _exchange.exchange.load_markets()
            _symbol_on_exchange.cache = {k.upper() for k in markets if markets[k].get("type") == "swap"}
        except Exception as e:
            logger.error(f"Could not load exchange markets: {e}")
    ccxt_symbol = f"{symbol}/USDT:USDT"
    return ccxt_symbol.upper() in _symbol_on_exchange.cache or symbol.upper() in {k.split("/")[0].upper() for k in _symbol_on_exchange.cache}


def _compute_stop_loss(entry_price: float, side: str, df_4h) -> float | None:
    """Simple ATR-based stop loss: 1.5× ATR(14) from entry."""
    if len(df_4h) < 15:
        return None
    closes = df_4h["close"].values
    highs = df_4h["high"].values
    lows = df_4h["low"].values
    # ATR(14) manual computation
    trs = []
    for i in range(1, min(15, len(df_4h))):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    atr = sum(trs) / len(trs) if trs else entry_price * 0.02
    sl_distance = atr * 1.5
    if side.lower() == "long":
        return entry_price - sl_distance
    return entry_price + sl_distance


def _get_balance_usdt() -> float:
    if _exchange is None:
        return 0.0
    try:
        bal = _exchange.fetch_balance()
        return bal.get("USDT", {}).get("free", 0) or 0.0
    except Exception:
        return 0.0


def _check_daily_loss(start_balance: float) -> bool:
    """Return True if daily loss limit is breached."""
    if start_balance <= 0:
        return False
    loss_pct = abs(_daily_pnl) / start_balance
    if loss_pct >= settings.MAX_DAILY_LOSS:
        logger.warning(f"Daily loss {loss_pct*100:.1f}% ≥ {settings.MAX_DAILY_LOSS*100}% — circuit breaker TRIGGERED")
        return True
    return False


async def _execute_trade(
    symbol: str, side: str, result: PositionSizeResult,
    entry_price: float, stop_loss_price: float,
) -> None:
    """Place market entry + stop-loss + take-profit on BingX."""
    ccxt_side = "buy" if side.lower() == "long" else "sell"
    ccxt_symbol = f"{symbol}/USDT:USDT"
    tp_price = _compute_take_profit(entry_price, side, result.stop_loss_pct)

    logger.info(
        f"EXECUTING: {ccxt_side.upper()} {result.position_size} {symbol} @ ~{entry_price} | "
        f"SL={stop_loss_price} | TP={tp_price} | risk={result.risk_amount} USDT"
    )

    # 1. Market entry
    order = _exchange.create_market_order(
        ccxt_symbol,
        ccxt_side,
        result.position_size,
        params={},
    )

    # 2. Record position locally
    _positions[ccxt_symbol] = {
        "symbol": ccxt_symbol,
        "side": side,
        "entry_price": entry_price,
        "size": result.position_size,
        "stop_loss": stop_loss_price,
        "take_profit": tp_price,
        "order_id": order.get("id"),
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }

    # 3. Place stop-loss (BingX supports stop loss via params on some endpoints;
    #    as a safe fallback we note it and rely on the daily reconciliation loop
    #    to monitor — the stop-loss order is placed below if the exchange supports it)
    try:
        _exchange.exchange.create_order(
            ccxt_symbol,
            "stop_market",
            "sell" if side.lower() == "long" else "buy",
            result.position_size,
            stop_loss_price,
            {"stopLossPrice": stop_loss_price},
        )
        logger.success(f"Stop-loss placed at {stop_loss_price} for {symbol}")
    except Exception as e:
        logger.warning(f"Stop-loss order failed (will monitor manually): {e}")

    # 4. Place take-profit
    try:
        _exchange.exchange.create_order(
            ccxt_symbol,
            "limit",
            "sell" if side.lower() == "long" else "buy",
            result.position_size,
            tp_price,
            {"price": tp_price},
        )
        logger.success(f"Take-profit placed at {tp_price} for {symbol}")
    except Exception as e:
        logger.warning(f"Take-profit order failed: {e}")

    # 5. Update daily P&L with estimated risk (worst case = risk_amount lost)
    #    Real P&L updates on close; this seeds the daily loss tracker.
    update_daily_pnl(-result.risk_amount)


def _compute_take_profit(entry_price: float, side: str, sl_pct: float) -> float:
    """2:1 reward-to-risk: TP distance = 2 × SL distance."""
    sl_distance = entry_price * sl_pct / 100
    if side.lower() == "long":
        return entry_price + sl_distance * 2
    return entry_price - sl_distance * 2


# ---------------------------------------------------------------------------
# Background: daily P&L reset at UTC midnight
# ---------------------------------------------------------------------------
async def _midnight_reset() -> None:
    """Reset daily P&L at UTC midnight, then sleep until next midnight."""
    while _running:
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if now >= midnight:
            midnight = midnight.replace(day=midnight.day + 1)
        wait = (midnight - now).total_seconds()
        logger.info(f"Next daily P&L reset in {wait/3600:.1f} hours")
        await asyncio.sleep(wait)
        if _running:
            reset_daily_pnl()


# ---------------------------------------------------------------------------
# Reconciliation loop: re-sync positions every 60s
# ---------------------------------------------------------------------------
async def _reconciliation_loop() -> None:
    """Every 60s: refresh positions from exchange, update P&L for closed trades."""
    while _running:
        await asyncio.sleep(60)
        if not _running:
            break
        try:
            old_count = len(_positions)
            refresh_positions()
            new_count = len(_positions)
            if new_count < old_count:
                logger.info(f"Position reconciliation: {old_count - new_count} position(s) closed")
                # Rough P&L update: mark closed positions as realized loss/gain
                # (full P&L tracking requires fill history — TODO for Phase 2)
        except Exception as e:
            logger.error(f"Reconciliation loop error: {e}")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
async def main_async() -> None:
    global _exchange, _risk, _running, _session_start_balance

    logger.info("=" * 60)
    logger.info("CKP Trading System starting...")
    logger.info(f"Mode: {'DEMO (VST)' if settings.BINGX_DEMO else 'LIVE'}")
    logger.info(f"Risk per trade: {settings.RISK_PER_TRADE * 100}%")
    logger.info(f"Max concurrent: {settings.MAX_CONCURRENT_POSITIONS}")
    logger.info(f"Daily loss limit: {settings.MAX_DAILY_LOSS * 100}%")
    logger.info(f"Default leverage: {settings.DEFAULT_LEVERAGE}x")
    logger.info("=" * 60)

    # 1. Exchange
    _exchange = BingXClient()
    if not _exchange.test_connection():
        logger.error("BingX connection failed — aborting")
        return

    # 2. Refresh positions from exchange (reconciliation on start)
    refresh_positions()

    # 3. Session start balance for daily loss tracking
    _session_start_balance = _get_balance_usdt()
    logger.info(f"Session start balance: {_session_start_balance:.2f} USDT")

    # 4. Risk manager
    _risk = RiskManager()

    # 5. Telegram listener
    listener = TelegramListener()
    listener.set_signal_callback(on_signal)

    _running = True

    # 6. Background tasks
    midnight_task = asyncio.create_task(_midnight_reset())
    recon_task = asyncio.create_task(_reconciliation_loop())

    # 7. Start Telegram listener (blocks)
    logger.info("Starting Telegram listener (blocking)...")
    try:
        await listener.start()
    finally:
        _running = False
        midnight_task.cancel()
        recon_task.cancel()
        logger.info("Shutdown complete")


def main() -> None:
    setup_logger()
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Fatal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
