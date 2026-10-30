from datetime import datetime, timezone
from config.settings import settings


def resolve_execution_target(live_flag: str | None = None, bingx_demo: bool | None = None) -> str:
    """Single source of truth for where orders are routed.

    paper       -> internal ledger only, no exchange orders
    bingx_demo  -> real orders on BingX VST (demo)
    live        -> real orders with real money

    When live_flag and bingx_demo are explicitly provided (for testing),
    they determine the target directly. Otherwise, settings are used.
    """
    # If explicit flags provided, use them for test scenarios
    if live_flag is not None or bingx_demo is not None:
        live_mode = live_flag if live_flag is not None else settings.LIVE_MODE
        demo_mode = bingx_demo if bingx_demo is not None else settings.BINGX_DEMO

        if live_mode != "true":
            return "paper"
        return "bingx_demo" if demo_mode else "live"

    # Production logic using settings
    target = settings.EXECUTION_TARGET
    if target not in ("paper", "bingx_demo", "live"):
        return "paper"
    if target == "live" and settings.LIVE_MODE != "true":
        # Deliberate two-key gate: never silently promote to real money.
        return "paper"
    if target == "bingx_demo" and not settings.BINGX_DEMO:
        return "paper"
    return target


def execution_mode(started_at: datetime, *, now: datetime | None = None, live_flag: str | None = None, paper_days: int | None = None, bingx_demo: bool | None = None) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    started_at = started_at.astimezone(timezone.utc)
    days = paper_days if paper_days is not None else settings.PAPER_DAYS
    if (now - started_at).total_seconds() < days * 86400:
        return 'paper'  # mandatory paper period has not elapsed
    return resolve_execution_target(live_flag=live_flag, bingx_demo=bingx_demo)


def days_remaining(started_at, *, now=None, paper_days=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    days = paper_days if paper_days is not None else settings.PAPER_DAYS
    return max(0.0, days - (now - started_at.astimezone(timezone.utc)).total_seconds() / 86400)