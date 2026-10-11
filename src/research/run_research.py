"""Pull real OHLCV from BingX public API and run the backtester across market
regimes. No API key needed (public klines). This is the anti-overfit gate: a
strategy that only wins on a synthetic uptrend is worthless.

Regimes are carved by trailing volatility tercile (calm / normal / wild) so we
can see if the edge holds when it matters. Results print + are returned for the
HTML report. NEVER trades - read-only research.
"""
import ccxt
import pandas as pd

from src.research.backtester import backtest, walk_forward


def fetch_ohlcv(symbol="BTC/USDT:USDT", timeframe="15m", limit=1000):
    ex = ccxt.bingx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna().sort_values("timestamp").reset_index(drop=True)


def split_regimes(df, col="close", window=96):
    """Label each bar by trailing-vol tercile: 0 calm, 1 normal, 2 wild."""
    vol = df[col].pct_change().rolling(window).std()
    q1, q2 = vol.quantile(1 / 3), vol.quantile(2 / 3)
    regime = pd.cut(vol, bins=[-1, q1, q2, vol.max() + 1], labels=[0, 1, 2]).astype("float")
    return regime.fillna(1).astype(int)


def run_research(symbol="BTC/USDT:USDT", timeframe="15m", limit=1000, folds=4):
    df = fetch_ohlcv(symbol, timeframe, limit)
    full = backtest(df)
    wf = walk_forward(df, folds=folds)
    regimes = split_regimes(df)
    per_regime = {}
    for r in (0, 1, 2):
        mask = regimes.values == r
        sub = df[mask].reset_index(drop=True)
        if len(sub) > 120:
            per_regime[str(r)] = backtest(sub)["metrics"]
    return {"symbol": symbol, "timeframe": timeframe, "bars": len(df),
            "full_sample": full.get("metrics"),
            "walk_forward": wf, "per_regime": per_regime}


if __name__ == "__main__":
    import json
    for sym in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
        try:
            res = run_research(sym)
            print("=" * 30, sym)
            print(json.dumps({k: res[k] for k in ("bars", "full_sample", "walk_forward")},
                             indent=2, default=str))
            print("per_regime:", json.dumps(res["per_regime"], indent=2, default=str))
        except Exception as e:
            print(sym, "FAILED:", type(e).__name__, e)
    print("research runner ok")
