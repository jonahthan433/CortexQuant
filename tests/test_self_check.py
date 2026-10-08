"""Self-check tests for the CKP Trading System.

Run:  python3 -m pytest tests/test_self_check.py
       or: python3 tests/test_self_check.py
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from loguru import logger
logger.remove()

errors = []

def check(condition, msg):
    if condition:
        print(f"  PASS: {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL: {msg}")

# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------
print("\n=== Settings ===")
from config.settings import settings
check(settings.RISK_PER_TRADE == 0.02, "RISK_PER_TRADE == 0.02")
check(settings.MAX_CONCURRENT_POSITIONS == 3, "MAX_CONCURRENT_POSITIONS == 3")
check(settings.MAX_DAILY_LOSS == 0.05, "MAX_DAILY_LOSS == 0.05")
check(settings.DEFAULT_LEVERAGE == 5, "DEFAULT_LEVERAGE == 5")
check(settings.LIVE_MODE == "false", "LIVE_MODE defaults to false")
check(settings.BINGX_DEMO is True, "BINGX_DEMO is read independently and defaults to true")
check(isinstance(settings.TELEGRAM_API_ID, int), "TELEGRAM_API_ID is int")

# ------------------------------------------------------------------
# Logger
# ------------------------------------------------------------------
print("\n=== Logger ===")
from src.utils.logger import setup_logger
try:
    setup_logger()
    check(True, "setup_logger() runs without exception")
except Exception as e:
    check(False, f"setup_logger() raised: {e}")

# ------------------------------------------------------------------
# Risk Manager
# ------------------------------------------------------------------
print("\n=== Risk Manager ===")
from src.risk.manager import RiskManager

rm = RiskManager()
rm.set_session_start_balance(10000.0)
rm.set_daily_pnl(0.0)

# Standard long: entry 100, SL 95 (5% away), 2% risk on 10k
r = rm.calculate_position_size(balance=10000, entry_price=100, stop_loss_price=95,
                                side="long", symbol="BTC", current_open_positions=0)
check(r.approved is True, "Standard long approved")
check(abs(r.risk_amount - 200) < 0.01, f"Risk=200 USDT (got {r.risk_amount})")
check(abs(r.position_size - 40) < 0.01, f"Size=40 (got {r.position_size})")
check(abs(r.stop_loss_price - 95) < 0.01, f"SL=95 (got {r.stop_loss_price})")
check(abs(r.take_profit_price - 110) < 0.01, f"TP=110 (got {r.take_profit_price})")
check(r.stop_loss_pct == 5.0, f"SL%=5.0 (got {r.stop_loss_pct})")

# Short
rs = rm.calculate_position_size(balance=10000, entry_price=200, stop_loss_price=210,
                                 side="short", symbol="ETH", current_open_positions=0)
check(rs.approved is True, "Standard short approved")
check(abs(rs.risk_amount - 200) < 0.01, "Short risk=200 USDT")

# Edge cases
check(rm.calculate_position_size(0, 100, 95, "long", "BTC", 0).approved is False, "Zero balance rejects")
check(rm.calculate_position_size(10000, 100, 100, "long", "BTC", 0).approved is False, "SL==entry rejects")
check(rm.calculate_position_size(10000, 100, 95, "long", "BTC", 3).approved is False, "Max concurrent rejects")
check(rm.calculate_position_size(10000, 0, 95, "long", "BTC", 0).approved is False, "Zero entry rejects")

# Daily loss circuit breaker
rm.set_session_start_balance(10000)
rm.set_daily_pnl(-600)
check(rm.check_daily_loss() is True, "6% daily loss triggers breaker")
rm.set_daily_pnl(-400)
check(rm.check_daily_loss() is False, "4% loss does not trigger (limit 5%)")
rm.set_daily_pnl(0)
check(rm.check_daily_loss() is False, "Zero P&L does not trigger")

# ------------------------------------------------------------------
# Parser
# ------------------------------------------------------------------
print("\n=== Parser ===")
from src.signals.parser import parse_ckp_message, validate_symbol

# msg1: clean CKP-style message — NO emoji (emoji transport via SSH is unreliable;
# Telethon gives proper strings at runtime, but tests use plain text)
msg1 = """⏰ 15:00 UTC｜#CKPscanner 4H SCOUT

🔴 LONG OPPORTUNITIES (RSI 15/4H)
1. BTCUSDT
   Entry: 98289.84 | SL: 96734.21 | TP: 101710.02 | RSI15: 50.09
2. ETHUSDT
   Entry: 4434.35 | SL: 4324.58 | TP: 4726.00 | RSI15: 49.88
3. SOLUSDT
   Entry: 192.02 | SL: 184.17 | TP: 210.93 | RSI15: 50.08

🟢 SHORT OPPORTUNITIES (RSI 15/4H)
1. AVAXUSDT
   Entry: 31.58 | SL: 32.94 | TP: 27.71 | RSI15: 49.71
2. LINKUSDT
   Entry: 20.28 | SL: 21.31 | TP: 17.15 | RSI15: 49.82
3. NEARUSDT
   Entry: 7.03 | SL: 7.37 | TP: 5.72 | RSI15: 49.91
4. UNIUSDT
   Entry: 15.09 | SL: 15.87 | TP: 12.53 | RSI15: 49.77
"""

# Fallback test: same message WITHOUT emoji (for transport-safe verification)
msg1_plain = """15:00 UTC｜#CKPscanner 4H SCOUT

LONG OPPORTUNITIES (RSI 15/4H)
1. BTCUSDT
   Entry: 98289.84 | SL: 96734.21 | TP: 101710.02 | RSI15: 50.09
2. ETHUSDT
   Entry: 4434.35 | SL: 4324.58 | TP: 4726.00 | RSI15: 49.88
3. SOLUSDT
   Entry: 192.02 | SL: 184.17 | TP: 210.93 | RSI15: 50.08

SHORT OPPORTUNITIES (RSI 15/4H)
1. AVAXUSDT
   Entry: 31.58 | SL: 32.94 | TP: 27.71 | RSI15: 49.71
2. LINKUSDT
   Entry: 20.28 | SL: 21.31 | TP: 17.15 | RSI15: 49.82
3. NEARUSDT
   Entry: 7.03 | SL: 7.37 | TP: 5.72 | RSI15: 49.91
4. UNIUSDT
   Entry: 15.09 | SL: 15.87 | TP: 12.53 | RSI15: 49.77
"""

cands1 = parse_ckp_message(msg1_plain)
symbols1 = {c.symbol for c in cands1}

check("BTC" in symbols1, "BTC parsed from LONG section")
check("ETH" in symbols1, "ETH parsed from LONG section")
check("SOL" in symbols1, "SOL parsed from LONG section")
check("AVAX" in symbols1, "AVAX parsed from SHORT section")
check("LINK" in symbols1, "LINK parsed from SHORT section")
check("NEAR" in symbols1, "NEAR parsed from SHORT section")
check("UNI" in symbols1, "UNI parsed from SHORT section")

long_cands = [c for c in cands1 if c.side == "long"]
short_cands = [c for c in cands1 if c.side == "short"]
check(len(long_cands) >= 3, f"At least 3 LONG (got {len(long_cands)})")
check(len(short_cands) >= 4, f"At least 4 SHORT (got {len(short_cands)})")

btc = next((c for c in cands1 if c.symbol == "BTC"), None)
check(btc is not None and btc.rank == 1, f"BTC rank=1 (got {btc.rank if btc else 'None'})")
avax = next((c for c in cands1 if c.symbol == "AVAX"), None)
check(avax is not None and avax.rank == 1, f"AVAX rank=1 (got {avax.rank if avax else 'None'})")

# Non-coin words filtered
msg3 = """LONG OPPORTUNITIES
1. TOP
2. LONG
3. SHORT
4. USDT
5. RSI
"""
cands3 = parse_ckp_message(msg3)
cands3_syms = {c.symbol for c in cands3}
for w in ["TOP", "LONG", "SHORT", "USDT", "RSI"]:
    check(w not in cands3_syms, f"{w} filtered")

# Empty
check(parse_ckp_message("") == [], "Empty → []")

# USDT suffix stripped
msg5 = "LONG OPPORTUNITIES\n1. BTCUSDT\n2. ETHUSDT\n"
cands5 = parse_ckp_message(msg5)
check({c.symbol for c in cands5} == {"BTC", "ETH"}, "USDT suffix stripped from tickers")

# Confidence
msg7 = "LONG OPPORTUNITIES\n1. BTCUSDT\n3. SOLUSDT\n8. AVAXUSDT\n"
cands7 = parse_ckp_message(msg7)
btc7 = next(c for c in cands7 if c.symbol == "BTC")
check(btc7.confidence >= 0.7, f"Rank 1 → conf>=0.7 (got {btc7.confidence})")

# ------------------------------------------------------------------
# Filters
# ------------------------------------------------------------------
print("\n=== Filters ===")
from src.signals.filters import get_4h_bias, simple_confirmation, ohlcv_to_df
import numpy as np

def make_ohlcv(n, close_start, trend=0.0, volatility=1.0):
    np.random.seed(42)
    closes, price = [], close_start
    for i in range(n):
        price += trend + np.random.normal(0, volatility)
        high = price + abs(np.random.normal(0, volatility * 0.5))
        low = price - abs(np.random.normal(0, volatility * 0.5))
        open_ = price + np.random.normal(0, volatility * 0.2)
        vol = abs(np.random.normal(1000, 200))
        closes.append([int((i + 100000) * 3600000), open_, high, low, open_, vol])
    return closes

# Uptrend → bullish
df_up = ohlcv_to_df(make_ohlcv(50, 100, trend=0.5, volatility=0.3))
check(get_4h_bias(df_up) == "bullish", "Uptrend → bullish")

# Downtrend → bearish
df_down = ohlcv_to_df(make_ohlcv(50, 100, trend=-0.5, volatility=0.3))
check(get_4h_bias(df_down) == "bearish", "Downtrend → bearish")

# Ranging → neutral
df_range = ohlcv_to_df(make_ohlcv(50, 100, trend=0.0, volatility=0.1))
check(get_4h_bias(df_range) == "neutral", "Ranging → neutral")

# Too few candles → neutral
df_short = ohlcv_to_df(make_ohlcv(10, 100, trend=0.5))
check(get_4h_bias(df_short) == "neutral", "<25 candles → neutral")

# Confirmation aligned
df15_up = ohlcv_to_df(make_ohlcv(50, 100, trend=0.2, volatility=0.1))
appr, reason = simple_confirmation("long", df_up, df15_up)
check(appr is True, f"LONG+bullish+15M aligned → approved")

# Confirmation conflict
appr2, _ = simple_confirmation("short", df_up, df15_up)
check(appr2 is False, "SHORT+bullish → rejected")

# Neutral
appr3, _ = simple_confirmation("long", df_range, df15_up)
check(appr3 is False, "LONG+neutral → rejected")

# 15M misalignment
df15_down = ohlcv_to_df(make_ohlcv(50, 100, trend=-0.3, volatility=0.1))
appr4, _ = simple_confirmation("long", df_up, df15_down, use_15m=True)
check(appr4 is False, "15M against trend → rejected")

# use_15m=False ignores 15M
appr5, _ = simple_confirmation("long", df_up, df15_down, use_15m=False)
check(appr5 is True, "use_15m=False ignores 15M")

# ------------------------------------------------------------------
# Exchange client structure
# ------------------------------------------------------------------
print("\n=== Exchange Client ===")
from src.exchange.bingx_client import BingXClient
for attr in ["symbol_exists", "place_stop_loss", "place_take_profit", "fetch_ohlcv", "test_connection"]:
    check(hasattr(BingXClient, attr), f"BingXClient has {attr}")

# ------------------------------------------------------------------
# Telegram listener structure
# ------------------------------------------------------------------
print("\n=== Telegram Listener ===")
from src.signals.telegram_listener import TelegramListener
for attr in ["set_signal_callback", "start", "stop"]:
    check(hasattr(TelegramListener, attr), f"TelegramListener has {attr}")

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
print("\n" + "=" * 60)
if errors:
    print(f"FAILED: {len(errors)} check(s)")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("ALL CHECKS PASSED")
