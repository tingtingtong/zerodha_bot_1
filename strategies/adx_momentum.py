"""
ADX Momentum Strategy

Concept: ADX (Average Directional Index) measures trend STRENGTH, not direction.
When ADX rises above a threshold (e.g., 25), a strong trend is developing.
Combined with +DI/-DI crossover, this identifies the start of powerful trending moves.

Entry conditions:
  Long : ADX > 25 and rising, +DI > -DI, RSI 55-80, price above EMA21.
         This means a strong uptrend is developing.
  Short: ADX > 25 and rising, -DI > +DI, RSI 20-45, price below EMA21.
         This means a strong downtrend is developing.

The key filter: ADX must be RISING (not just above threshold). A high but
falling ADX means the trend is weakening — we want to catch new trends.

SL  : Below recent swing low (long) / above swing high (short) + ATR buffer
T1  : 1.5x risk
T2  : 3.0x risk (trend trades deserve wider targets)
Max hold: 14 candles (3.5 hours) — trend trades need time

Best conditions: strong_bull, weak_bull (for longs), weak_bear, strong_bear (for shorts)
Avoid: sideways regime (ADX < 20 = no trend)

Popularized by: Welles Wilder (creator of ADX, RSI). Used by CTAs and
systematic trend followers globally.
Edge: Only trades when a strong trend EXISTS (not hoped for). ADX filter
reduces whipsaws compared to pure moving average crossover systems.

NSE applicability: Indian stocks frequently show strong ADX readings during
sector rotation moves and result-season trends. Works well on liquid F&O stocks.
"""

import numpy as np
import pandas as pd
import logging
import pytz
from .base_strategy import BaseStrategy, TradeSetup, Signal

IST = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger(__name__)


class ADXMomentumStrategy(BaseStrategy):

    ADX_PERIOD = 14
    ADX_THRESHOLD = 25
    RSI_LONG_MIN = 55
    RSI_LONG_MAX = 80
    RSI_SHORT_MIN = 20
    RSI_SHORT_MAX = 45
    EMA_TREND = 21
    MIN_RR = 1.5
    MAX_HOLD_CANDLES = 14
    T1_MULT = 1.5
    T2_MULT = 3.0       # wider target — trend trades
    NO_TRADE_BEFORE = "09:45"
    NO_TRADE_AFTER = "14:15"
    MIN_VOL_MULT = 0.8

    BLOCKED_REGIMES = {"sideways", "high_volatility"}

    @property
    def strategy_name(self) -> str:
        return "ADXMomentum"

    @property
    def required_timeframe(self) -> str:
        return "15m"

    def _calc_adx(self, h, l, c, period=14):
        """Calculate ADX, +DI, -DI.

        Returns: (adx_array, plus_di_array, minus_di_array)
        All arrays are same length as input. Early values are NaN.
        """
        n = len(c)
        if n < period * 2 + 1:
            nan_arr = np.full(n, np.nan)
            return nan_arr, nan_arr, nan_arr

        # True Range
        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)

        for i in range(1, n):
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
            up_move = h[i] - h[i - 1]
            down_move = l[i - 1] - l[i]

            plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0
            minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0

        # Smoothed TR, +DM, -DM using Wilder's smoothing
        atr_s = np.zeros(n)
        plus_dm_s = np.zeros(n)
        minus_dm_s = np.zeros(n)

        atr_s[period] = np.sum(tr[1:period + 1])
        plus_dm_s[period] = np.sum(plus_dm[1:period + 1])
        minus_dm_s[period] = np.sum(minus_dm[1:period + 1])

        for i in range(period + 1, n):
            atr_s[i] = atr_s[i - 1] - atr_s[i - 1] / period + tr[i]
            plus_dm_s[i] = plus_dm_s[i - 1] - plus_dm_s[i - 1] / period + plus_dm[i]
            minus_dm_s[i] = minus_dm_s[i - 1] - minus_dm_s[i - 1] / period + minus_dm[i]

        # +DI and -DI
        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        dx = np.zeros(n)

        for i in range(period, n):
            if atr_s[i] > 0:
                plus_di[i] = 100 * plus_dm_s[i] / atr_s[i]
                minus_di[i] = 100 * minus_dm_s[i] / atr_s[i]
            di_sum = plus_di[i] + minus_di[i]
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / max(di_sum, 0.01)

        # ADX = smoothed average of DX
        adx = np.zeros(n)
        start_idx = period * 2
        if start_idx < n:
            adx[start_idx] = np.mean(dx[period:start_idx + 1])
            for i in range(start_idx + 1, n):
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

        return adx, plus_di, minus_di

    def generate_signal(self, symbol, df_primary, df_daily,
                        regime_bullish, capital_per_trade, charges_estimate,
                        regime: str = "") -> TradeSetup:

        # Regime gate
        if regime in self.BLOCKED_REGIMES:
            return self._no_trade(symbol, f"regime_blocked_{regime}")

        if df_primary is None or len(df_primary) < 40:
            return self._no_trade(symbol, "insufficient_data")
        if df_daily is None or len(df_daily) < 22:
            return self._no_trade(symbol, "insufficient_daily_data")

        # Time window
        if "timestamp" in df_primary.columns:
            last_ts = pd.to_datetime(df_primary["timestamp"].iloc[-1], utc=True).tz_convert(IST)
            is_live = last_ts.date() == pd.Timestamp.now(tz=IST).date()
            now_str = pd.Timestamp.now(tz=IST).strftime("%H:%M") if is_live else last_ts.strftime("%H:%M")
        else:
            now_str = pd.Timestamp.now(tz=IST).strftime("%H:%M")

        if now_str < self.NO_TRADE_BEFORE or now_str > self.NO_TRADE_AFTER:
            return self._no_trade(symbol, f"outside_window_{now_str}")

        c = df_primary["close"].values
        h = df_primary["high"].values
        l = df_primary["low"].values
        v = df_primary["volume"].values
        cur = c[-1]

        if cur <= 0:
            return self._no_trade(symbol, "invalid_price")

        # Calculate ADX
        adx, plus_di, minus_di = self._calc_adx(h, l, c, self.ADX_PERIOD)

        # ADX must be above threshold
        if np.isnan(adx[-1]) or adx[-1] < self.ADX_THRESHOLD:
            return self._no_trade(symbol, f"adx_low_{adx[-1]:.1f}")

        # ADX must be rising (current > 2 candles ago)
        if len(adx) >= 3 and adx[-1] <= adx[-3]:
            return self._no_trade(symbol, "adx_not_rising")

        # Direction from DI crossover
        long_signal = plus_di[-1] > minus_di[-1]
        short_signal = minus_di[-1] > plus_di[-1]

        if not long_signal and not short_signal:
            return self._no_trade(symbol, "no_di_crossover")

        # EMA trend alignment
        ema21 = self._ema(c, self.EMA_TREND)
        if long_signal and cur < ema21[-1]:
            return self._no_trade(symbol, "long_below_ema21")
        if short_signal and cur > ema21[-1]:
            return self._no_trade(symbol, "short_above_ema21")

        # RSI filter
        rsi = self._rsi(c, 14)
        if long_signal and not (self.RSI_LONG_MIN <= rsi <= self.RSI_LONG_MAX):
            return self._no_trade(symbol, f"rsi_out_of_range_long_{rsi:.1f}")
        if short_signal and not (self.RSI_SHORT_MIN <= rsi <= self.RSI_SHORT_MAX):
            return self._no_trade(symbol, f"rsi_out_of_range_short_{rsi:.1f}")

        # Regime direction alignment
        if long_signal and regime in ("strong_bear",):
            return self._no_trade(symbol, "long_blocked_strong_bear")
        if short_signal and regime in ("strong_bull",):
            return self._no_trade(symbol, "short_blocked_strong_bull")

        # Daily trend confirmation
        daily_c = df_daily["close"].values
        d_ema20 = self._ema(daily_c, 20)
        if long_signal and daily_c[-1] < d_ema20[-1]:
            return self._no_trade(symbol, "long_below_daily_ema20")
        if short_signal and daily_c[-1] > d_ema20[-1]:
            return self._no_trade(symbol, "short_above_daily_ema20")

        # Volume check
        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v[:-1]))
        vol_mult = float(v[-1]) / max(avg_vol, 1)
        if vol_mult < self.MIN_VOL_MULT:
            return self._no_trade(symbol, f"low_volume_{vol_mult:.2f}x")

        # SL / Targets
        atr = self._atr(h, l, c, 14)
        vp = self._vol_profile(symbol, df_daily)
        vol_regime = vp.regime if vp else "normal"

        if long_signal:
            swing_low = float(np.min(l[-5:]))
            sl = self._dynamic_sl(cur, swing_low, atr, vol_regime)
            rps = cur - sl
            if rps <= 0.01:
                return self._no_trade(symbol, "invalid_sl_long")
            t1 = round(cur + rps * self.T1_MULT, 2)
            t2 = round(cur + rps * self.T2_MULT, 2)
            be = round(cur + rps * 0.8, 2)
            signal = Signal.LONG
            direction = "long"
        else:
            swing_high = float(np.max(h[-5:]))
            sl = self._dynamic_sl_short(cur, swing_high, atr, vol_regime)
            rps = sl - cur
            if rps <= 0.01:
                return self._no_trade(symbol, "invalid_sl_short")
            t1 = round(cur - rps * self.T1_MULT, 2)
            t2 = round(cur - rps * self.T2_MULT, 2)
            be = round(cur - rps * 0.8, 2)
            signal = Signal.SHORT
            direction = "short"

        qty = int(capital_per_trade / cur)
        if qty < 1:
            return self._no_trade(symbol, "insufficient_capital")

        if long_signal:
            net_reward = qty * (t2 - cur) - charges_estimate
        else:
            net_reward = qty * (cur - t2) - charges_estimate
        net_rr = net_reward / max(qty * rps, 0.01)

        if net_rr < self.MIN_RR:
            return self._no_trade(symbol, f"rr_{net_rr:.2f}_below_{self.MIN_RR}")

        quality = "A" if (adx[-1] > 35 and vol_mult >= 1.5) else "B"

        return TradeSetup(
            signal=signal, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=t1, target_2=t2,
            breakeven_trigger=be, trailing_step=round(atr * 0.5, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality,
            reason=f"adx_{direction}_adx{adx[-1]:.0f}_di+{plus_di[-1]:.0f}_di-{minus_di[-1]:.0f}_rsi{rsi:.0f}",
            max_hold_candles=self.MAX_HOLD_CANDLES,
            strategy_name=self.strategy_name, is_valid=True,
        )
