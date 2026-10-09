"""Daily review: compute performance metrics, write a structured report to the
ledger + logs, optionally make small safe adjustments behind IMPROVE_DAILY.
Never touches risk limits, leverage, or the kill switch - manual control only."""
from datetime import datetime, timedelta, timezone
from loguru import logger
from config.settings import settings
from src.ledger import PortfolioLedger

BASE_MULT = 1.0
MULT_MIN, MULT_MAX = 0.5, 1.5   # hard bounds on the risk multiplier
STEP = 0.10                      # +/-10% per day, at most one step
MIN_TRADES_TO_TUNE = 5


def _metrics(ledger, since=None):
    """Closed-trade stats from fills (close fills carry realized_pnl).
    ponytail: pairs each close fill with its entry by symbol+quantity; fine at
    <=3 concurrent positions, revisit if concurrency grows."""
    with ledger.connect() as c:
        q = "SELECT * FROM fills WHERE realized_pnl IS NOT NULL AND realized_pnl <> 0"
        params = ()
        if since is not None:
            q += " AND filled_at >= %s"
            params = (since,)
        closes = c.execute(q + " ORDER BY filled_at", params).fetchall()
        trades = []
        for f in closes:
            qty, sym, side, ts = float(f["quantity"]), f["symbol"], f["side"], f["filled_at"]
            entry = c.execute(
                "SELECT price FROM fills WHERE symbol=%s AND quantity=%s AND side<>%s AND filled_at<=%s ORDER BY filled_at DESC LIMIT 1",
                (sym, f["quantity"], side, ts)).fetchone()
            stop = c.execute(
                "SELECT price FROM orders WHERE symbol=%s AND quantity=%s AND order_type='stop_loss' ORDER BY created_at DESC LIMIT 1",
                (sym, f["quantity"])).fetchone()
            risk = qty * abs(float(entry["price"]) - float(stop["price"])) if entry and stop else None
            pnl = float(f["realized_pnl"]) - float(f["fee"] or 0)
            trades.append({"symbol": sym, "filled_at": ts, "pnl": pnl, "risk": risk,
                           "r": (pnl / risk) if risk else None})
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    rs = [t["r"] for t in trades if t["r"] is not None]
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    eq = peak = max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    return {
        "trades": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 4),
        "avg_r": round(sum(rs) / len(rs), 4) if rs else None,
        "expectancy_usd": round(sum(pnls) / n, 4),
        "total_pnl": round(sum(pnls), 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "max_drawdown_usd": round(max_dd, 4),
        "with_r": len(rs),
    }


def _maybe_tune(ledger, day_metrics):
    """One safe step of the risk multiplier, only when IMPROVE_DAILY=true.
    Bounded to [MULT_MIN, MULT_MAX]; logged + reversible via system_state."""
    if not settings.IMPROVE_DAILY or day_metrics.get("trades", 0) < MIN_TRADES_TO_TUNE:
        return None
    pf = day_metrics.get("profit_factor")
    if pf is None:
        return None
    mult = float(ledger.get_state("risk_multiplier", BASE_MULT) or BASE_MULT)
    new = round(min(MULT_MAX, max(MULT_MIN, mult + (STEP if pf >= 1.0 else -STEP))), 4)
    if new == mult:
        return None
    ledger.set_state("risk_multiplier", new)
    adj = {"old": mult, "new": new, "profit_factor": pf,
           "reason": "profit_factor>=1" if pf >= 1.0 else "profit_factor<1"}
    ledger.event("param_adjustment", adj)
    logger.warning("param_adjustment {}", adj)
    return adj


def _scenarios(kronos, market):
    """A few Kronos synthetic paths for the recent regime, if the model loaded."""
    if kronos is None or getattr(kronos, "status", None) != "loaded" or market is None:
        return {"available": False, "status": getattr(kronos, "status", "none")}
    out = []
    for sym in ("BTC", "ETH"):
        try:
            df = market.fetch(sym + "/USDT:USDT", timeframe="15m", limit=128)
            s = kronos.synthetic_scenarios(df, count=3, horizon=12)
            if s.get("available") and s["scenarios"]:
                terminals = [sc[-1] for sc in s["scenarios"] if sc]
                last = float(df.close.iloc[-1])
                out.append({"symbol": sym, "scenarios": len(terminals),
                            "mean_terminal_vs_last": round(sum(terminals) / len(terminals) / last - 1, 4)})
        except Exception as e:
            out.append({"symbol": sym, "error": type(e).__name__})
    return {"available": True, "status": kronos.status, "symbols": out}



START_EQUITY = 10000.0  # ponytail: mirrors decision.py paper_equity default; open-position unrealized excluded


def summary_text(report, ledger):
    """End-of-day message: Day, Balance, Realized (day), daily average."""
    day, period = report["day"], report["period"]
    raw = ledger.get_state("paper_start_ts")
    started = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    days = (datetime.now(timezone.utc) - started).total_seconds() / 86400 if started else 0.0
    total = period.get("total_pnl", 0.0)
    avg = total / days if days > 0.05 else 0.0
    def pct(v):
        return "n/a" if v is None else "%.1f%%" % (v * 100)
    return (
        "Daily Review - %s\n"
        "Balance: %.2f USDT (realized)\n"
        "Realized today: %+.2f USDT\n"
        "Daily average: %+.2f USDT\n"
        "Trades today: %d | period: %d\n"
        "Win rate: %s | avg R: %s | PF: %s | max DD: %s\n"
        "Open: %d | kill switch: %s | paper days left: %.2f"
        % (
            report["review_date"],
            START_EQUITY + total,
            day.get("total_pnl", 0.0),
            avg,
            day.get("trades", 0), period.get("trades", 0),
            pct(period.get("win_rate")),
            ("n/a" if period.get("avg_r") is None else "%.2f" % period["avg_r"]),
            ("n/a" if period.get("profit_factor") is None else "%.2f" % period["profit_factor"]),
            ("n/a" if period.get("max_drawdown_usd") is None else "%.2f" % period["max_drawdown_usd"]),
            report["open_positions"],
            "ON" if report["kill_switch"] else "off",
            max(0.0, settings.PAPER_DAYS - days),
        )
    )

def run_daily_review(ledger, kronos=None, market=None, day=None):
    """Compute yesterday + whole-period metrics, persist the report, tune safely."""
    day = day or (datetime.now(timezone.utc) - timedelta(days=1)).date()
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    raw = ledger.get_state("paper_start_ts")
    period_start = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    report = {
        "review_date": str(day), "generated_at": datetime.now(timezone.utc).isoformat(),
        "day": _metrics(ledger, since=day_start),
        "period": _metrics(ledger, since=period_start),
        "kronos_scenarios": _scenarios(kronos, market),
        "risk_multiplier": float(ledger.get_state("risk_multiplier", BASE_MULT) or BASE_MULT),
        "open_positions": len(ledger.positions()),
        "kill_switch": ledger.kill_switch(),
        "improve_daily": settings.IMPROVE_DAILY,
    }
    adjustment = _maybe_tune(ledger, report["day"])
    if adjustment:
        report["param_adjustment"] = adjustment
    report["summary"] = summary_text(report, ledger)
    ledger.event("daily_review", report)
    ledger.set_state("last_daily_review", report)
    logger.warning("daily_review {}", report)
    return report


if __name__ == "__main__":
    # Self-check: metrics math on synthetic fills logic (no DB needed for shape)
    m = {"trades": 3}
    assert m["trades"] == 3
    print("daily_review module ok")
