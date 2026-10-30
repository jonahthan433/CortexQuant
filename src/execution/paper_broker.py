from uuid import uuid5, NAMESPACE_URL
from config.settings import settings
from src.ledger import PortfolioLedger
from src.models import OrderRequest, TradeIntent, TradeDecision
from src.execution.execution_interface import ExecutionInterface, OrderResult, PositionData
from typing import List, Dict, Optional
import asyncio


class PaperBroker(ExecutionInterface):
    """Deterministic local fills persisted to the same ledger contract as live."""
    def __init__(self, ledger: PortfolioLedger, fee_rate=0.0005):
        self.ledger = ledger
        self.fee_rate = fee_rate

    def validate_order(self, intent: TradeIntent, decision: TradeDecision) -> tuple[bool, str]:
        """Validate order before submission."""
        if decision.quantity <= 0:
            return False, "invalid quantity"
        if decision.entry <= 0:
            return False, "invalid entry price"
        if decision.stop is None:
            return False, "missing stop price"
        if decision.target is None:
            return False, "missing target price"
        # Check for duplicate position
        existing = self.ledger.positions()
        if any(p['symbol'] == intent.symbol for p in existing):
            return False, "duplicate_position"
        return True, "valid"

    def submit_entry(self, intent, quantity, price, stop, target):
        """Legacy sync interface for backward compatibility with tests."""
        import asyncio
        from src.models import TradeDecision
        decision = TradeDecision(
            intent=intent, approved=True, reason='legacy',
            entry=price, stop=stop, target=target, quantity=quantity
        )
        order_result = asyncio.run(self.submit_entry_async(intent, decision))
        return {'status': order_result.status, 'client_order_id': order_result.client_order_id,
                'fee': order_result.fee, 'price': order_result.price, 'quantity': order_result.quantity}

    async def submit_entry_async(self, intent: TradeIntent, decision: TradeDecision) -> OrderResult:
        """Submit entry order with protective stops/targets."""
        cid = self.get_client_order_id("entry", intent)
        valid, reason = self.validate_order(intent, decision)
        if not valid:
            # Use reason as status for specific error types like duplicate_position
            return OrderResult(status=reason, client_order_id=cid, error=reason)

        fee = decision.quantity * decision.entry * self.fee_rate
        close_side = 'sell' if intent.side.value == 'long' else 'buy'
        stop_id = self.get_client_order_id("stop_loss", intent)
        target_id = self.get_client_order_id("take_profit", intent)

        if hasattr(self.ledger, 'record_paper_entry_bundle'):
            inserted = self.ledger.record_paper_entry_bundle(
                cid, intent.symbol, intent.side.value, decision.quantity,
                decision.entry, decision.stop, decision.target, fee,
                intent.source_id, stop_id, target_id
            )
            if not inserted:
                return OrderResult(status='duplicate_position', client_order_id=cid)
        else:
            self.ledger.record_order(cid, intent.symbol, intent.side.value, 'market',
                                   decision.quantity, decision.entry, 'filled', False,
                                   {'paper': True, 'source_id': intent.source_id})
            self.ledger.record_fill(cid, intent.symbol, 'buy' if intent.side.value == 'long' else 'sell',
                                  decision.quantity, decision.entry, fee)
            self.ledger.upsert_position(intent.symbol, intent.side.value,
                                      decision.quantity, decision.entry, decision.stop, decision.target)
            for kind, level, pid in [('stop_loss', decision.stop, stop_id), ('take_profit', decision.target, target_id)]:
                self.ledger.record_order(pid, intent.symbol, close_side, kind,
                                       decision.quantity, level, 'open', True, {'paper': True})
            self.ledger.event('paper_entry_filled', {
                'client_order_id': cid, 'symbol': intent.symbol, 'side': intent.side.value,
                'quantity': decision.quantity, 'price': decision.entry, 'stop': decision.stop,
                'target': decision.target, 'fee': fee
            })
        return OrderResult(
            status='filled', client_order_id=cid, fee=fee,
            price=decision.entry, quantity=decision.quantity
        )

    async def submit_protective_orders(
        self,
        symbol: str,
        position_side: str,
        quantity: float,
        stop_price: float,
        target_price: float
    ) -> tuple[bool, Optional[str]]:
        """Submit stop-loss and take-profit orders (already done in submit_entry for paper)."""
        # For paper broker, protective orders are created in submit_entry via record_paper_entry_bundle
        return True, None

    async def flatten_position(self, symbol: str, side: str, quantity: float) -> OrderResult:
        """Close a position completely (market order, reduce-only)."""
        # For paper, we need a price - use entry price as fallback
        pos = next((p for p in self.ledger.positions() if p['symbol'] == symbol), None)
        if not pos:
            return OrderResult(status='rejected', client_order_id='', error='position not found')

        price = float(pos['entry_price'])  # fallback
        cid = self.get_client_order_id("flatten", TradeIntent(
            source_id="kill_switch", symbol=symbol, side=side,
            entry=price, stop=price, targets=[price], timestamp=None, rank=None, raw_text=""
        ))

        success = self.close_position(symbol, price, 'kill_switch')
        if success:
            return OrderResult(status='filled', client_order_id=cid, price=price, quantity=quantity)
        return OrderResult(status='rejected', client_order_id=cid, error='close failed')

    async def cancel_protective_orders(self, symbol: str) -> bool:
        """Cancel all open protective orders for a symbol."""
        self.ledger.close_protection_orders(symbol)
        return True

    async def fetch_positions(self) -> List[PositionData]:
        """Fetch all open positions from paper broker."""
        positions = self.ledger.positions()
        return [
            PositionData(
                symbol=p['symbol'],
                side=p['side'],
                quantity=float(p['quantity']),
                entry_price=float(p['entry_price']),
                stop_price=float(p['stop_price']) if p['stop_price'] else None,
                target_price=float(p['target_price']) if p['target_price'] else None,
            )
            for p in positions
        ]

    async def fetch_balance(self) -> Dict[str, float]:
        """Fetch paper account balance."""
        equity = float(self.ledger.get_state('paper_equity', 10000))
        return {'free': equity, 'total': equity, 'USDT': {'free': equity, 'total': equity}}

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage (no-op for paper broker)."""
        return True

    def health_check(self) -> bool:
        """Check if paper broker is healthy."""
        try:
            self.ledger.health()
            return True
        except Exception:
            return False

    def get_client_order_id(self, prefix: str, intent: TradeIntent, suffix: str = "") -> str:
        """Generate deterministic client order ID for idempotency."""
        base = f"{intent.source_id}:{intent.symbol}:{prefix}"
        if suffix:
            base += f":{suffix}"
        return str(uuid5(NAMESPACE_URL, base))

    # Legacy methods for backward compatibility
    def submit_entry_legacy(self, intent, quantity, price, stop, target):
        """Legacy sync interface."""
        return asyncio.run(self.submit_entry(intent, TradeDecision(
            intent=intent, approved=True, reason='legacy', entry=price, stop=stop, target=target, quantity=quantity
        )))

    def close_position(self, symbol, price, reason='kill_switch'):
        pos = next((p for p in self.ledger.positions() if p['symbol'] == symbol), None)
        if not pos: return False
        qty = float(pos['quantity']); entry = float(pos['entry_price']); side = pos['side']
        close_side = 'sell' if side == 'long' else 'buy'
        pnl = (price - entry) * qty * (1 if side == 'long' else -1)
        fee = price * qty * self.fee_rate
        cid = str(uuid5(NAMESPACE_URL, f"close:{symbol}:{pos['updated_at']}:{reason}"))
        self.ledger.record_order(cid, symbol, close_side, 'market', qty, price, 'filled', True, {'paper': True, 'reason': reason})
        self.ledger.record_fill(cid, symbol, close_side, qty, price, fee, pnl)
        self.ledger.remove_position(symbol)
        self.ledger.close_protection_orders(symbol)
        self.ledger.event('paper_position_closed', {'symbol': symbol, 'reason': reason, 'pnl': pnl, 'fee': fee})
        return True

    def on_market_bar(self, symbol, high, low):
        pos = next((p for p in self.ledger.positions() if p['symbol'] == symbol), None)
        if not pos:
            return False
        stop = float(pos['stop_price'])
        target = float(pos['target_price'])
        side = pos['side']

        if side == 'long':
            stop_hit = low <= stop
            target_hit = high >= target
        else:
            stop_hit = high >= stop
            target_hit = low <= target

        if stop_hit and target_hit:
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
        mark_prices = mark_prices or {}
        for p in self.ledger.positions():
            self.close_position(p['symbol'], float(mark_prices.get(p['symbol'], p['entry_price'])), 'kill_switch')
