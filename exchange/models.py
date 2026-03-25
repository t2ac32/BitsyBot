from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Order:
    order_id: str
    book: str
    side: str          # 'buy' | 'sell'
    order_type: str    # 'limit' | 'market'
    price: float
    amount: float
    filled: float = 0.0
    status: str = "open"  # open | filled | cancelled | partial
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Trade:
    trade_id: str
    book: str
    side: str
    price: float
    amount: float
    fees: float
    timestamp: datetime


@dataclass
class Balance:
    currency: str
    available: float
    locked: float

    @property
    def total(self) -> float:
        return self.available + self.locked


@dataclass
class Ticker:
    book: str
    last: float
    bid: float
    ask: float
    volume: float
    timestamp: datetime
