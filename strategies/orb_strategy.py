"""
Opening Range Breakout (ORB) Strategy

Opening range = first 2 candles of the day (09:15-09:44, 30 minutes).
Entry: price breaks out of range high (long) or range low (short) after 09:45.
SL  : opposite side of range + small ATR buffer.
T1  : entry ± 1x range size.
T2  : entry ± 2x range size.

Fires reliably on most trading days when stocks have a clear morning move.
Works in any regime: longs allowed if stock is above daily EMA20,
shorts allowed in bear/sideways.
"""

import numpy as np
import pandas as pd
import logging
import pytz
from .base_strategy import BaseStrategy, TradeSetup, Signal

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class ORBStrategy(BaseStrategy):

    MIN_RANGE_PCT = 0.003   # 0.3% minimum range — filters choppy opens
    MAX_RANGE_PCT = 0.030   # 3.0% maximum range — filters gap/circuit days
    MIN_VOL_MULT = 0.8      # breakout candle volume >= 0.8x average
    MIN_RR = 1.5
    MAX_HOLD_CANDLES = 12        # 3 hours max hold (trending regimes)
    MAX_HOLD_CANDLES_SIDEWAYS = 6  # 1.5 hours in sideways — breakouts reverse faster
    NO_TRADE_BEFORE = "09:45"
    NO_TRADE_AFTER = "12:30"  # ORB setups go stale by afternoon

    @property
    def strategy_name(self) -> str:
        return "ORB"

    @property
    def required_timeframe(self) -> str:
        return "15m"

    def _get_today_candles(self, df: pd.DataFrame) -> pd.DataFrame:
        today = pd.Timestamp.now(tz=IST).date()
        # Prefer the 'timestamp' column (set by FreeNSEProvider._normalize)
        # Fall back to df.index only if it's a genuine DatetimeIndex
        if "timestamp" in df.columns:
            ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(IST)
            return df[ts.dt.date == today]
        idx = df.index
        if not isinstance(idx, pd.DatetimeIndex):
            return df.iloc[0:0]  # empty — can't determine today's candles
        if idx.tz is not None:
            dates = idx.tz_convert(IST).date
        else:
            dates = idx.date
        return df[dates == today]

    def generate_signal(self, symbol, df_primary, df_daily,
                        regime_bullish, capital_per_trade, charges_estimate,
                        regime: str = "") -> TradeSetup:

        if df_primary is None or len(df_primary) < 5:
            return self._no_trade(symbol, "insufficient_data")

        now_str = pd.Timestamp.now(tz=IST).strftime("%H:%M")
        if now_str < self.NO_TRADE_BEFORE or now_str > self.NO_TRADE_AFTER:
            return self._no_trade(symbol, f"outside_window_{now_str}")

        # ── Opening range from today's first 2 candles ──────────────
        today_df = self._get_today_candles(df_primary)
        if len(today_df) < 2:
            return self._no_trade(symbol, "insufficient_today_candles")

        orb_df = today_df.iloc[:2]
        orb_high = float(orb_df["high"].max())
        orb_low = float(orb_df["low"].min())
        range_size = orb_high - orb_low

        c = df_primary["close"].values
        h = df_primary["high"].values
        l = df_primary["low"].values
        v = df_primary["volume"].values
        cur = c[-1]

        if cur <= 0:
            return self._no_trade(symbol, "invalid_price")

        # ── Range quality ────────────────────────────────────────────
        range_pct = range_size / cur
        if range_pct < self.MIN_RANGE_PCT:
            return self._no_trade(symbol, f"range_too_tight_{range_pct:.3f}")
        if range_pct > self.MAX_RANGE_PCT:
            return self._no_trade(symbol, f"range_too_wide_{range_pct:.3f}")

        # ── Volume confirmation ──────────────────────────────────────
        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v[:-1]))
        vol_mult = float(v[-1]) / max(avg_vol, 1)
        if vol_mult < self.MIN_VOL_MULT:
            return self._no_trade(symbol, f"low_volume_{vol_mult:.2f}x")

        # ── Breakout direction ───────────────────────────────────────
        long_bo = cur > orb_high * 1.001
        short_bo = cur < orb_low * 0.999

        if not long_bo and not short_bo:
            return self._no_trade(symbol, "no_breakout")

        # If both (rare), pick the one with more clearance
        if long_bo and short_bo:
            if (cur - orb_high) >= (orb_low - cur):
                short_bo = False
            else:
                long_bo = False

        # ── Regime filters ───────────────────────────────────────────
        if long_bo:
            if not regime_bullish:
                # Allow long in weak_bear only if stock itself is above daily EMA20
                if df_daily is not None and len(df_daily) >= 22:
                    d_ema20 = self._ema(df_daily["close"].values, 20)
                    if df_daily["close"].values[-1] < d_ema20[-1]:
                        return self._no_trade(symbol, "long_stock_below_daily_ema20")

        if short_bo and regime_bullish:
            return self._no_trade(symbol, "short_blocked_bull_regime")

        # ── SL / targets ─────────────────────────────────────────────
        atr = self._atr(h, l, c, 14)
        vp = self._vol_profile(symbol, df_daily)
        vol_regime = vp.regime if vp else "normal"

        if long_bo:
            sl = self._dynamic_sl(cur, orb_low, atr, vol_regime)
            rps = cur - sl
            if rps <= 0.01:
                return self._no_trade(symbol, "invalid_sl_long")
            t1 = round(cur + range_size, 2)
            t2 = round(cur + range_size * 2, 2)
            be = round(cur + rps * 0.8, 2)
            signal = Signal.LONG
        else:
            sl = self._dynamic_sl_short(cur, orb_high, atr, vol_regime)
            rps = sl - cur
            if rps <= 0.01:
                return self._no_trade(symbol, "invalid_sl_short")
            t1 = round(cur - range_size, 2)
            t2 = round(cur - range_size * 2, 2)
            be = round(cur - rps * 0.8, 2)
            signal = Signal.SHORT

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

        direction = "long" if long_bo else "short"
        quality = "A" if (vol_mult >= 2.0 and range_pct >= 0.015) else "B"
        hold_candles = self.MAX_HOLD_CANDLES_SIDEWAYS if regime == "sideways" else self.MAX_HOLD_CANDLES

        return TradeSetup(
            signal=signal, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=t1, target_2=t2,
            breakeven_trigger=be, trailing_step=round(atr * 0.5, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality,
            reason=f"orb_{direction}_range{range_pct:.1%}_vol{vol_mult:.1f}x_sl_{vol_regime}",
            max_hold_candles=hold_candles,
            strategy_name=self.strategy_name, is_valid=True,
        )
