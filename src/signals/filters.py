"""Simple multi-timeframe filters (4H bias + 15M)
Later we will replace/enhance this with Kronos.
"""

from typing import Optional, Tuple
from loguru import logger
import pandas as pd


def ohlcv_to_df(ohlcv: list) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def get_4h_bias(df_4h: pd.DataFrame) -> str:
    """
    Very simple 4H bias:
    - Close > EMA20 and EMA20 rising → bullish
    - Close < EMA20 and EMA20 falling → bearish
    - Otherwise → neutral
    """
    if len(df_4h) < 25:
        return "neutral"

    df = df_4h.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    ema_delta = last["ema20"] - prev["ema20"]

    # Require meaningful EMA move to call a trend (avoids noise-driven neutral→trend flips)
    price_scale = max(df["close"].mean(), 1.0)
    MIN_EMA_MOVE = max(0.1, price_scale * 0.001)  # 0.1 USDT or 0.1% of price, whichever larger

    if last["close"] > last["ema20"] and ema_delta > MIN_EMA_MOVE:
        return "bullish"
    elif last["close"] < last["ema20"] and ema_delta < -MIN_EMA_MOVE:
        return "bearish"
    return "neutral"


def simple_confirmation(
    side: str,
    df_4h: pd.DataFrame,
    df_15m: pd.DataFrame,
    use_15m: bool = True,
) -> Tuple[bool, str]:
    """
    Returns (approved, reason)

    If use_15m is True (default), additionally requires the 15M trend to
    align with the 4H bias direction. If False, skips the 15M check.
    """
    bias = get_4h_bias(df_4h)

    if side == "long" and bias == "bullish":
        if not use_15m:
            return True, f"4H bias bullish – aligned with LONG (15M skip)"
        # 15M confirmation: 15M close should be above its own EMA20
        if len(df_15m) < 25:
            return True, f"4H bias bullish – aligned with LONG (15M too short)"
        df15 = df_15m.copy()
        df15["ema20"] = df15["close"].ewm(span=20, adjust=False).mean()
        last15 = df15.iloc[-1]
        if last15["close"] > last15["ema20"]:
            return True, f"4H bias bullish + 15M up – aligned with LONG"
        return False, f"4H bullish but 15M down – rejecting LONG"

    if side == "short" and bias == "bearish":
        if not use_15m:
            return True, f"4H bias bearish – aligned with SHORT (15M skip)"
        if len(df_15m) < 25:
            return True, f"4H bias bearish – aligned with SHORT (15M too short)"
        df15 = df_15m.copy()
        df15["ema20"] = df15["close"].ewm(span=20, adjust=False).mean()
        last15 = df15.iloc[-1]
        if last15["close"] < last15["ema20"]:
            return True, f"4H bias bearish + 15M down – aligned with SHORT"
        return False, f"4H bearish but 15M up – rejecting SHORT"

    if bias == "neutral":
        return False, f"4H bias neutral – skipping {side.upper()}"

    return False, f"4H bias {bias} – conflicts with {side.upper()}"
