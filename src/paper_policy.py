from datetime import datetime, timezone
from config.settings import settings

def execution_mode(started_at: datetime, *, now: datetime | None = None, live_flag: str | None = None, paper_days: int | None = None, bingx_demo: bool | None = None) -> str:
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    started_at=started_at.astimezone(timezone.utc)
    days=paper_days if paper_days is not None else settings.PAPER_DAYS
    flag=settings.LIVE_MODE if live_flag is None else live_flag
    demo=settings.BINGX_DEMO if bingx_demo is None else bingx_demo
    if (now-started_at).total_seconds() < days*86400 or flag != 'true': return 'paper'
    return 'bingx_demo' if demo else 'live'

def days_remaining(started_at, *, now=None, paper_days=None):
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    days=paper_days if paper_days is not None else settings.PAPER_DAYS
    return max(0.0, days-(now-started_at.astimezone(timezone.utc)).total_seconds()/86400)
