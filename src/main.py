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
        logger.warning('Paper trading period finished. Live trading is now possible if LIVE_MODE=true')
    # Public market data only; authenticated order adapter is instantiated only after both live gates pass.
    market_exchange=ccxt.bingx({'enableRateLimit':True,'options':{'defaultType':'swap'}})
    market=MarketData(market_exchange)
    kronos=KronosForecaster(); await asyncio.to_thread(kronos.load)
    broker=PaperBroker(ledger)
    live_exchange=None
    if live_eligible:
        from src.exchange.bingx_client import BingXClient
        live_exchange=BingXClient()
        if settings.BINGX_DEMO: logger.warning('LIVE_MODE enabled after paper period; BingX DEMO exchange path selected')
        else: logger.critical('LIVE_MODE enabled after paper period; production BingX adapter selected')
    engine=DecisionEngine(ledger,market,kronos,broker,live_exchange)
    listener=TelegramListener(ledger); listener.set_signal_callback(engine.process)
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
                    try:
                        symbol=position['symbol'][:-4]+'/USDT:USDT'
                        bar=market.fetch(symbol,timeframe='1m',limit=2).iloc[-1]
                        broker.on_market_bar(position['symbol'],float(bar.high),float(bar.low))
                    except Exception as e: logger.warning('paper_reconciliation_unavailable symbol={} error={}',position['symbol'],type(e).__name__)
    try:
        await asyncio.gather(listener_supervisor(),monitor())
    finally:
        await health.stop(); await listener.stop()

if __name__=='__main__':
    try: asyncio.run(run())
    except KeyboardInterrupt: pass
