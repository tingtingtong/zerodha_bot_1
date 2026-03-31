import logging
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional, List
import pytz
import uuid

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class TradeState(Enum):
    SIGNAL_GENERATED = "signal_generated"
    RISK_APPROVED = "risk_approved"
    ENTRY_ORDERED = "entry_ordered"
    ENTRY_FILLED = "entry_filled"
    SL_PLACED = "sl_placed"
    TARGET_1_HIT = "target_1_hit"
    BREAKEVEN_MOVED = "breakeven_moved"
    TRAILING_ACTIVE = "trailing_active"
    CLOSED_PROFIT = "closed_profit"
    CLOSED_LOSS = "closed_loss"
    CLOSED_TIME = "closed_time"
    CLOSED_EMERGENCY = "closed_emergency"
    ERROR = "error"


VALID_TRANSITIONS = {
    TradeState.SIGNAL_GENERATED: {TradeState.RISK_APPROVED, TradeState.ENTRY_ORDERED, TradeState.ERROR},
    TradeState.RISK_APPROVED: {TradeState.ENTRY_ORDERED, TradeState.ERROR},
    TradeState.ENTRY_ORDERED: {TradeState.ENTRY_FILLED, TradeState.ERROR},
    TradeState.ENTRY_FILLED: {TradeState.SL_PLACED, TradeState.CLOSED_LOSS, TradeState.CLOSED_PROFIT,
                               TradeState.CLOSED_TIME, TradeState.CLOSED_EMERGENCY},
    TradeState.SL_PLACED: {TradeState.TARGET_1_HIT, TradeState.CLOSED_LOSS, TradeState.CLOSED_PROFIT,
                            TradeState.BREAKEVEN_MOVED, TradeState.CLOSED_TIME, TradeState.CLOSED_EMERGENCY,
                            TradeState.TRAILING_ACTIVE},
    TradeState.TARGET_1_HIT: {TradeState.BREAKEVEN_MOVED, TradeState.CLOSED_PROFIT, TradeState.CLOSED_LOSS,
                               TradeState.CLOSED_TIME, TradeState.CLOSED_EMERGENCY, TradeState.TRAILING_ACTIVE},
    TradeState.BREAKEVEN_MOVED: {TradeState.TRAILING_ACTIVE, TradeState.CLOSED_PROFIT, TradeState.CLOSED_LOSS,
                                  TradeState.CLOSED_TIME, TradeState.CLOSED_EMERGENCY},
    TradeState.TRAILING_ACTIVE: {TradeState.CLOSED_PROFIT, TradeState.CLOSED_LOSS,
                                  TradeState.CLOSED_TIME, TradeState.CLOSED_EMERGENCY},
    TradeState.CLOSED_PROFIT: set(),
    TradeState.CLOSED_LOSS: set(),
    TradeState.CLOSED_TIME: set(),
    TradeState.CLOSED_EMERGENCY: set(),
    TradeState.ERROR: set(),
}


@dataclass
class PartialExit:
    timestamp: datetime
    qty: int
    price: float
    reason: str
    pnl: float


@dataclass
class TradeRecord:
    symbol: str
    strategy: str
    setup_quality: str
    entry_price: float
    entry_qty: int
    stop_loss: float
    target_1: float
    target_2: float
    breakeven_trigger: float
    trailing_step: float
    direction: str = "long"   # "long" | "short"
    regime_at_entry: str = ""
    trade_id: str = field(
        default_factory=lambda: (
            f"T{datetime.now(IST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4].upper()}"
        )
    )
    state: TradeState = TradeState.SIGNAL_GENERATED
    entry_order_id: Optional[str] = None
    sl_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_qty: Optional[int] = None
    realized_pnl: Optional[float] = None
    charges: Optional[float] = None
    net_pnl: Optional[float] = None
    max_hold_candles: int = 16
    candles_held: int = 0
    partial_exits: List[PartialExit] = field(default_factory=list)
    state_history: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    remaining_qty: int = 0

    def __post_init__(self):
        self.remaining_qty = self.entry_qty
        self._log_state(self.state)

    def transition(self, new_state: TradeState, **kwargs):
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            logger.warning(f"[{self.symbol}] Invalid transition {self.state.value} -> {new_state.value}, ignoring")
            return f"{self.state.value} → {new_state.value} (BLOCKED)"
        old = self.state
        self.state = new_state
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self._log_state(new_state, old.value)
        return f"{old.value} → {new_state.value}"

    def _log_state(self, state: TradeState, from_state: str = ""):
        prefix = f"→ " if from_state else ""
        self.state_history.append(
            f"{datetime.now(IST).strftime('%H:%M:%S')} {prefix}{state.value}"
        )

    def is_open(self) -> bool:
        return self.state in {
            TradeState.ENTRY_FILLED, TradeState.SL_PLACED,
            TradeState.TARGET_1_HIT, TradeState.BREAKEVEN_MOVED,
            TradeState.TRAILING_ACTIVE,
        }

    def is_closed(self) -> bool:
        return self.state in {
            TradeState.CLOSED_PROFIT, TradeState.CLOSED_LOSS,
            TradeState.CLOSED_TIME, TradeState.CLOSED_EMERGENCY,
        }

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "quality": self.setup_quality,
            "state": self.state.value,
            "entry_price": self.entry_price,
            "entry_qty": self.entry_qty,
            "remaining_qty": self.remaining_qty,
            "stop_loss": self.stop_loss,
            "target_1": self.target_1,
            "target_2": self.target_2,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": self.exit_price,
            "realized_pnl": self.realized_pnl,
            "charges": self.charges,
            "net_pnl": self.net_pnl,
            "max_hold_candles": self.max_hold_candles,
            "candles_held": self.candles_held,
            "regime_at_entry": self.regime_at_entry,
            "direction": self.direction,
        }
