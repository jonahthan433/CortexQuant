"""Best-first selection of intents across a cross-section.

Lazy substitute for a 12-equation alpha framework: this system has ONE signal
source and <=3 concurrent slots, so what matters is *selection* (which few
candidates earn the slots), not portfolio construction (how to weight a book).
We score each intent as signal-strength-per-unit-vol, demean cross-sectionally,
and process best-first.

ponytail: no factor orthogonalization / residual extraction / sum-to-unity
weighting. Add when N > ~10 positions AND >=2 independent signal sources exist.
"""
import numpy as np


def rank_cross_sectional(intents, market, timeframe="15m", limit=64):
    """Return intents ordered best-first by vol-normalized, demeaned strength.

    strength = (1/rank) / realized-vol. Demeaning keeps only relative edge;
    argsort(-s) processes the strongest candidates first so the position-limit
    gate fills slots with the best available alpha instead of arrival order.
    """
    if not intents:
        return intents
    scored = []
    for it in intents:
        try:
            base = str(it.symbol).split("USDT")[0]
            frame = market.fetch(base + "/USDT:USDT", timeframe=timeframe, limit=limit)
            vol = float(frame.close.pct_change().std())
        except Exception:
            vol = 0.0
        if not vol or vol != vol or vol <= 0:
            vol = 1e-9
        strength = 1.0 / (it.rank or 999)  # scanner rank 1 = strongest
        scored.append(strength / vol)
    s = np.asarray(scored, dtype=float)
    s = s - s.mean()  # cross-sectional demean
    order = np.argsort(-s)
    return [intents[i] for i in order]


if __name__ == "__main__":  # smallest runnable check
    import pandas as pd

    class Market:
        def __init__(self, vols):
            self.vols = vols

        def fetch(self, symbol, timeframe, limit):
            v = self.vols.get(symbol, 0.01)
            rng = np.random.default_rng(abs(hash(symbol)) % 2**32)
            closes = 100 * (1 + rng.normal(0, v, limit)).cumprod()
            return pd.DataFrame({"close": closes})

    class Intent:
        def __init__(self, symbol, rank):
            self.symbol, self.rank = symbol, rank

    # comparable vol: rank dominates -> rank 1 first
    mkt = Market({"AAA/USDT:USDT": 0.002, "BBB/USDT:USDT": 0.0021})
    out = rank_cross_sectional([Intent("AAAUSDT", 2), Intent("BBBUSDT", 1)], mkt)
    assert [i.symbol for i in out] == ["BBBUSDT", "AAAUSDT"], out

    # extreme vol gap: risk-adjustment wins (vol normalization is the point)
    mkt2 = Market({"AAA/USDT:USDT": 0.002, "BBB/USDT:USDT": 0.02})
    out2 = rank_cross_sectional([Intent("AAAUSDT", 2), Intent("BBBUSDT", 1)], mkt2)
    assert [i.symbol for i in out2] == ["AAAUSDT", "BBBUSDT"], out2

    # empty input passes through
    assert rank_cross_sectional([], mkt) == []

    # market failure must not drop intents, only reorder
    class Dead:
        def fetch(self, *a, **k):
            raise RuntimeError("no data")

    out3 = rank_cross_sectional([Intent("CCCUSDT", 1), Intent("DDDUSDT", 3)], Dead())
    assert len(out3) == 2
    print("ranker ok")
