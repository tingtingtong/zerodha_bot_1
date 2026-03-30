import numpy as np
import pandas as pd
import logging
from .base_strategy import BaseStrategy, TradeSetup, Signal

logger = logging.getLogger(__name__)


class EMAPullbackStrategy(BaseStrategy):

    EMA_FAST = 9
    EMA_SLOW = 21
    MIN_VOL_MULT = 1.5   # raised from 1.2 — need genuine volume surge, not just above average
    MIN_RR = 1.5
    MAX_HOLD_CANDLES = 16
    NO_TRADE_BEFORE = "09:45"
    NO_TRADE_AFTER = "14:45"

    @property
    def strategy_name(self) -> str:
        return "EMAPullback"

    @property
    def required_timeframe(self) -> str:
        return "15m"

    def generate_signal(self, symbol, df_primary, df_daily,
                        regime_bullish, capital_per_trade, charges_estimate) -> TradeSetup:

        # No regime gate — EMA pullback scans all days; price/RSI/volume conditions decide
        if df_primary is None or len(df_primary) < 30:
            return self._no_trade(symbol, "insufficient_15m_data")

        # Use current IST time for window check, not candle timestamp (yfinance can return stale times)
        import pytz
        now_str = pd.Timestamp.now(tz=pytz.timezone("Asia/Kolkata")).strftime("%H:%M")
        if now_str < self.NO_TRADE_BEFORE or now_str > self.NO_TRADE_AFTER:
            return self._no_trade(symbol, f"outside_window_{now_str}")

        c = df_primary["close"].values
        h = df_primary["high"].values
        l = df_primary["low"].values
        v = df_primary["volume"].values

        ema9 = self._ema(c, self.EMA_FAST)
        ema21 = self._ema(c, self.EMA_SLOW)
        cur = c[-1]

        if ema9[-1] <= ema21[-1]:
            return self._no_trade(symbol, "bearish_ribbon")
        if cur <= ema9[-1] or cur <= ema21[-1]:
            return self._no_trade(symbol, "price_below_ema")

        # EMA9 must be rising (slope positive over last 3 candles) — not just above EMA21
        if ema9[-1] <= ema9[-3]:
            return self._no_trade(symbol, "ema9_not_rising")

        # Daily trend filter: stock must be above its 20-day EMA (in overall uptrend)
        if df_daily is not None and len(df_daily) >= 22:
            daily_c = df_daily["close"].values
            d_ema20 = self._ema(daily_c, 20)
            if daily_c[-1] < d_ema20[-1]:
                return self._no_trade(symbol, "below_daily_ema20")

        # Pullback: low touched EMA9 in last 5 candles AND current price is above EMA9 (bounce confirmed)
        pullback = (cur > ema9[-1]) and any(l[-i] <= ema9[-i] * 1.005 for i in range(2, 6) if i < len(l))
        if not pullback:
            return self._no_trade(symbol, "no_pullback")

        # Bounce candle must close above EMA9 (not just touch and fail)
        if c[-1] <= ema9[-1]:
            return self._no_trade(symbol, "bounce_candle_below_ema9")

        rsi = self._rsi(c, 14)
        if rsi < 55:
            return self._no_trade(symbol, f"rsi_below_55_{rsi:.1f}")

        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v))
        if v[-1] < avg_vol * self.MIN_VOL_MULT:
            return self._no_trade(symbol, f"low_volume_{v[-1]/avg_vol:.2f}x")

        atr = self._atr(h, l, c, 14)
        pullback_low = float(np.min(l[-4:-1]))
        vp = self._vol_profile(symbol, df_daily)
        vol_regime = vp.regime if vp else "normal"
        sl = self._dynamic_sl(cur, pullback_low, atr, vol_regime)
        rps = cur - sl

        if rps <= 0.01:
            return self._no_trade(symbol, "invalid_sl")

        t1 = cur + rps * 1.5
        t2 = cur + rps * 2.5
        be = cur + rps * 1.0
        qty = int(capital_per_trade / cur)

        if qty < 1:
            return self._no_trade(symbol, "insufficient_capital")

        # RR check uses T2 (2.5x risk) — that's the full trade target
        # T1 is partial exit; overall trade is assessed against T2
        net_reward = qty * (t2 - cur) - charges_estimate
        net_rr = net_reward / max(qty * rps, 0.01)

        if net_rr < self.MIN_RR:
            return self._no_trade(symbol, f"rr_{net_rr:.2f}_below_{self.MIN_RR}")

        vspike = v[-1] / max(avg_vol, 1)
        quality = "A" if rsi > 65 and vspike >= 2.0 else "B" if rsi >= 55 else "C"

        return TradeSetup(
            signal=Signal.LONG, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=round(t1, 2), target_2=round(t2, 2),
            breakeven_trigger=round(be, 2), trailing_step=round(atr * 0.5, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality,
            reason=f"ema_pullback_rsi{rsi:.0f}_vol{vspike:.1f}x_sl_{vol_regime}",
            max_hold_candles=self.MAX_HOLD_CANDLES,
            strategy_name=self.strategy_name, is_valid=True,
        )
