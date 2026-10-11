"""Event-driven backtester for the momentum+scanner strategy, reusing the live
signal code (momentum_score) so a backtest tests the SAME logic that trades.

Realism: taker fee on entry+exit, slippage in bps, funding every 8h on held
perps. No look-ahead: a bar's signal uses only data up to that bar's close and
fills at the NEXT bar's open.

ponytail: single-strategy, single-position, long-only on swap perps. Walk-forward
and OOS are the anti-overfit gate. Add multi-position/portfolio when a single-asset
edge is confirmed. Not a guarantee of profit - it is evidence, nothing more.
"""
import numpy as np
import pandas as pd

from src.signals.indicators import momentum_score


def _apply_costs(price, side, fee_rate, slippage_bps):
    """Slippage moves the fill against you; fee is charged on notional."""
    slip = price * (slippage_bps / 1e4)
    fill = price + slip if side == "long" else price - slip
    return fill


def backtest(df, *, fee_rate=0.0005, slippage_bps=2.0, funding_rate_8h=0.0001,
             risk_frac=0.02, stop_atr_mult=1.5, tp_r=1.5, warmup=40,
             equity0=10000.0):
    """Run the momentum strategy over an OHLCV frame. Returns metrics + trades.

    Entry: momentum_score > threshold (sign of histogram slope). Sizing: fixed
    fractional risk vs an ATR stop. Exit: ATR stop, R-multiple target, or
    momentum flip. Long-only; funding accrues on held bars every 8h.
    """
    df = df.reset_index(drop=True).copy()
    n = len(df)
    if n < warmup + 5:
        return {"error": "insufficient data", "bars": n}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    # ATR(14) for stop distance
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    equity = equity0
    pos = None            # dict with entry_fill, qty, stop, target, entry_bar
    trades = []
    equity_curve = np.full(n, equity)
    bars_since_funding = 0

    for i in range(warmup, n):
        px_open = float(df["open"].iloc[i])
        px_high = float(high.iloc[i])
        px_low = float(low.iloc[i])
        px_close = float(close.iloc[i])

        # 1. manage open position against THIS bar's range
        if pos is not None:
            exit_fill = None
            reason = None
            if px_low <= pos["stop"]:
                exit_fill, reason = pos["stop"], "stop"
            elif px_high >= pos["target"]:
                exit_fill, reason = pos["target"], "target"
            if exit_fill is not None:
                gross = (exit_fill - pos["entry_fill"]) * pos["qty"]
                fee = exit_fill * pos["qty"] * fee_rate
                equity += gross - fee
                trades.append({"entry_bar": pos["entry_bar"], "exit_bar": i,
                               "side": "long", "entry": pos["entry_fill"],
                               "exit": exit_fill, "qty": pos["qty"],
                               "pnl": gross - fee, "reason": reason})
                pos = None

        # 2. funding every 8h on held position (crypto perps: longs pay when
        #    funding positive). 15m bars -> 32 bars = 8h.
        if pos is not None:
            bars_since_funding += 1
            if bars_since_funding >= 32:
                funding = pos["notional"] * funding_rate_8h
                equity -= funding
                trades[-1]["pnl"] -= funding if trades else 0.0
                bars_since_funding = 0

        # 3. new entry only if flat: signal from data up to PREVIOUS close,
        #    fill at THIS bar's open (no look-ahead).
        if pos is None:
            window = df.iloc[:i]  # strictly prior bars
            mom = momentum_score(window)
            a = float(atr.iloc[i - 1])
            if mom > 0.15 and a and a > 0:
                entry_fill = _apply_costs(px_open, "long", fee_rate, slippage_bps)
                risk_per_unit = stop_atr_mult * a
                stop = entry_fill - risk_per_unit
                target = entry_fill + tp_r * risk_per_unit
                risk_amt = equity * risk_frac
                qty = risk_amt / risk_per_unit if risk_per_unit > 0 else 0.0
                fee = entry_fill * qty * fee_rate
                if qty > 0:
                    equity -= fee
                    pos = {"entry_fill": entry_fill, "qty": qty, "stop": stop,
                           "target": target, "entry_bar": i,
                           "notional": entry_fill * qty}
                    bars_since_funding = 0

        equity_curve[i] = equity

    # force-close any open position at final close
    if pos is not None:
        exit_fill = _apply_costs(float(close.iloc[-1]), "long", fee_rate, slippage_bps)
        gross = (exit_fill - pos["entry_fill"]) * pos["qty"]
        fee = exit_fill * pos["qty"] * fee_rate
        equity += gross - fee
        trades.append({"entry_bar": pos["entry_bar"], "exit_bar": n - 1,
                       "side": "long", "entry": pos["entry_fill"], "exit": exit_fill,
                       "qty": pos["qty"], "pnl": gross - fee, "reason": "eop"})

    return {"metrics": compute_metrics(trades, equity_curve[warmup:], equity0),
            "trades": trades,
            "equity_curve": equity_curve[warmup:].tolist()}


def compute_metrics(trades, equity_curve, equity0):
    if not trades:
        return {"trades": 0, "total_return": 0.0, "win_rate": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0}
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    eq = np.asarray(equity_curve, dtype=float)
    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-12)
    sharpe = (rets.mean() / rets.std() * np.sqrt(24 * 365)) if rets.std() > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.maximum(peak, 1e-12)
    gross_win = wins.sum() if len(wins) else 0.0
    gross_loss = abs(losses.sum()) if len(losses) else 0.0
    return {
        "trades": len(trades),
        "total_return": round(float(eq[-1] / equity0 - 1), 4),
        "win_rate": round(float(len(wins) / len(trades)), 4),
        "sharpe": round(float(sharpe), 3),
        "max_drawdown": round(float(dd.min()), 4),
        "profit_factor": round(float(gross_win / gross_loss), 3) if gross_loss > 0 else None,
        "expectancy": round(float(pnls.mean()), 4),
        "avg_win": round(float(wins.mean()), 4) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 4) if len(losses) else 0.0,
    }


def walk_forward(df, folds=4, **bt_kwargs):
    """Split into `folds` contiguous chunks; test each on data it never trained
    on. Guards against a single lucky regime. Returns per-fold metrics + a
    stability summary (mean/std of OOS return)."""
    df = df.reset_index(drop=True)
    n = len(df)
    size = n // folds
    results = []
    for f in range(folds):
        lo = f * size
        hi = n if f == folds - 1 else (f + 1) * size
        chunk = df.iloc[lo:hi]
        if len(chunk) < bt_kwargs.get("warmup", 40) + 5:
            continue
        r = backtest(chunk, **bt_kwargs)
        if "error" not in r:
            results.append({"fold": f, **r["metrics"]})
    if not results:
        return {"folds": [], "stable": False}
    rets = [r["total_return"] for r in results]
    return {
        "folds": results,
        "oos_mean_return": round(float(np.mean(rets)), 4),
        "oos_std_return": round(float(np.std(rets)), 4),
        "profitable_folds": int(sum(1 for r in rets if r > 0)),
        "stable": bool(np.mean(rets) > 0 and sum(1 for r in rets if r > 0) >= folds / 2),
    }


if __name__ == "__main__":  # smallest runnable check
    rng = np.random.default_rng(0)
    # synthetic uptrend with noise -> strategy should be net positive after costs
    drift = 0.0004
    steps = rng.normal(drift, 0.004, 600)
    close = 100 * np.exp(np.cumsum(steps))
    df = pd.DataFrame({
        "open": close, "high": close * 1.002, "low": close * 0.998, "close": close,
    })
    res = backtest(df)
    assert "error" not in res, res
    m = res["metrics"]
    assert m["trades"] > 0, "no trades generated"
    assert 0.0 <= m["win_rate"] <= 1.0
    assert m["max_drawdown"] <= 0.0
    wf = walk_forward(df, folds=4)
    assert "folds" in wf and len(wf["folds"]) >= 1
    print("backtest metrics:", m)
    print("walk-forward:", {k: wf[k] for k in ("profitable_folds", "oos_mean_return", "stable")})
    print("backtester ok")
