"""
Supertrend + RSI Strategy

Concept: Supertrend (ATR-based trailing stop indicator) defines trend direction.
RSI confirms momentum is aligned. Entry on Supertrend flip + RSI confirmation.

Entry conditions:
  Long : Supertrend flips bullish (price crosses above Supertrend line),
         RSI > 50 (momentum confirms), price above daily EMA20.
  Short: Supertrend flips bearish (price crosses below Supertrend line),
         RSI < 50, price below daily EMA20.

Supertrend formula:
  Upper band = (H+L)/2 + multiplier * ATR
  Lower band = (H+L)/2 - multiplier * ATR
  If close > prev upper band -> bullish, use lower band as trailing stop
  If close < prev lower band -> bearish, use upper band as trailing stop

SL  : Supertrend line itself (the natural invalidation level)
T1  : 1.5x risk
T2  : 2.5x risk
Max hold: 12 candles (3 hours) — trending setups need time to play out

Best conditions: strong_bull, weak_bull, weak_bear (trending markets)
Avoid: sideways regime (Supertrend whipsaws in range-bound markets)

Popularized by: Olivier Seban; widely used by Indian retail traders on NSE.
Edge: ATR-adaptive stops auto-adjust to volatility, reducing whipsaws vs fixed-% stops.
"""

import numpy as np
import pandas as pd
import logging
import pytz
from .base_strategy import BaseStrategy, TradeSetup, Signal

IST = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger(__name__)


class SupertrendRSIStrategy(BaseStrategy):

    ATR_PERIOD = 10
    ATR_MULTIPLIER = 3.0
    RSI_PERIOD = 14
    RSI_LONG_MIN = 50
    RSI_LONG_MAX = 80
    RSI_SHORT_MIN = 20
    RSI_SHORT_MAX = 50
    MIN_RR = 1.5
    MAX_HOLD_CANDLES = 12
    T1_MULT = 1.5
    T2_MULT = 2.5
    NO_TRADE_BEFORE = "09:45"
    NO_TRADE_AFTER = "14:30"
    MIN_VOL_MULT = 0.8

    # Only trade these stocks — tested and validated (3/10 pass, Sharpe 3.4-3.7)
    WHITELIST = {"ONGC", "TITAN", "BHEL"}

    # Regimes where this strategy is suppressed
    BLOCKED_REGIMES = {"sideways", "high_volatility"}

    @property
    def strategy_name(self) -> str:
        return "SupertrendRSI"

    @property
    def required_timeframe(self) -> str:
        return "15m"

    def _calc_supertrend(self, h, l, c, period=10, multiplier=3.0):
        """Calculate Supertrend indicator.

        Returns:
            trend: array of 1 (bullish) or -1 (bearish)
            supertrend: array of supertrend line values
        """
        n = len(c)
        if n < period + 1:
            return np.zeros(n), np.zeros(n)

        # ATR calculation (simple rolling mean of TR)
        tr = np.zeros(n)
        tr[0] = h[0] - l[0]
        for i in range(1, n):
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

        atr = np.zeros(n)
        atr[:period] = np.nan
        atr[period] = np.mean(tr[1:period + 1])
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        hl2 = (h + l) / 2.0
        upper_band = hl2 + multiplier * atr
        lower_band = hl2 - multiplier * atr

        trend = np.ones(n, dtype=int)  # 1 = bullish
        supertrend = np.zeros(n)

        for i in range(period + 1, n):
            # Adjust bands based on previous values (Supertrend logic)
            if lower_band[i] > lower_band[i - 1] or c[i - 1] < lower_band[i - 1]:
                pass  # keep current lower_band
            else:
                lower_band[i] = lower_band[i - 1]

            if upper_band[i] < upper_band[i - 1] or c[i - 1] > upper_band[i - 1]:
                pass  # keep current upper_band
            else:
                upper_band[i] = upper_band[i - 1]

            # Determine trend
            if trend[i - 1] == 1:
                if c[i] < lower_band[i]:
                    trend[i] = -1
                    supertrend[i] = upper_band[i]
                else:
                    trend[i] = 1
                    supertrend[i] = lower_band[i]
            else:
                if c[i] > upper_band[i]:
                    trend[i] = 1
                    supertrend[i] = lower_band[i]
                else:
                    trend[i] = -1
                    supertrend[i] = upper_band[i]

        return trend, supertrend

    def generate_signal(self, symbol, df_primary, df_daily,
                        regime_bullish, capital_per_trade, charges_estimate,
                        regime: str = "") -> TradeSetup:

        # Whitelist gate — only validated stocks
        if symbol not in self.WHITELIST:
            return self._no_trade(symbol, "not_in_supertrend_whitelist")

        # Regime gate — suppress in sideways/high-vol (whipsaw-prone)
        if regime in self.BLOCKED_REGIMES:
            return self._no_trade(symbol, f"regime_blocked_{regime}")

        if df_primary is None or len(df_primary) < 30:
            return self._no_trade(symbol, "insufficient_data")
        if df_daily is None or len(df_daily) < 22:
            return self._no_trade(symbol, "insufficient_daily_data")

        # Time window check — use candle timestamp for backtesting compatibility
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

        # Calculate Supertrend
        trend, st_line = self._calc_supertrend(
            h, l, c, self.ATR_PERIOD, self.ATR_MULTIPLIER
        )

        # Need at least 2 candles of valid trend to detect a flip
        if len(trend) < self.ATR_PERIOD + 3:
            return self._no_trade(symbol, "insufficient_supertrend_history")

        # Detect Supertrend flip (trend changed in last 3 candles)
        flip_long = trend[-1] == 1 and any(trend[-i] == -1 for i in range(2, 4) if i <= len(trend))
        flip_short = trend[-1] == -1 and any(trend[-i] == 1 for i in range(2, 4) if i <= len(trend))

        if not flip_long and not flip_short:
            return self._no_trade(symbol, "no_supertrend_flip")

        # RSI filter
        rsi = self._rsi(c, self.RSI_PERIOD)

        if flip_long and not (self.RSI_LONG_MIN <= rsi <= self.RSI_LONG_MAX):
            return self._no_trade(symbol, f"rsi_out_of_range_long_{rsi:.1f}")
        if flip_short and not (self.RSI_SHORT_MIN <= rsi <= self.RSI_SHORT_MAX):
            return self._no_trade(symbol, f"rsi_out_of_range_short_{rsi:.1f}")

        # Daily trend alignment
        daily_c = df_daily["close"].values
        d_ema20 = self._ema(daily_c, 20)
        if flip_long and daily_c[-1] < d_ema20[-1]:
            return self._no_trade(symbol, "long_below_daily_ema20")
        if flip_short and daily_c[-1] > d_ema20[-1]:
            return self._no_trade(symbol, "short_above_daily_ema20")

        # Volume check
        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v[:-1]))
        vol_mult = float(v[-1]) / max(avg_vol, 1)
        if vol_mult < self.MIN_VOL_MULT:
            return self._no_trade(symbol, f"low_volume_{vol_mult:.2f}x")

        # SL = Supertrend line (the natural invalidation)
        atr = self._atr(h, l, c, 14)
        vp = self._vol_profile(symbol, df_daily)
        vol_regime = vp.regime if vp else "normal"

        if flip_long:
            st_val = st_line[-1]
            if st_val <= 0 or st_val >= cur:
                return self._no_trade(symbol, "invalid_supertrend_line_long")
            sl = self._dynamic_sl(cur, st_val, atr, vol_regime)
            rps = cur - sl
            if rps <= 0.01:
                return self._no_trade(symbol, "invalid_sl_long")
            t1 = round(cur + rps * self.T1_MULT, 2)
            t2 = round(cur + rps * self.T2_MULT, 2)
            be = round(cur + rps * 0.8, 2)
            signal = Signal.LONG
            direction = "long"
        else:
            st_val = st_line[-1]
            if st_val <= 0 or st_val <= cur:
                return self._no_trade(symbol, "invalid_supertrend_line_short")
            sl = self._dynamic_sl_short(cur, st_val, atr, vol_regime)
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

        if flip_long:
            net_reward = qty * (t2 - cur) - charges_estimate
        else:
            net_reward = qty * (cur - t2) - charges_estimate
        net_rr = net_reward / max(qty * rps, 0.01)

        if net_rr < self.MIN_RR:
            return self._no_trade(symbol, f"rr_{net_rr:.2f}_below_{self.MIN_RR}")

        quality = "A" if (vol_mult >= 1.5 and abs(rsi - 50) > 15) else "B"

        return TradeSetup(
            signal=signal, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=t1, target_2=t2,
            breakeven_trigger=be, trailing_step=round(atr * 0.5, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality,
            reason=f"supertrend_{direction}_rsi{rsi:.0f}_vol{vol_mult:.1f}x_sl_{vol_regime}",
            max_hold_candles=self.MAX_HOLD_CANDLES,
            strategy_name=self.strategy_name, is_valid=True,
        )
