"""Paper-first trading runtime with Postgres durability and an explicit live gate."""
import asyncio, time
from datetime import datetime, timezone
import ccxt
from loguru import logger
from config.settings import settings
from src.ledger import PortfolioLedger
from src.execution.paper_broker import PaperBroker
from src.kronos.forecaster import KronosForecaster
from src.market_data import MarketData
from src.signals.telegram_listener import TelegramListener
from src.decision import DecisionEngine
from src.health import HealthServer
from src.daily_review import run_daily_review
from src.paper_policy import execution_mode
from src.utils.logger import setup_logger

setup_logger()

async def run():
    ledger=PortfolioLedger(); ledger.initialize()
    started=ledger.paper_start(None)
    settings.PAPER_START_TS=started
    elapsed=(datetime.now(timezone.utc)-started.astimezone(timezone.utc)).total_seconds()/86400
    mode=execution_mode(started)
    live_eligible=mode != 'paper'
    logger.warning('execution_mode={} paper_days_elapsed={:.3f} paper_days_remaining={:.3f}', mode.upper(),elapsed,max(0,settings.PAPER_DAYS-elapsed))
    if elapsed>=settings.PAPER_DAYS and not ledger.get_state('paper_period_alert_sent',False):
        logger.warning('Paper trading period finished. Execution now follows EXECUTION_TARGET={}', settings.EXECUTION_TARGET)
    # Public market data only; authenticated order adapter is instantiated only after both live gates pass.
    market_exchange=ccxt.bingx({'enableRateLimit':True,'options':{'defaultType':'swap'}})
    market=market_g=MarketData(market_exchange)
    kronos=kronos_g=KronosForecaster(); await asyncio.to_thread(kronos.load)
    broker=broker_g=PaperBroker(ledger)
    live_exchange=None
    if live_eligible:
        from src.exchange.bingx_client import BingXClient
        live_exchange=BingXClient()
        if mode=='bingx_demo': logger.warning('EXECUTION_TARGET=bingx_demo; BingX VST demo exchange path selected')
        else: logger.critical('EXECUTION_TARGET=live; production BingX adapter selected - REAL MONEY')
    engine=DecisionEngine(ledger,market,kronos,broker,live_exchange)
    import queue as _queue, threading as _threading
    signal_q=_queue.Queue(maxsize=1000)   # ponytail: bounded; drop+log past 1000
    def signal_worker():
        # Single consumer: one intent processed at a time. ccxt/Kronos/sync-ledger
        # block here, never on the event loop -> /health stays responsive.
        # Serialization is what keeps position-limit + duplicate checks atomic.
        while True:
            intents=signal_q.get()
            try: asyncio.run(engine.process(intents))
            except Exception as e: logger.exception('signal_worker_error error={}',type(e).__name__)
            finally: signal_q.task_done()
    _worker=_threading.Thread(target=signal_worker,name='signal-worker',daemon=True); _worker.start()
    def enqueue_signals(intents):
        try: signal_q.put_nowait(intents)
        except _queue.Full: logger.warning('signal_queue_full dropped_intents={}',len(intents))
    listener=TelegramListener(ledger); listener.set_signal_callback(enqueue_signals)
    health=HealthServer(ledger,listener,kronos,broker=broker,on_kill=engine.flatten_all); await health.start()
    ledger.set_state('runtime_mode',{'mode':'paper' if not live_eligible else ('bingx_demo' if settings.BINGX_DEMO else 'live'),'paper_start_ts':started.isoformat(),'paper_days':settings.PAPER_DAYS})
    equity=float(ledger.get_state('paper_equity',10000)); ledger.initialize_day(equity)
    async def listener_supervisor():
        retry=2
        while True:
            try:
                await listener.start()
                retry=2
            except asyncio.CancelledError: raise
            except Exception as e:
                listener.connected=False
                logger.exception('telegram_listener_failed error={} retry_seconds={}',type(e).__name__,retry)
            await asyncio.sleep(retry); retry=min(retry*2,60)
    async def monitor():
        while True:
            await asyncio.sleep(15)
            if not ledger.health(): raise RuntimeError('Postgres ledger unavailable')
            elapsed=(datetime.now(timezone.utc)-started.astimezone(timezone.utc)).total_seconds()/86400
            if elapsed>=settings.PAPER_DAYS and not ledger.get_state('paper_period_alert_sent',False) and listener.client.is_connected():
                msg='Paper trading period finished. Live trading is now possible if LIVE_MODE=true'
                sent=await listener.alert(msg)
                if sent: ledger.set_state('paper_period_alert_sent',True)
            if ledger.kill_switch() and ledger.positions():
                await engine.flatten_all()
            elif live_exchange is None:
                # Simulate paper stop/target fills from fresh one-minute OHLCV.
                for position in ledger.positions():
                    symbol=position['symbol'][:-4]+'/USDT:USDT'
                    # BingX rate-limits the public OHLCV endpoint; one failure is
                    # transient, so retry before giving up on this bar.
                    bar=None
                    for _attempt in range(3):
                        try:
                            bar=market.fetch(symbol,timeframe='1m',limit=2).iloc[-1]
                            break
                        except Exception:
                            time.sleep(1.0)
                    if bar is None:
                        logger.warning('paper_reconciliation_unavailable symbol={} error=retries_exhausted',position['symbol'])
                        continue
                    broker.on_market_bar(position['symbol'],float(bar.high),float(bar.low))
    async def daily_review_scheduler():
        # Runs once per day at REVIEW_HOUR_UTC (default 00:05). Survives restarts:
        # last_daily_review date in Postgres prevents double-run; wall-clock aligns.
        while True:
            now=datetime.now(timezone.utc)
            last=ledger.get_state('last_daily_review',{}) or {}
            target=now.replace(hour=settings.REVIEW_HOUR_UTC,minute=5,second=0,microsecond=0)
            due=(ledger.get_state('daily_review_ran_on')!=str(now.date()) and now>=target)
            if due:
                try:
                    ledger.set_state('daily_review_ran_on',str(now.date()))
                    _rep=await asyncio.to_thread(run_daily_review,ledger,kronos_g,market_g)
                    try: await listener.alert(_rep.get('summary','daily review complete'))
                    except Exception as e: logger.warning('daily_summary_send_failed error={}',type(e).__name__)
                except Exception as e: logger.exception('daily_review_failed error={}',type(e).__name__)
            await asyncio.sleep(60)
    try:
        await asyncio.gather(listener_supervisor(),monitor(),daily_review_scheduler())
    finally:
        for t in (): pass
        await health.stop(); await listener.stop()

if __name__=='__main__':
    try: asyncio.run(run())
    except KeyboardInterrupt: pass
