from aiohttp import web
from datetime import datetime, timezone
from config.settings import settings
import hashlib
import hmac

class HealthServer:
    def __init__(self,ledger,listener,kronos,broker=None,on_kill=None):
        self.ledger,self.listener,self.kronos=ledger,listener,kronos
        self.broker=broker
        self.on_kill=on_kill
        self.runner=None
        # Kill switch auth token - derived from settings for zero-config but overrideable
        self._kill_token = getattr(settings, 'HEALTH_KILL_TOKEN', None)

    def _verify_kill_auth(self, request) -> bool:
        """Verify bearer token for kill switch endpoints."""
        if self._kill_token is None:
            # No token configured - deny all kill switch access for safety
            return False
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return False
        provided = auth[7:]  # Strip 'Bearer '
        # Use constant-time comparison
        return hmac.compare_digest(provided, self._kill_token)
    async def health(self,request):
        try: db_ok=self.ledger.health()
        except Exception: db_ok=False
        telegram_ok=bool(self.listener.connected and self.listener.client.is_connected())
        kronos_ok=self.kronos.status in {'loaded','degraded'}
        paperbroker_ok=self.broker is not None and hasattr(self.broker,'flatten_all')
        ready=telegram_ok and db_ok and kronos_ok and paperbroker_ok
        try: stamp=self.ledger.get_state('paper_start_ts',None) if db_ok else None
        except Exception: stamp=None
        started=datetime.fromisoformat(stamp) if isinstance(stamp,str) else None
        elapsed=(datetime.now(timezone.utc)-started.astimezone(timezone.utc)).total_seconds()/86400 if started else 0
        remaining=max(0,settings.PAPER_DAYS-elapsed)
        body={'status':'ok' if ready else 'not_ready','checks':{'telegram_connected':telegram_ok,'ledger_reachable':db_ok,'paperbroker_ready':paperbroker_ok,'kronos_loaded_or_degraded':kronos_ok,'kronos_status':self.kronos.status},'paper':{'start_ts':stamp,'days_required':settings.PAPER_DAYS,'days_elapsed':round(elapsed,3),'days_remaining':round(remaining,3),'live_mode_flag':settings.LIVE_MODE,'live_eligible':elapsed>=settings.PAPER_DAYS and settings.live_opt_in}}
        return web.json_response(body,status=200 if ready else 503)
    async def metrics(self,request):
        try: ready=self.ledger.health() and self.listener.connected and self.kronos.status in {'loaded','degraded'}
        except Exception: ready=False
        text=f"trading_system_up {1 if ready else 0}\n"
        try:
            stamp=self.ledger.get_state('paper_start_ts',None)
            started=datetime.fromisoformat(stamp) if isinstance(stamp,str) else datetime.now(timezone.utc)
            remaining=max(0,settings.PAPER_DAYS-(datetime.now(timezone.utc)-started.astimezone(timezone.utc)).total_seconds()/86400)
            text+=f"trading_system_paper_days_remaining {remaining:.3f}\n"
        except Exception: pass
        return web.Response(text=text,content_type='text/plain')
    async def kill(self,request):
        if not self._verify_kill_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        self.ledger.set_kill_switch(True,'local_health_api')
        if self.on_kill: await self.on_kill()
        elif self.broker: self.broker.flatten_all()
        positions=self.ledger.positions()
        return web.json_response({'enabled':True,'open_positions':len(positions),'flattened':not positions})
    async def clear_kill(self,request):
        if not self._verify_kill_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        self.ledger.set_kill_switch(False,'local_operator_reset'); return web.json_response({'enabled':False})
    async def start(self):
        app=web.Application(); app.router.add_get('/health',self.health); app.router.add_get('/metrics',self.metrics); app.router.add_post('/kill-switch',self.kill); app.router.add_post('/kill-switch/disable',self.clear_kill)
        self.runner=web.AppRunner(app,access_log=None); await self.runner.setup(); await web.TCPSite(self.runner,settings.HEALTH_HOST,settings.HEALTH_PORT).start()
    async def stop(self):
        if self.runner: await self.runner.cleanup()
