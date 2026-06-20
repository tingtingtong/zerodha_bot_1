"""
VWAP Reversion Strategy

Concept: In low-VIX / sideways markets, price oscillates around VWAP (the
institutional benchmark). A candle that touches VWAP and reverses with volume
is a high-probability mean-reversion entry.

Entry conditions:
  Long : price pulls back to VWAP from above, current candle closes above VWAP
         with above-average volume. RSI 42–65 (not oversold, not extended).
  Short: price rallies up to VWAP from below, current candle closes below VWAP
         with above-average volume. RSI 38–62.

SL  : 1.5x ATR below/above VWAP (VWAP acts as the invalidation line)
T1  : entry ± 1.5x risk
T2  : entry ± 2.5x risk
Max hold: 8 candles (2 hours) — VWAP setups are short-duration

Best conditions: sideways regime + VIX < 15
Avoid: strong trending days (price won't revert to VWAP)
"""

import numpy as np
import pandas as pd
import logging
import pytz
from .base_strategy import BaseStrategy, TradeSetup, Signal

IST = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger(__name__)

# Stocks where VWAP reversion is statistically reliable (backtested Jun 2025–Jun 2026).
# Heavy institutional volume / range-bound behaviour causes price to anchor to VWAP.
VWAP_WHITELIST = {"JSWSTEEL", "TATASTEEL", "GODREJCP", "TITAN", "COFORGE", "BAJAJFINSV", "DIVISLAB"}


class VWAPStrategy(BaseStrategy):

    MIN_RR = 1.5
    MAX_HOLD_CANDLES = 8          # 2 hours — VWAP setups resolve quickly
    VWAP_TOLERANCE = 0.005        # price must be within 0.5% of VWAP
    MIN_VOL_MULT = 1.2            # bounce/rejection needs above-average volume
    RSI_LONG_MIN = 42
    RSI_LONG_MAX = 65
    RSI_SHORT_MIN = 38
    RSI_SHORT_MAX = 62
    T1_MULT = 1.5
    T2_MULT = 2.5
    NO_TRADE_BEFORE = "09:45"
    NO_TRADE_AFTER = "14:00"      # VWAP drifts and distorts late in the session

    @property
    def strategy_name(self) -> str:
        return "VWAP"

    @property
    def required_timeframe(self) -> str:
        return "15m"

    def _calc_vwap(self, df: pd.DataFrame) -> float:
        """Calculate VWAP for the most recent trading day in df.

        Uses the last candle's date rather than wall-clock time so this
        works correctly in both live trading and backtesting.
        """
        if "timestamp" in df.columns:
            ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(IST)
            last_date = ts.iloc[-1].date()
            today_df = df[ts.dt.date == last_date]
        else:
            idx = df.index
            if not isinstance(idx, pd.DatetimeIndex):
                return float("nan")
            dates = idx.tz_convert(IST).date if idx.tz is not None else idx.date
            last_date = dates[-1]
            today_df = df[[d == last_date for d in dates]]

        if len(today_df) < 2:
            return float("nan")

        typical = (today_df["high"] + today_df["low"] + today_df["close"]) / 3
        vol = today_df["volume"]
        total_vol = vol.sum()
        if total_vol == 0:
            return float("nan")
        return float((typical * vol).sum() / total_vol)

    def generate_signal(self, symbol, df_primary, df_daily,
                        regime_bullish, capital_per_trade, charges_estimate,
                        regime: str = "") -> TradeSetup:

        if symbol not in VWAP_WHITELIST:
            return self._no_trade(symbol, "not_in_vwap_whitelist")

        if df_primary is None or len(df_primary) < 20:
            return self._no_trade(symbol, "insufficient_data")

        # Live: use wall clock. Backtest: use last candle's timestamp.
        if "timestamp" in df_primary.columns:
            last_ts = pd.to_datetime(df_primary["timestamp"].iloc[-1], utc=True).tz_convert(IST)
            is_live = last_ts.date() == pd.Timestamp.now(tz=IST).date()
            now_str = pd.Timestamp.now(tz=IST).strftime("%H:%M") if is_live else last_ts.strftime("%H:%M")
        else:
            now_str = pd.Timestamp.now(tz=IST).strftime("%H:%M")
        if now_str < self.NO_TRADE_BEFORE or now_str > self.NO_TRADE_AFTER:
            return self._no_trade(symbol, f"outside_window_{now_str}")

        vwap = self._calc_vwap(df_primary)
        if np.isnan(vwap) or vwap <= 0:
            return self._no_trade(symbol, "vwap_unavailable")

        c = df_primary["close"].values
        h = df_primary["high"].values
        l = df_primary["low"].values
        v = df_primary["volume"].values
        cur = c[-1]

        # Price must be near VWAP
        dist_pct = abs(cur - vwap) / vwap
        if dist_pct > self.VWAP_TOLERANCE:
            return self._no_trade(symbol, f"price_far_from_vwap_{dist_pct:.3f}")

        # Long: candle low touched VWAP from above, closed back above it
        long_setup = (
            cur > vwap
            and l[-1] <= vwap * 1.003   # wick touched VWAP
            and c[-1] > c[-2]           # bounce candle (closing higher)
        )
        # Short: candle high touched VWAP from below, closed back below it
        short_setup = (
            cur < vwap
            and h[-1] >= vwap * 0.997   # wick touched VWAP
            and c[-1] < c[-2]           # rejection candle (closing lower)
        )

        if not long_setup and not short_setup:
            return self._no_trade(symbol, "no_vwap_touch_reversal")

        # Regime filter
        if long_setup and regime == "strong_bear":
            return self._no_trade(symbol, "long_blocked_strong_bear")
        if short_setup and regime_bullish:
            return self._no_trade(symbol, "short_blocked_bull_regime")

        # RSI filter
        rsi = self._rsi(c, 14)
        if long_setup and not (self.RSI_LONG_MIN <= rsi <= self.RSI_LONG_MAX):
            return self._no_trade(symbol, f"rsi_out_of_range_{rsi:.1f}")
        if short_setup and not (self.RSI_SHORT_MIN <= rsi <= self.RSI_SHORT_MAX):
            return self._no_trade(symbol, f"rsi_out_of_range_{rsi:.1f}")

        # Volume confirmation
        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v[:-1]))
        vol_mult = float(v[-1]) / max(avg_vol, 1)
        if vol_mult < self.MIN_VOL_MULT:
            return self._no_trade(symbol, f"low_volume_{vol_mult:.2f}x")

        # SL / targets — VWAP itself is the invalidation line
        atr = self._atr(h, l, c, 14)
        vp = self._vol_profile(symbol, df_daily)
        vol_regime = vp.regime if vp else "normal"

        if long_setup:
            sl = self._dynamic_sl(cur, vwap - atr * 0.5, atr, vol_regime)
            rps = cur - sl
            if rps <= 0.01:
                return self._no_trade(symbol, "invalid_sl_long")
            t1 = round(cur + rps * self.T1_MULT, 2)
            t2 = round(cur + rps * self.T2_MULT, 2)
            be = round(cur + rps * 0.8, 2)
            signal = Signal.LONG
            direction = "long"
        else:
            sl = self._dynamic_sl_short(cur, vwap + atr * 0.5, atr, vol_regime)
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

        if long_setup:
            net_reward = qty * (t2 - cur) - charges_estimate
        else:
            net_reward = qty * (cur - t2) - charges_estimate
        net_rr = net_reward / max(qty * rps, 0.01)

        if net_rr < self.MIN_RR:
            return self._no_trade(symbol, f"rr_{net_rr:.2f}_below_{self.MIN_RR}")

        quality = "A" if (vol_mult >= 2.0 and dist_pct <= 0.002) else "B"

        return TradeSetup(
            signal=signal, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=t1, target_2=t2,
            breakeven_trigger=be, trailing_step=round(atr * 0.4, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality,
            reason=f"vwap_{direction}_dist{dist_pct:.3f}_vol{vol_mult:.1f}x_rsi{rsi:.0f}",
            max_hold_candles=self.MAX_HOLD_CANDLES,
            strategy_name=self.strategy_name, is_valid=True,
        )
