from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np


class Signal(Enum):
    LONG = "long"
    SHORT = "short"
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
    def direction(self) -> str:
        return self.signal.value  # "long" | "short" | "no_trade"

    @property
    def risk_per_share(self) -> float:
        if self.signal == Signal.SHORT:
            return max(self.stop_loss - self.entry_price, 0.0)
        return max(self.entry_price - self.stop_loss, 0.0)

    @property
    def reward_per_share_t1(self) -> float:
        if self.signal == Signal.SHORT:
            return max(self.entry_price - self.target_1, 0.0)
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
        gains = np.where(d > 0, d, 0.0)
        losses = np.where(d < 0, -d, 0.0)
        avg_gain = float(np.mean(gains[:n]))
        avg_loss = float(np.mean(losses[:n]))
        for i in range(n, len(gains)):
            avg_gain = (avg_gain * (n - 1) + gains[i]) / n
            avg_loss = (avg_loss * (n - 1) + losses[i]) / n
        rsi = 100 - 100 / (1 + avg_gain / max(avg_loss, 1e-9))
        return rsi

    # ── Volatility-adjusted SL helpers ───────────────────────────────

    # ATR multipliers per volatility regime
    _VOL_SL_MULT = {"low": 1.0, "normal": 1.2, "elevated": 1.5, "extreme": 1.8}

    def _vol_profile(self, symbol: str, df_daily: pd.DataFrame):
        """Return VolatilityProfile from daily data, or None if insufficient data."""
        try:
            from research.volatility_engine import VolatilityEngine
            return VolatilityEngine().profile(symbol, df_daily)
        except Exception:
            return None

    def _dynamic_sl(self, cur: float, anchor: float, atr: float,
                    vol_regime: str = "normal") -> float:
        """Compute SL using volatility-regime-adjusted ATR multiplier.

        anchor   — structural price level (pullback low, swing low, EMA, etc.)
        atr      — 14-period ATR on the intraday frame
        vol_regime — 'low' / 'normal' / 'elevated' / 'extreme'

        Returns the tighter of:
          (a) anchor minus a small ATR buffer  — structure-based SL
          (b) cur minus multiplier * ATR       — volatility-based SL cap
        """
        mult = self._VOL_SL_MULT.get(vol_regime, 1.2)
        structure_sl = anchor - atr * 0.3
        volatility_sl = cur - atr * mult
        sl = max(structure_sl, volatility_sl)
        # Hard cap: SL must always be below current price by at least 0.5x ATR
        # Prevents cases where anchor (pullback_low) is above cur due to fast drops
        return min(sl, cur - atr * 0.5)

    def _dynamic_sl_short(self, cur: float, anchor: float, atr: float,
                           vol_regime: str = "normal") -> float:
        """Like _dynamic_sl but for SHORT trades — SL is ABOVE entry price.

        anchor   — structural level above price (swing high, EMA9, etc.)
        Returns the lower of:
          (a) anchor + small ATR buffer  — structure-based SL
          (b) cur + multiplier * ATR     — volatility cap
        """
        mult = self._VOL_SL_MULT.get(vol_regime, 1.2)
        structure_sl = anchor + atr * 0.3
        volatility_sl = cur + atr * mult
        sl = min(structure_sl, volatility_sl)
        # Hard cap: SL must always be above current price by at least 0.5x ATR
        return max(sl, cur + atr * 0.5)
