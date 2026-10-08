"""Fixed risk/manager.py — 2% fixed fractional + daily loss + position registry"""
"""Risk Manager — 2% fixed fractional risk per trade.
Adds: daily P&L tracking, position registry, circuit breaker.
"""

from dataclasses import dataclass, field
from typing import Optional
from loguru import logger
from config.settings import settings
import time


@dataclass
class PositionSizeResult:
    symbol: str
    side: str
    risk_amount: float          # USDT to risk
    position_size: float        # in base currency
    leverage: int
    stop_loss_pct: float
    approved: bool
    reason: str = ""
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0


class RiskManager:
    """
    Stateless risk calculator + reference to a shared position registry.
    The registry is managed by main.py — this class only reads it.
    """

    def __init__(self):
        self.risk_per_trade = settings.RISK_PER_TRADE
        self.max_concurrent = settings.MAX_CONCURRENT_POSITIONS
        self.max_daily_loss = settings.MAX_DAILY_LOSS
        self.default_leverage = settings.DEFAULT_LEVERAGE
        self.daily_pnl: float = 0.0       # updated by main.py
        self.session_start_balance: float = 0.0
        self.last_reset_epoch: float = time.time()

    def set_daily_pnl(self, value: float) -> None:
        self.daily_pnl = value

    def add_daily_pnl(self, delta: float) -> None:
        self.daily_pnl += delta

    def reset_daily_pnl(self) -> None:
        self.daily_pnl = 0.0
        self.last_reset_epoch = time.time()
        logger.info("RiskManager: daily P&L reset")

    def set_session_start_balance(self, balance: float) -> None:
        self.session_start_balance = balance

    def calculate_position_size(
        self,
        balance: float,
        entry_price: float,
        stop_loss_price: float,
        side: str,
        symbol: str,
        current_open_positions: int = 0,
        open_positions: Optional[dict] = None,
    ) -> PositionSizeResult:
        """
        Calculate position size based on 2% risk.
        Now requires stop_loss_price (not optional).
        Uses a real position registry for concurrent check.
        """

        # Concurrent check from registry (more reliable than caller arg)
        effective_open = current_open_positions
        if open_positions is not None:
            effective_open = len(open_positions)
        if effective_open >= self.max_concurrent:
            return PositionSizeResult(
                symbol=symbol, side=side, risk_amount=0, position_size=0,
                leverage=self.default_leverage, stop_loss_pct=0,
                approved=False,
                reason=f"Max concurrent ({self.max_concurrent}) reached",
                stop_loss_price=0, take_profit_price=0,
            )

        if balance <= 0:
            return PositionSizeResult(
                symbol=symbol, side=side, risk_amount=0, position_size=0,
                leverage=self.default_leverage, stop_loss_pct=0,
                approved=False,
                reason="Balance is zero or negative",
                stop_loss_price=0, take_profit_price=0,
            )

        if entry_price <= 0:
            return PositionSizeResult(
                symbol=symbol, side=side, risk_amount=0, position_size=0,
                leverage=self.default_leverage, stop_loss_pct=0,
                approved=False,
                reason="Invalid entry price",
                stop_loss_price=0, take_profit_price=0,
            )

        # Risk amount in USDT (2% of balance)
        risk_amount = balance * self.risk_per_trade

        # Distance to stop loss
        sl_distance = abs(entry_price - stop_loss_price)
        if sl_distance <= 0:
            return PositionSizeResult(
                symbol=symbol, side=side, risk_amount=0, position_size=0,
                leverage=self.default_leverage, stop_loss_pct=0,
                approved=False,
                reason="Invalid stop loss distance (SL == entry)",
                stop_loss_price=0, take_profit_price=0,
            )

        stop_loss_pct = sl_distance / entry_price

        # Position size: risk_amount = size × entry × sl_pct
        # → size = risk_amount / (entry × sl_pct)
        position_size = risk_amount / (entry_price * stop_loss_pct)

        # Minimum size guard (avoid sub-marketable orders)
        min_size = 1 / entry_price  # smallest position worth ~$1
        if position_size < min_size:
            return PositionSizeResult(
                symbol=symbol, side=side, risk_amount=0, position_size=0,
                leverage=self.default_leverage, stop_loss_pct=0,
                approved=False,
                reason=f"Position size ({position_size:.6f}) below minimum ({min_size:.6f})",
                stop_loss_price=0, take_profit_price=0,
            )

        # Take profit: 2:1 reward-to-risk
        tp_distance = sl_distance * 2
        if side.lower() == "long":
            tp_price = entry_price + tp_distance
        else:
            tp_price = entry_price - tp_distance

        result = PositionSizeResult(
            symbol=symbol,
            side=side,
            risk_amount=round(risk_amount, 2),
            position_size=round(position_size, 6),
            leverage=self.default_leverage,
            stop_loss_pct=round(stop_loss_pct * 100, 2),
            approved=True,
            reason="OK",
            stop_loss_price=round(stop_loss_price, 2),
            take_profit_price=round(tp_price, 2),
        )

        logger.info(
            f"Risk calc → {side.upper()} {symbol} | "
            f"Risk: {result.risk_amount} USDT ({self.risk_per_trade*100}%) | "
            f"Size: {result.position_size} | SL: {result.stop_loss_pct}% | "
            f"SL px: {result.stop_loss_price} | TP px: {result.take_profit_price}"
        )

        return result

    def check_daily_loss(self) -> bool:
        """Return True if daily loss limit is breached."""
        if self.session_start_balance <= 0:
            return False
        loss_pct = abs(self.daily_pnl) / self.session_start_balance
        if loss_pct >= self.max_daily_loss:
            logger.warning(
                f"Daily loss circuit breaker: {self.daily_pnl:+.2f} USDT "
                f"({loss_pct*100:.1f}%) ≥ {self.max_daily_loss*100}%"
            )
            return True
        return False

    def max_position_size_pct(self, balance: float) -> float:
        """Maximum position size as % of balance (safety cap)."""
        return min(settings.RISK_PER_TRADE * 5, 0.10)  # cap at 10% of balance
