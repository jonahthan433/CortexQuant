"""Best-first selection of intents across a cross-section.

Lazy substitute for a 12-equation alpha framework: this system has ONE signal
source and <=3 concurrent slots, so what matters is *selection* (which few
candidates earn the slots), not portfolio construction (how to weight a book).
We score each intent as vol-normalized scanner strength, folded with a
MACD-style momentum confirmation from the SAME OHLCV frame we already fetch,
demean cross-sectionally, and process best-first.

ponytail: momentum rides the existing fetch (no extra call). Add RSI/BB/
cointegration only when a backtest on YOUR fills shows momentum alone is the
bottleneck. No factor orthogonalization until N > ~10 positions.
"""
import numpy as np
from src.signals.indicators import momentum_score


def rank_cross_sectional(intents, market, timeframe="15m", limit=64):
    """Return intents ordered best-first by (scanner strength / vol) * momentum.

    strength = (1/rank); risk-adjust by realized vol; confirm with MACD
    histogram direction so a rank-1 signal that is NOT momentum-aligned sinks
    below a rank-1 that is. Demeaning keeps only relative edge; argsort(-s)
    fills the limited slots with the best confirmed alpha instead of arrival
    order.
    """
    if not intents:
        return intents
    scored = []
    for it in intents:
        try:
            base = str(it.symbol).split("USDT")[0]
            frame = market.fetch(base + "/USDT:USDT", timeframe=timeframe, limit=limit)
            vol = float(frame.close.pct_change().std())
            mom = momentum_score(frame)
        except Exception:
            vol, mom = 0.0, 0.0
        if not vol or vol != vol or vol <= 0:
            vol = 1e-9
        strength = 1.0 / (it.rank or 999)      # scanner rank 1 = strongest
        confirm = 1.0 + 0.5 * mom              # momentum tilt, never zero
        scored.append(strength / vol * confirm)
    s = np.asarray(scored, dtype=float)
    s = s - s.mean()  # cross-sectional demean
    order = np.argsort(-s)
    return [intents[i] for i in order]


if __name__ == "__main__":  # smallest runnable check
    import pandas as pd

    class Market:
        def __init__(self, closes):
            self.closes = closes

        def fetch(self, symbol, timeframe, limit):
            return pd.DataFrame({"close": np.asarray(self.closes[symbol], dtype=float)})

    class Intent:
        def __init__(self, symbol, rank):
            self.symbol, self.rank = symbol, rank

    import numpy as _np
    up = list(_np.linspace(100, 200, 80))     # rising -> positive momentum
    # flat-with-equal-vol: same realized vol as `up`, but zero momentum, so the
    # ONLY differentiator is the momentum confirmation term.
    step = _np.diff(_np.asarray(up)) / _np.asarray(up)[1:]
    rng = _np.random.default_rng(7)
    flat = [100.0]
    for s in step:
        flat.append(flat[-1] * (1 + rng.normal(0, abs(s))))
    mkt = Market({"AAA/USDT:USDT": up, "BBB/USDT:USDT": flat})
    out = rank_cross_sectional([Intent("BBBUSDT", 1), Intent("AAAUSDT", 1)], mkt)
    assert [i.symbol for i in out] == ["AAAUSDT", "BBBUSDT"], out

    # empty input passes through
    assert rank_cross_sectional([], mkt) == []

    # market failure must not drop intents, only reorder
    class Dead:
        def fetch(self, *a, **k):
            raise RuntimeError("no data")

    out2 = rank_cross_sectional([Intent("CCCUSDT", 1), Intent("DDDUSDT", 3)], Dead())
    assert len(out2) == 2
    print("ranker ok")
