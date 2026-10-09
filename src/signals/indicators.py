"""Technical confirmation on existing OHLCV. Lazy port of the MACD/Awesome
idea from je-suis-tm/quant-trading, minus fix_yahoo_finance/matplotlib: data
comes from the caller's MarketData frame, output is a scalar in [-1, 1] the
ranker folds into its score. No new dependency, no second signal source.

ponytail: single EMA-histogram momentum term. Add RSI/BB/cointegration only
when a backtest on YOUR fills shows this one alone is the bottleneck.
"""
import numpy as np


def momentum_score(frame, fast=12, slow=26, signal=9):
    """+1 strong up, -1 strong down, 0 flat. Tanh-normalized MACD histogram.

    Robust to short/empty frames (returns 0.0) so the ranker never drops an
    intent just because a symbol has thin history.
    """
    try:
        c = frame["close"].astype(float)
    except Exception:
        return 0.0
    if len(c) < slow + signal:
        return 0.0
    macd = c.ewm(span=fast).mean() - c.ewm(span=slow).mean()
    hist = (macd - macd.ewm(span=signal).mean()).dropna()
    if len(hist) < 3:
        return 0.0
    denom = float(hist.std()) + 1e-12
    return float(np.tanh(hist.iloc[-1] / denom))


if __name__ == "__main__":  # smallest runnable check
    import pandas as pd

    up = pd.DataFrame({"close": np.linspace(100, 200, 80)})   # clean uptrend
    down = pd.DataFrame({"close": np.linspace(200, 100, 80)})  # clean downtrend
    flat = pd.DataFrame({"close": np.full(80, 100.0)})
    assert momentum_score(up) > 0.2, momentum_score(up)
    assert momentum_score(down) < -0.2, momentum_score(down)
    assert abs(momentum_score(flat)) < 0.1, momentum_score(flat)
    # thin history must not raise
    assert momentum_score(pd.DataFrame({"close": [1, 2, 3]})) == 0.0
    print("indicators ok")
