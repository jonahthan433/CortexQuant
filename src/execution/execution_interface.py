"""Common execution interface for paper and live trading.

Both paper and live adapters must implement this interface to ensure:
- Order validation
- Position sizing
- Risk checks
- Execution intent
- Idempotency requirements
- Order lifecycle tracking
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from src.models import TradeIntent, TradeDecision


@dataclass
class OrderResult:
    """Result of an order submission."""
    status: str  # 'filled', 'submitted', 'rejected', 'duplicate_position'
    client_order_id: str
    fee: float = 0.0
    price: float = 0.0
    quantity: float = 0.0
    exchange_order_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PositionData:
    """Position information from exchange/broker."""
    symbol: str
    side: str  # 'long' or 'short'
    quantity: float
    entry_price: float
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    unrealized_pnl: float = 0.0


class ExecutionInterface(ABC):
    """Abstract base class for execution adapters (paper/live)."""

    @abstractmethod
    def validate_order(self, intent: TradeIntent, decision: TradeDecision) -> tuple[bool, str]:
        """Validate order before submission.

        Returns:
            (is_valid, reason_if_invalid)
        """
        pass

    @abstractmethod
    async def submit_entry(self, intent: TradeIntent, decision: TradeDecision) -> OrderResult:
        """Submit entry order with protective stops/targets.

        Must be idempotent - same client_order_id should not create duplicate positions.
        """
        pass

    @abstractmethod
    async def submit_protective_orders(
        self,
        symbol: str,
        position_side: str,
        quantity: float,
        stop_price: float,
        target_price: float
    ) -> tuple[bool, Optional[str]]:
        """Submit stop-loss and take-profit orders.

        Returns:
            (success, error_message)
        """
        pass

    @abstractmethod
    async def flatten_position(self, symbol: str, side: str, quantity: float) -> OrderResult:
        """Close a position completely (market order, reduce-only)."""
        pass

    @abstractmethod
    async def cancel_protective_orders(self, symbol: str) -> bool:
        """Cancel all open protective orders for a symbol."""
        pass

    @abstractmethod
    async def fetch_positions(self) -> List[PositionData]:
        """Fetch all open positions from exchange/broker."""
        pass

    @abstractmethod
    async def fetch_balance(self) -> Dict[str, float]:
        """Fetch account balance (free, total, etc.)."""
        pass

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol."""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Check if exchange/broker is healthy and reachable."""
        pass

    @abstractmethod
    def get_client_order_id(self, prefix: str, intent: TradeIntent, suffix: str = "") -> str:
        """Generate deterministic client order ID for idempotency."""
        pass