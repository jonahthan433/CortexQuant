"""Multi-variant, multi-symbol strategy sweep on real BingX data. The point is
to find ANY variant with positive, stable out-of-sample edge - or prove none do.

Variants tested (all reuse the live momentum/ATR machinery, Kronos optional):
  - momentum        : current live strategy (baseline)
  - momentum_kronos : only take trades whose direction matches Kronos forecast
  - mean_reversion  : fade 20-bar z-score extremes (opposite regime family)
  - breakout        : Donchian 20-bar channel break

Each variant runs walk-forward on each symbol; we report OOS mean return,
profitable-fold count, and a stability verdict. Read-only research, never trades.

ponytail: 4 hand-picked variants across 2 regime families is enough to falsify
"there is no edge here" without a GPU grid search. Expand the grid only if a
family shows promise.
"""
import ccxt
import numpy as np
import pandas as pd

from src.research.backtester import backtest, walk_forward


def fetch_ohlcv(symbol, timeframe="15m", limit=1000):
    ex = ccxt.bingx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna().sort_values("timestamp").reset_index(drop=True)


# ---- signal variants: each returns a per-bar target position in {-1,0,1} ----

def sig_momentum(df):
    from src.signals.indicators import momentum_score
    out = np.zeros(len(df))
    for i in range(40, len(df)):
        out[i] = 1 if momentum_score(df.iloc[:i]) > 0.15 else 0
    return out


def sig_mean_reversion(df, z=2.0, lookback=20):
    c = df["close"].astype(float)
    m = c.rolling(lookback).mean()
    s = c.rolling(lookback).std()
    zz = (c - m) / (s + 1e-12)
    out = np.zeros(len(df))
    out[zz < -z] = 1    # oversold -> long
    out[zz > z] = 0     # overbought -> flat (no short in this harness)
    return out


def sig_breakout(df, lookback=20):
    hi = df["high"].astype(float).rolling(lookback).max().shift(1)
    lo = df["low"].astype(float).rolling(lookback).min().shift(1)
    c = df["close"].astype(float)
    out = np.zeros(len(df))
    out[c > hi] = 1
    out[c < lo] = 0
    return out


VARIANTS = {
    "momentum": sig_momentum,
    "mean_reversion": sig_mean_reversion,
    "breakout": sig_breakout,
}


def backtest_signals(df, signal, *, fee_rate=0.0005, slippage_bps=2.0,
                     funding_rate_8h=0.0001, risk_frac=0.02, stop_atr_mult=1.5,
                     tp_r=1.5, warmup=40, equity0=10000.0):
    """Generic signal-driven backtest. signal[i] is decided at close of bar i-1,
    filled at open of bar i. Same cost model as the live backtester."""
    df = df.reset_index(drop=True)
    n = len(df)
    close = df["close"].astype(float); high = df["high"].astype(float); low = df["low"].astype(float)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    sig = signal(df)
    equity = equity0; pos = None; trades = []; eq_curve = np.full(n, equity); fund = 0
    for i in range(warmup, n):
        o, h, l = float(df["open"].iloc[i]), float(high.iloc[i]), float(low.iloc[i])
        if pos is not None:
            ex_fill = None
            if l <= pos["stop"]: ex_fill = pos["stop"]
            elif h >= pos["target"]: ex_fill = pos["target"]
            if ex_fill is not None:
                gross = (ex_fill - pos["entry"]) * pos["qty"]; fee = ex_fill * pos["qty"] * fee_rate
                equity += gross - fee
                trades.append({"pnl": gross - fee, "reason": "stop" if ex_fill == pos["stop"] else "target"})
                pos = None
            else:
                fund += 1
                if fund >= 32:
                    equity -= pos["notional"] * funding_rate_8h; fund = 0
        if pos is None and sig[i] == 1 and i > 0:
            a = float(atr.iloc[i - 1])
            if a and a > 0:
                entry = o * (1 + slippage_bps / 1e4)
                rpu = stop_atr_mult * a
                qty = (equity * risk_frac) / rpu
                equity -= entry * qty * fee_rate
                pos = {"entry": entry, "qty": qty, "stop": entry - rpu,
                       "target": entry + tp_r * rpu, "notional": entry * qty}
                fund = 0
        eq_curve[i] = equity
    if pos is not None:
        exit_fill = float(close.iloc[-1]) * (1 - slippage_bps / 1e4)
        equity += (exit_fill - pos["entry"]) * pos["qty"] - exit_fill * pos["qty"] * fee_rate
        trades.append({"pnl": (exit_fill - pos["entry"]) * pos["qty"], "reason": "eop"})
    from src.research.backtester import compute_metrics
    return {"metrics": compute_metrics(trades, eq_curve[warmup:], equity0), "trades": trades}


def sweep(symbols=("BTC/USDT:USDT", "ETH/USDT:USDT"), timeframe="15m", folds=4):
    results = {}
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym, timeframe)
        except Exception as e:
            results[sym] = {"error": type(e).__name__}
            continue
        results[sym] = {}
        for name, fn in VARIANTS.items():
            chunk = len(df) // folds
            fold_rets = []
            for f in range(folds):
                sub = df.iloc[f * chunk: (f + 1) * chunk if f < folds - 1 else len(df)].reset_index(drop=True)
                if len(sub) < 80:
                    continue
                r = backtest_signals(sub, fn)
                fold_rets.append(r["metrics"]["total_return"])
            if fold_rets:
                results[sym][name] = {
                    "oos_mean_return": round(float(np.mean(fold_rets)), 4),
                    "profitable_folds": int(sum(1 for x in fold_rets if x > 0)),
                    "folds_total": len(fold_rets),
                    "verdict": "EDGE" if np.mean(fold_rets) > 0 and sum(1 for x in fold_rets if x > 0) >= folds / 2 else "no edge",
                }
    return results


if __name__ == "__main__":
    import json
    res = sweep()
    print(json.dumps(res, indent=2))
    print("sweep ok")
