from uuid import uuid5, NAMESPACE_URL
from config.settings import settings
from src.ledger import PortfolioLedger
from src.models import OrderRequest

class PaperBroker:
    """Deterministic local fills persisted to the same ledger contract as live."""
    def __init__(self, ledger: PortfolioLedger, fee_rate=0.0005): self.ledger=ledger; self.fee_rate=fee_rate
    def submit_entry(self, intent, quantity, price, stop, target):
        cid=str(uuid5(NAMESPACE_URL,f"{intent.source_id}:{intent.symbol}:entry"))
        OrderRequest(client_order_id=cid,symbol=intent.symbol,side='buy' if intent.side.value=='long' else 'sell',order_type='market',quantity=quantity,price=price)
        existing=self.ledger.positions()
        if any(p['symbol']==intent.symbol for p in existing): return {'status':'duplicate_position','client_order_id':cid}
        fee=quantity*price*self.fee_rate
        close_side='sell' if intent.side.value=='long' else 'buy'
        stop_id=str(uuid5(NAMESPACE_URL,f"{intent.source_id}:{intent.symbol}:stop_loss"))
        target_id=str(uuid5(NAMESPACE_URL,f"{intent.source_id}:{intent.symbol}:take_profit"))
        if hasattr(self.ledger,'record_paper_entry_bundle'):
            inserted=self.ledger.record_paper_entry_bundle(cid,intent.symbol,intent.side.value,quantity,price,stop,target,fee,intent.source_id,stop_id,target_id)
            if not inserted: return {'status':'duplicate_position','client_order_id':cid}
        else:
            self.ledger.record_order(cid,intent.symbol,intent.side.value,'market',quantity,price,'filled',False,{'paper':True,'source_id':intent.source_id})
            self.ledger.record_fill(cid,intent.symbol,'buy' if intent.side.value=='long' else 'sell',quantity,price,fee)
            self.ledger.upsert_position(intent.symbol,intent.side.value,quantity,price,stop,target)
            for kind,level,pid in [('stop_loss',stop,stop_id),('take_profit',target,target_id)]:
                self.ledger.record_order(pid,intent.symbol,close_side,kind,quantity,level,'open',True,{'paper':True})
            self.ledger.event('paper_entry_filled',{'client_order_id':cid,'symbol':intent.symbol,'side':intent.side.value,'quantity':quantity,'price':price,'stop':stop,'target':target,'fee':fee})
        return {'status':'filled','client_order_id':cid,'fee':fee,'price':price,'quantity':quantity}
    def close_position(self, symbol, price, reason='kill_switch'):
        pos=next((p for p in self.ledger.positions() if p['symbol']==symbol),None)
        if not pos: return False
        qty=float(pos['quantity']); entry=float(pos['entry_price']); side=pos['side']
        close_side='sell' if side=='long' else 'buy'
        pnl=(price-entry)*qty*(1 if side=='long' else -1)
        fee=price*qty*self.fee_rate
        cid=str(uuid5(NAMESPACE_URL,f"close:{symbol}:{pos['updated_at']}:{reason}"))
        self.ledger.record_order(cid,symbol,close_side,'market',qty,price,'filled',True,{'paper':True,'reason':reason})
        self.ledger.record_fill(cid,symbol,close_side,qty,price,fee,pnl)
        self.ledger.remove_position(symbol)
        self.ledger.close_protection_orders(symbol)
        self.ledger.event('paper_position_closed',{'symbol':symbol,'reason':reason,'pnl':pnl,'fee':fee})
        return True
    def on_market_bar(self, symbol, high, low):
        pos = next((p for p in self.ledger.positions() if p['symbol'] == symbol), None)
        if not pos:
            return False
        stop = float(pos['stop_price'])
        target = float(pos['target_price'])
        side = pos['side']

        # Determine which level was hit first in this bar.
        # For longs: stop hit if low <= stop, target hit if high >= target
        # For shorts: stop hit if high >= stop, target hit if low <= target
        # If both hit in same bar, the one closer to the open (or prior close)
        # should win. Since we only have high/low, we use distance from entry
        # as a proxy for priority: the level closer to entry is more likely hit first.
        if side == 'long':
            stop_hit = low <= stop
            target_hit = high >= target
        else:
            stop_hit = high >= stop
            target_hit = low <= target

        if stop_hit and target_hit:
            # Both hit in same bar - determine which was closer to entry
            entry = float(pos['entry_price'])
            stop_dist = abs(entry - stop)
            target_dist = abs(target - entry)
            if stop_dist <= target_dist:
                return self.close_position(symbol, stop, 'stop_loss')
            else:
                return self.close_position(symbol, target, 'take_profit')
        elif stop_hit:
            return self.close_position(symbol, stop, 'stop_loss')
        elif target_hit:
            return self.close_position(symbol, target, 'take_profit')
        return False
    def flatten_all(self, mark_prices=None):
        mark_prices=mark_prices or {}
        for p in self.ledger.positions(): self.close_position(p['symbol'],float(mark_prices.get(p['symbol'],p['entry_price'])),'kill_switch')
