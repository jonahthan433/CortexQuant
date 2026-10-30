from datetime import datetime, timezone
from loguru import logger
from src.risk.engine import RiskEngine
from src.models import TradeIntent
from src.execution.paper_broker import PaperBroker
from src.execution.protection import protect_or_flatten, PositionFlattenFailure
from config.settings import settings

class DecisionEngine:
    def __init__(self, ledger, market, kronos, broker, live_exchange=None):
        self.ledger,self.market,self.kronos,self.broker,self.live_exchange=ledger,market,kronos,broker,live_exchange
        self.risk=RiskEngine(ledger)
    async def flatten_all(self):
        if self.live_exchange is None:
            self.broker.flatten_all()
            return
        ex = self.live_exchange
        for position in ex.fetch_positions():
            qty = float(position.get('contracts') or 0)
            if qty <= 0:
                continue
            symbol = position.get('symbol')
            side = str(position.get('side') or '').lower()
            close_side = 'sell' if side == 'long' else 'buy'
            # Reduce-only order
            ex.create_market_order(symbol, close_side, qty, params={'reduceOnly': True})

        # Verify positions are actually closed - poll with timeout
        import asyncio
        max_wait = 30  # seconds
        poll_interval = 2
        elapsed = 0
        while elapsed < max_wait:
            await asyncio.sleep(poll_interval)
            remaining = ex.fetch_positions()
            open_positions = [p for p in remaining if float(p.get('contracts', 0)) != 0]
            if not open_positions:
                break
            elapsed += poll_interval

        # Only remove from ledger after verified flat
        remaining = ex.fetch_positions()
        open_positions = [p for p in remaining if float(p.get('contracts', 0)) != 0]
        if open_positions:
            logger.error(f"Kill switch: {len(open_positions)} positions still open after flatten attempt")
            for p in open_positions:
                sym = p.get('symbol')
                self.ledger.event('kill_switch_flatten_failed', {'symbol': sym, 'side': p.get('side'), 'contracts': p.get('contracts')})
        else:
            for position in ex.fetch_positions():
                symbol = position.get('symbol')
                if symbol:
                    self.ledger.remove_position(symbol.replace('/', '').replace(':USDT', ''))

        self.ledger.event('live_kill_switch_flatten_complete', {'remaining_positions': len(open_positions)})

    async def process(self,intents):
        from src.signals.ranker import rank_cross_sectional
        intents=rank_cross_sectional(intents,self.market)  # best-first selection
        for intent in intents:
            logger.info('trade_signal_received source_id={} symbol={} side={}',intent.source_id,intent.symbol,intent.side.value)
            age=(datetime.now(timezone.utc)-intent.timestamp.astimezone(timezone.utc)).total_seconds()
            if age>1800:
                self.ledger.event('decision_rejected',{'source_id':intent.source_id,'reason':'stale_signal'}); logger.warning('trade_decision rejected stale_signal source_id={}',intent.source_id); continue
            if self.risk.check_daily_loss():
                self.ledger.event('decision_rejected',{'source_id':intent.source_id,'reason':'daily_loss_limit_reached'})
                logger.critical('trade_decision rejected daily_loss_limit_reached source_id={}',intent.source_id)
                continue
            ccxt_symbol=intent.symbol[:-4]+'/USDT:USDT'
            try:
                frame=self.market.fetch(ccxt_symbol,limit=min(settings.KRONOS_MAX_CONTEXT,512)); price=float(frame.close.iloc[-1])
            except Exception as e:
                self.ledger.event('decision_rejected',{'source_id':intent.source_id,'reason':'market_data_unavailable','error':type(e).__name__}); logger.error('trade_decision rejected market_data_unavailable'); continue
            kronos=(self.kronos.forecast(frame,12) if len(frame)>=settings.KRONOS_MAX_CONTEXT else None) if self.kronos.status=='loaded' else None
            if kronos is None: kronos={'available':False,'status':self.kronos.status,'model_version':self.kronos.version}
            # Kronos remains optional: invalid/unavailable forecasts are recorded but do not block valid risk rules.
            equity=float(self.ledger.get_state('paper_equity',10000))
            positions=self.ledger.positions()
            correlations=[]
            for position in positions:
                try:
                    other=position['symbol'][:-4]+'/USDT:USDT'
                    correlations.append((self.market.correlation(ccxt_symbol,other),position['side']))
                except Exception as e:
                    logger.warning('correlation_check_unavailable symbol={} error={}',position['symbol'],type(e).__name__)
                    # On transient data issues, default to 0.0 (uncorrelated) rather than 1.0 (perfect correlation).
                    # This avoids overly conservative blocking of valid trades.
                    correlations.append((0.0,position['side']))
            decision=self.risk.decide(intent,price,equity,kronos,correlations)
            if decision.approved and settings.KRONOS_REQUIRE_ALIGNMENT and kronos.get('available') and kronos.get('direction') != intent.side.value:
                decision=decision.model_copy(update={'approved':False,'quantity':0,'reason':'Kronos direction conflicts with signal'})
            _d=decision.model_dump(mode='json'); _d['intent'].pop('raw_text',None)
            self.ledger.event('trade_decision',_d)
            logger.info('trade_decision source_id={} approved={} reason={} entry={} stop={} target={} quantity={} kronos_status={}',intent.source_id,decision.approved,decision.reason,decision.entry,decision.stop,decision.target,decision.quantity,kronos.get('status'))
            if not decision.approved:
                self.ledger.event('decision_rejected',{'source_id':intent.source_id,'reason':decision.reason})
                logger.warning('trade_decision rejected risk_engine source_id={} reason={}',intent.source_id,decision.reason)
                continue
            if self.live_exchange is None:
                order_result = await self.broker.submit_entry_async(intent, decision)
                result = {'status': order_result.status, 'client_order_id': order_result.client_order_id,
                         'fee': order_result.fee, 'price': order_result.price, 'quantity': order_result.quantity}
            else:
                ex = self.live_exchange
                await ex.set_leverage_async(ccxt_symbol, settings.DEFAULT_LEVERAGE)
                order = ex.create_market_order(ccxt_symbol, 'buy' if intent.side.value == 'long' else 'sell', decision.quantity)
                try:
                    protected = protect_or_flatten(ex, symbol=ccxt_symbol, position_side=intent.side.value, amount=decision.quantity, stop_price=decision.stop, target_price=decision.target)
                    if not protected: continue
                except PositionFlattenFailure:
                    self.ledger.set_kill_switch(True, 'protection_flatten_failure'); raise
                cid = ex.get_client_order_id("live", intent)
                self.ledger.record_order(cid, intent.symbol, intent.side.value, 'market', decision.quantity, decision.entry, 'submitted', False, {'exchange_order_id': order.get('id')})
                result = {'status': 'submitted', 'client_order_id': cid}
            self.ledger.event('order_result',{'source_id':intent.source_id,'result':result,'mode':'paper' if self.live_exchange is None else 'live_or_demo'})
            logger.info('order_result source_id={} status={} order_id={}',intent.source_id,result.get('status'),result.get('client_order_id'))
