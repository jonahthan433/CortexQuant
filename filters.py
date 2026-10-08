"""Fixed signals/filters.py — robust multi-timeframe filter with Kronos stub"""
"""Multi-timeframe filters (4H bias + 15M confirmation).
Replaces bare EMA20 with EMA20 slope + ADX trend strength check.
Kronos stub returns neutral until Phase 3.
"""

from typing import Optional, Tuple
from loguru import logger
import pandas as pd
import numpy as np


def ohlcv_to_df(ohlcv: list) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def get_4h_bias(df_4h: pd.DataFrame) -> str:
    """
    Robust 4H trend bias:
    - EMA20 slope direction
    - ADX(14) > 20 confirms trend (not ranging)
    - Close vs EMA20 alignment
    Returns "bullish", "bearish", or "neutral".
    """
    if len(df_4h) < 25:
        return "neutral"

    df = df_4h.copy()
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    # EMA20
    df["ema20"] = close.ewm(span=20, adjust=False).mean()

    # ADX(14) manual computation
    df["tr"] = np.maximum(
        high - low,
        np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))
    )
    df["atr"] = df["tr"].rolling(14).mean()
    df["plus_dm"] = np.where(
        (high - high.shift(1)) > (low.shift(1) - low),
        np.maximum(high - high.shift(1), 0),
        0,
    )
    df["minus_dm"] = np.where(
        (low.shift(1) - low) > (high - high.shift(1)),
        np.maximum(low.shift(1) - low, 0),
        0,
    )
    plus_di = 100 * (df["plus_dm"].rolling(14).mean() / df["atr"])
    minus_di = 100 * (df["minus_dm"].rolling(14).mean() / df["atr"])
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(14).mean()

    last = df.iloc[-1]
    prev_ema = df.iloc[-2]["ema20"]

    ema_slope = last["ema20"] - prev_ema
    trend_strength = adx.iloc[-1]

    # Neutral if ADX < 20 (ranging market)
    if trend_strength < 20:
        return "neutral"

    if last["close"] > last["ema20"] and ema_slope > 0:
        return "bullish"
    elif last["close"] < last["ema20"] and ema_slope < 0:
        return "bearish"

    return "neutral"


def simple_confirmation(
    side: str,
    df_4h: pd.DataFrame,
    df_15m: pd.DataFrame,
    use_15m: bool = True,
) -> Tuple[bool, str]:
    """
    Returns (approved, reason).
    Checks 4H bias + optional 15m momentum confirmation.
    """
    bias = get_4h_bias(df_4h)

    if bias == "neutral":
        return False, "4H bias neutral (ranging) – skipping"

    if side == "long" and bias != "bullish":
        return False, f"4H bias {bias} – conflicts with LONG"
    if side == "short" and bias != "bearish":
        return False, f"4H bias {bias} – conflicts with SHORT"

    # Optional 15m confirmation: price must be on the correct side of 15m EMA20
    if use_15m and len(df_15m) >= 20:
        df15 = df_15m.copy()
        df15["ema20"] = df15["close"].astype(float).ewm(span=20, adjust=False).mean()
        last_15 = df15.iloc[-1]
        if side == "long" and last_15["close"] < last_15["ema20"]:
            return False, "15M price below EMA20 – weak intraday momentum"
        if side == "short" and last_15["close"] > last_15["ema20"]:
            return False, "15M price above EMA20 – weak intraday momentum"

    reason = f"4H bias {bias}"
    if use_15m:
        reason += " + 15M aligned"
    return True, reason


def kronos_forecast(ohlcv_df: pd.DataFrame, horizon: int = 24) -> dict:
    """
    Kronos-mini forecast stub.
    Phase 3: replace with real model load + inference.
    Returns neutral until implemented.
    """
    logger.debug(f"Kronos forecast requested (horizon={horizon}) — STUB")
    return {
        "direction": "neutral",
        "confidence": 0.0,
        "volatility_estimate": None,
        "note": "Kronos not yet loaded – Phase 3",
    }
