from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
from datetime import datetime


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL-M"
    SL_LIMIT = "SL"


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class ProductType(Enum):
    MIS = "MIS"
    CNC = "CNC"
    NRML = "NRML"


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    COMPLETE = "complete"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    product: ProductType
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    tag: Optional[str] = None
    exchange: str = "NSE"


@dataclass
class OrderResponse:
    order_id: str
    status: OrderStatus
    filled_qty: int
    avg_fill_price: float
    timestamp: datetime
    message: str = ""
    is_simulated: bool = False


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float
    current_price: float
    unrealized_pnl: float
    product: ProductType
    side: str = "long"


class BrokerBase(ABC):

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResponse:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResponse:
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def get_available_margin(self) -> float:
        pass

    @abstractmethod
    def exit_all_positions(self) -> List[OrderResponse]:
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass

    @property
    @abstractmethod
    def broker_name(self) -> str:
        pass
