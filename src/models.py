from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field, field_validator

class Side(str, Enum):
    long = "long"
    short = "short"

class TradeIntent(BaseModel):
    symbol: str
    side: Side
    entry: float | None = None
    stop: float | None = None
    targets: list[float] = Field(default_factory=list)
    source_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_text: str
    rank: int | None = None
    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, value):
        v = value.upper().replace("/", "").replace(":", "")
        if not v.endswith("USDT"):
            v += "USDT"
        if len(v) < 6 or not v.isalnum():
            raise ValueError("invalid symbol")
        return v
    @field_validator("entry", "stop")
    @classmethod
    def positive_optional_price(cls, value):
        if value is not None and value <= 0:
            raise ValueError("prices must be positive")
        return value

class TradeDecision(BaseModel):
    intent: TradeIntent
    approved: bool
    reason: str
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    quantity: float = 0
    kronos: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrderRequest(BaseModel):
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    reduce_only: bool = False
