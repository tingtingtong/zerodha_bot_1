"""
EMA Breakdown Strategy — mirror image of EMA Pullback, for SHORT trades.

Only fires in bear/sideways regimes (regime_bullish=False).

Logic:
- Bearish ribbon: EMA9 < EMA21 AND EMA9 is falling
- Price is below EMA9 (confirmed breakdown)
- Rejection bounce: price bounced UP to EMA9 in last 5 candles but closed back below it
- RSI < 45 (weak momentum, not oversold enough to snap back hard)
- Volume surge >= 1.5x on the rejection candle
- Daily trend filter: stock must be below daily EMA20 (macro downtrend)
- Entry: SHORT at current price
- SL: above recent swing high (structure-based + ATR buffer)
- T1: 1.5x risk below entry (quick cover)
- T2: 2.5x risk below entry (full target)
- Max hold: 16 candles (4 hours)
"""

import numpy as np
import pandas as pd
import logging
from .base_strategy import BaseStrategy, TradeSetup, Signal

logger = logging.getLogger(__name__)


class EMABreakdownStrategy(BaseStrategy):

    EMA_FAST = 9
    EMA_SLOW = 21
    MIN_VOL_MULT = 2.0     # Raised — need real volume spike on rejection (was 1.0)
    MIN_RR = 1.8           # Raised — shorts need better reward to justify risk (was 1.5)
    MAX_HOLD_CANDLES = 16
    T1_MULT = 1.5          # target_1 cover = entry - rps * T1_MULT
    T2_MULT = 2.5          # target_2 cover = entry - rps * T2_MULT
    NO_TRADE_BEFORE = "09:45"
    NO_TRADE_AFTER = "13:30"  # No new shorts after 1:30 PM — too close to close (was 14:45)

    @property
    def strategy_name(self) -> str:
        return "EMABreakdown"

    @property
    def required_timeframe(self) -> str:
        return "15m"

    def generate_signal(self, symbol, df_primary, df_daily,
                        regime_bullish, capital_per_trade, charges_estimate) -> TradeSetup:

        # Only short in bear/sideways — EMA Pullback handles bull markets
        if regime_bullish:
            return self._no_trade(symbol, "regime_bullish_no_shorts")

        if df_primary is None or len(df_primary) < 30:
            return self._no_trade(symbol, "insufficient_15m_data")

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

        # Bearish ribbon: EMA9 must be below EMA21
        if ema9[-1] >= ema21[-1]:
            return self._no_trade(symbol, "bullish_ribbon")

        # Price must be below EMA9 (confirmed breakdown, not just touching)
        if cur >= ema9[-1]:
            return self._no_trade(symbol, "price_above_ema9")

        # EMA9 must be falling for at least 5 candles (confirmed downtrend, not a blip)
        if ema9[-1] >= ema9[-5]:
            return self._no_trade(symbol, "ema9_not_falling_5c")

        # Daily trend filter: stock must be AT or BELOW its 20-day EMA — confirmed macro downtrend
        # Removed the old 5% buffer — that was allowing shorts on stocks still in uptrends
        if df_daily is not None and len(df_daily) >= 22:
            daily_c = df_daily["close"].values
            d_ema20 = self._ema(daily_c, 20)
            if daily_c[-1] > d_ema20[-1]:
                return self._no_trade(symbol, "above_daily_ema20_no_shorts")

        # Rejection: high touched EMA9 in last 5 candles (fresh setup only, not stale)
        rejection = (cur < ema9[-1]) and any(
            h[-i] >= ema9[-i] * 0.995 for i in range(2, 6) if i < len(h)
        )
        if not rejection:
            return self._no_trade(symbol, "no_rejection_5c")

        # Current close must be below EMA9 (rejection confirmed, not just approaching)
        if c[-1] >= ema9[-1]:
            return self._no_trade(symbol, "rejection_candle_above_ema9")

        rsi = self._rsi(c, 14)
        if rsi > 38:
            return self._no_trade(symbol, f"rsi_above_38_{rsi:.1f}")  # was 45, too loose
        if rsi < 15:
            return self._no_trade(symbol, f"rsi_extreme_{rsi:.1f}_possible_gap_down")

        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v))
        if v[-1] < avg_vol * self.MIN_VOL_MULT:
            return self._no_trade(symbol, f"low_volume_{v[-1]/avg_vol:.2f}x")

        atr = self._atr(h, l, c, 14)
        swing_high = float(np.max(h[-4:-1]))  # recent swing high for SL anchor
        vp = self._vol_profile(symbol, df_daily)
        vol_regime = vp.regime if vp else "normal"
        sl = self._dynamic_sl_short(cur, swing_high, atr, vol_regime)
        rps = sl - cur  # risk per share (positive)

        if rps <= 0.01:
            return self._no_trade(symbol, "invalid_sl")

        t1 = cur - rps * self.T1_MULT
        t2 = cur - rps * self.T2_MULT
        be = cur - rps * 1.0  # breakeven trigger (move SL to entry when T1 hit)
        qty = int(capital_per_trade / cur)

        if qty < 1:
            return self._no_trade(symbol, "insufficient_capital")

        # RR check: net reward to T2 vs risk
        net_reward = qty * (cur - t2) - charges_estimate
        net_rr = net_reward / max(qty * rps, 0.01)

        if net_rr < self.MIN_RR:
            return self._no_trade(symbol, f"rr_{net_rr:.2f}_below_{self.MIN_RR}")

        vspike = v[-1] / max(avg_vol, 1)
        quality = "A" if rsi < 30 and vspike >= 2.0 else "B" if rsi <= 45 else "C"

        return TradeSetup(
            signal=Signal.SHORT, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=round(t1, 2), target_2=round(t2, 2),
            breakeven_trigger=round(be, 2), trailing_step=round(atr * 0.5, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality,
            reason=f"ema_breakdown_rsi{rsi:.0f}_vol{vspike:.1f}x_sl_{vol_regime}",
            max_hold_candles=self.MAX_HOLD_CANDLES,
            strategy_name=self.strategy_name, is_valid=True,
        )


def _load_optimized_params():
    import json, os
    f = os.path.join(os.path.dirname(__file__), "..", "backtesting", "results", "best_params.json")
    if os.path.exists(f):
        try:
            params = json.loads(open(f).read()).get("ema_breakdown", {})
            for k, v in params.items():
                if hasattr(EMABreakdownStrategy, k):
                    setattr(EMABreakdownStrategy, k, v)
        except Exception:
            pass

_load_optimized_params()
