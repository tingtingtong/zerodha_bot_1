from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np


class Signal(Enum):
    LONG = "long"
    NO_TRADE = "no_trade"


@dataclass
class TradeSetup:
    signal: Signal
    symbol: str
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    breakeven_trigger: float
    trailing_step: float
    risk_amount: float
    reward_risk_ratio: float
    setup_quality: str
    reason: str
    max_hold_candles: int
    strategy_name: str = ""
    is_valid: bool = True
    rejection_reason: Optional[str] = None

    @property
    def risk_per_share(self) -> float:
        return max(self.entry_price - self.stop_loss, 0.0)

    @property
    def reward_per_share_t1(self) -> float:
        return max(self.target_1 - self.entry_price, 0.0)


class BaseStrategy(ABC):

    @abstractmethod
    def generate_signal(
        self, symbol: str, df_primary: pd.DataFrame,
        df_daily: pd.DataFrame, regime_bullish: bool,
        capital_per_trade: float, charges_estimate: float,
    ) -> TradeSetup:
        pass

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        pass

    @property
    @abstractmethod
    def required_timeframe(self) -> str:
        pass

    def _no_trade(self, symbol: str, reason: str) -> TradeSetup:
        return TradeSetup(
            signal=Signal.NO_TRADE, symbol=symbol,
            entry_price=0, stop_loss=0, target_1=0, target_2=0,
            breakeven_trigger=0, trailing_step=0, risk_amount=0,
            reward_risk_ratio=0, setup_quality="", reason=reason,
            max_hold_candles=0, strategy_name=self.strategy_name,
            is_valid=False, rejection_reason=reason,
        )

    def _ema(self, data, period):
        a = 2 / (period + 1)
        e = np.zeros(len(data))
        e[0] = data[0]
        for i in range(1, len(data)):
            e[i] = a * data[i] + (1 - a) * e[i - 1]
        return e

    def _atr(self, h, l, c, n=14):
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        return float(np.mean(tr[-n:])) if len(tr) >= n else float(np.mean(tr)) if len(tr) else 0.5

    def _rsi(self, c, n=14):
        if len(c) < n + 1:
            return 50.0
        d = np.diff(c)
        ag = float(np.mean(np.where(d > 0, d, 0.0)[-n:]))
        al = float(np.mean(np.where(d < 0, -d, 0.0)[-n:]))
        return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))
