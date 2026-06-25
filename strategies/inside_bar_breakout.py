"""
Inside Bar Breakout Strategy

Concept: An inside bar (IB) is a candle whose high and low are entirely within
the previous candle's range. It represents consolidation/indecision. A breakout
from this compressed range often leads to a directional move.

Entry conditions:
  Long : Price breaks above inside bar's high (mother candle high),
         RSI 45-75, volume on breakout candle > average.
  Short: Price breaks below inside bar's low (mother candle low),
         RSI 25-55, volume on breakout candle > average.

The mother bar's range defines natural risk — SL is opposite end of the
inside bar pattern. This gives very clean, well-defined risk.

SL  : Opposite side of inside bar pattern + ATR buffer
T1  : 1.5x risk
T2  : 2.5x risk
Max hold: 10 candles (2.5 hours)

Best conditions: All regimes — pattern is regime-neutral.
  Longs preferred in bull; shorts in bear; both in sideways.
Avoid: Extremely low volume stocks (pattern unreliable)

Popularized by: Price action traders (Brooks, Nial Fuller).
Edge: Volatility contraction precedes expansion. IB identifies the
contraction; breakout captures the expansion. Risk is tightly defined
by the pattern itself.

NSE applicability: Very effective on 15m charts. Indian stocks frequently
form inside bars during mid-session consolidation (11:00-14:00).
"""

import numpy as np
import pandas as pd
import logging
import pytz
from .base_strategy import BaseStrategy, TradeSetup, Signal

IST = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger(__name__)


class InsideBarBreakoutStrategy(BaseStrategy):

    MIN_RR = 1.5
    MAX_HOLD_CANDLES = 10
    RSI_LONG_MIN = 45
    RSI_LONG_MAX = 75
    RSI_SHORT_MIN = 25
    RSI_SHORT_MAX = 55
    MIN_VOL_MULT = 1.0
    T1_MULT = 1.5
    T2_MULT = 2.5
    NO_TRADE_BEFORE = "09:45"
    NO_TRADE_AFTER = "14:15"
    # Minimum mother bar range as % of price — filters tiny/meaningless patterns
    MIN_MOTHER_RANGE_PCT = 0.003  # 0.3%
    MAX_MOTHER_RANGE_PCT = 0.025  # 2.5% — too wide = too much risk

    @property
    def strategy_name(self) -> str:
        return "InsideBarBreakout"

    @property
    def required_timeframe(self) -> str:
        return "15m"

    def _find_inside_bar(self, h, l):
        """Look for inside bar pattern in last 5 candles.

        Returns (mother_idx, ib_idx) if found, or (None, None).
        An inside bar: h[ib] <= h[mother] and l[ib] >= l[mother].
        We look for the most recent IB pattern.
        """
        n = len(h)
        if n < 3:
            return None, None

        # Search backwards from second-to-last candle
        # (last candle is the breakout candle, IB must be before it)
        for ib_idx in range(n - 2, max(n - 6, 0), -1):
            mother_idx = ib_idx - 1
            if mother_idx < 0:
                continue
            if h[ib_idx] <= h[mother_idx] and l[ib_idx] >= l[mother_idx]:
                return mother_idx, ib_idx

        return None, None

    def generate_signal(self, symbol, df_primary, df_daily,
                        regime_bullish, capital_per_trade, charges_estimate,
                        regime: str = "") -> TradeSetup:

        if df_primary is None or len(df_primary) < 30:
            return self._no_trade(symbol, "insufficient_data")

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

        # Find inside bar pattern
        mother_idx, ib_idx = self._find_inside_bar(h, l)
        if mother_idx is None:
            return self._no_trade(symbol, "no_inside_bar")

        mother_high = float(h[mother_idx])
        mother_low = float(l[mother_idx])
        mother_range = mother_high - mother_low

        # Mother bar range quality check
        range_pct = mother_range / cur
        if range_pct < self.MIN_MOTHER_RANGE_PCT:
            return self._no_trade(symbol, f"mother_range_too_small_{range_pct:.4f}")
        if range_pct > self.MAX_MOTHER_RANGE_PCT:
            return self._no_trade(symbol, f"mother_range_too_large_{range_pct:.4f}")

        # Check breakout direction on current (latest) candle
        long_bo = cur > mother_high * 1.001  # close above mother high
        short_bo = cur < mother_low * 0.999   # close below mother low

        if not long_bo and not short_bo:
            return self._no_trade(symbol, "no_breakout_from_ib")

        # If both (extremely rare), pick stronger direction
        if long_bo and short_bo:
            if (cur - mother_high) >= (mother_low - cur):
                short_bo = False
            else:
                long_bo = False

        # Regime filters
        if long_bo and regime in ("strong_bear",):
            return self._no_trade(symbol, "long_blocked_strong_bear")
        if short_bo and regime in ("strong_bull",):
            return self._no_trade(symbol, "short_blocked_strong_bull")

        # Daily trend alignment (soft filter — only block longs below daily EMA20)
        if df_daily is not None and len(df_daily) >= 22:
            daily_c = df_daily["close"].values
            d_ema20 = self._ema(daily_c, 20)
            if long_bo and daily_c[-1] < d_ema20[-1] * 0.98:
                return self._no_trade(symbol, "long_well_below_daily_ema20")
            if short_bo and daily_c[-1] > d_ema20[-1] * 1.02:
                return self._no_trade(symbol, "short_well_above_daily_ema20")

        # RSI filter
        rsi = self._rsi(c, 14)
        if long_bo and not (self.RSI_LONG_MIN <= rsi <= self.RSI_LONG_MAX):
            return self._no_trade(symbol, f"rsi_out_of_range_long_{rsi:.1f}")
        if short_bo and not (self.RSI_SHORT_MIN <= rsi <= self.RSI_SHORT_MAX):
            return self._no_trade(symbol, f"rsi_out_of_range_short_{rsi:.1f}")

        # Volume confirmation
        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v[:-1]))
        vol_mult = float(v[-1]) / max(avg_vol, 1)
        if vol_mult < self.MIN_VOL_MULT:
            return self._no_trade(symbol, f"low_volume_{vol_mult:.2f}x")

        # SL / Targets — Inside bar pattern defines natural risk
        atr = self._atr(h, l, c, 14)
        vp = self._vol_profile(symbol, df_daily)
        vol_regime = vp.regime if vp else "normal"

        if long_bo:
            # SL below mother bar low
            sl = self._dynamic_sl(cur, mother_low, atr, vol_regime)
            rps = cur - sl
            if rps <= 0.01:
                return self._no_trade(symbol, "invalid_sl_long")
            t1 = round(cur + rps * self.T1_MULT, 2)
            t2 = round(cur + rps * self.T2_MULT, 2)
            be = round(cur + rps * 0.8, 2)
            signal = Signal.LONG
            direction = "long"
        else:
            # SL above mother bar high
            sl = self._dynamic_sl_short(cur, mother_high, atr, vol_regime)
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

        if long_bo:
            net_reward = qty * (t2 - cur) - charges_estimate
        else:
            net_reward = qty * (cur - t2) - charges_estimate
        net_rr = net_reward / max(qty * rps, 0.01)

        if net_rr < self.MIN_RR:
            return self._no_trade(symbol, f"rr_{net_rr:.2f}_below_{self.MIN_RR}")

        quality = "A" if (vol_mult >= 1.5 and range_pct >= 0.008) else "B"

        return TradeSetup(
            signal=signal, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=t1, target_2=t2,
            breakeven_trigger=be, trailing_step=round(atr * 0.4, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality,
            reason=f"ib_breakout_{direction}_range{range_pct:.3f}_vol{vol_mult:.1f}x_rsi{rsi:.0f}",
            max_hold_candles=self.MAX_HOLD_CANDLES,
            strategy_name=self.strategy_name, is_valid=True,
        )
