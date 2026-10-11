from config.settings import settings
from src.ledger import PortfolioLedger
from src.models import TradeDecision, TradeIntent

class RiskEngine:
    def __init__(self, ledger): self.ledger=ledger
    def check_daily_loss(self):
        pnl,baseline=self.ledger.day_pnl()
        return baseline>0 and max(0.0,-pnl)/baseline>=settings.MAX_DAILY_LOSS
    def decide(self, intent: TradeIntent, price: float, equity: float, kronos=None, correlations=None) -> TradeDecision:
        reason='approved'; ok=True; stop=intent.stop; target=intent.targets[0] if intent.targets else None
        positions=self.ledger.positions()
        if self.ledger.kill_switch(): ok=False; reason='persistent kill switch enabled'
        elif self.check_daily_loss():
            ok=False; reason='daily loss limit reached'
        elif len(positions)>=settings.MAX_CONCURRENT_POSITIONS: ok=False; reason='max concurrent positions reached'
        elif any(p['symbol']==intent.symbol for p in positions): ok=False; reason='symbol already open'
        if correlations and any(abs(value)>=settings.MAX_CORRELATION and side==intent.side.value for value,side in correlations): ok=False; reason='correlation exposure limit reached'
        if price<=0: ok=False; reason='invalid market price'
        if stop is None:
            volatility=float((kronos or {}).get('volatility_estimate') or 0.0)
            # Clamp the stop distance to a fraction of price: an unclamped
            # stop_pct > 1 drives the stop negative on very cheap tokens
            # (UBIKUSDT entry 0.00701 -> stop -0.00059), which is unplaceable.
            stop_pct=min(0.5,max(0.008,2.0*volatility*(12**0.5)))
            stop=price*(1-stop_pct if intent.side.value=='long' else 1+stop_pct)
        if (intent.side.value=='long' and not stop<price) or (intent.side.value=='short' and not stop>price): ok=False; reason='stop is on wrong side of entry'
        if target is None: target=price+(price-stop)*1.5 if intent.side.value=='long' else price-(stop-price)*1.5
        if (intent.side.value=='long' and target<=price) or (intent.side.value=='short' and target>=price): ok=False; reason='target is on wrong side of entry'
        max_notional=min(settings.MAX_POSITION_NOTIONAL, max(0,equity*settings.RISK_PER_TRADE/abs(price-stop)*price))
        aggregate=sum(float(p['quantity'])*float(p['entry_price']) for p in positions)
        qty=min(max_notional/price, max(0,settings.MAX_AGGREGATE_NOTIONAL-aggregate)/price)
        if qty<=0: ok=False; reason='notional cap exhausted'
        return TradeDecision(intent=intent,approved=ok,reason=reason,entry=price,stop=stop,target=target,quantity=qty if ok else 0,kronos=kronos or {})
