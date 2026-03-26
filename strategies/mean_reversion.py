"""
Mean Reversion Strategy — for WEAK_BEAR and SIDEWAYS market regimes.

Logic:
- Stock has pulled back hard (RSI oversold < 35, price below 20 EMA)
- Bounce attempt: current candle close > prev candle close AND volume surge
- Entry: current price (oversold bounce)
- SL: below recent swing low (1.5x ATR)
- T1: 1.2x risk (quick profit, short hold)
- T2: 20 EMA (mean reversion target)
- Max hold: 8 candles (2 hours) — tight, don't overstay

Works when EMA pullback won't fire (bearish/sideways regime).
"""

import numpy as np
import logging
from .base_strategy import BaseStrategy, TradeSetup, Signal

logger = logging.getLogger(__name__)


class MeanReversionStrategy(BaseStrategy):

    MIN_RR = 1.3          # Lower bar — moves are counter-trend, shorter
    MAX_HOLD_CANDLES = 8  # 2 hours max — get in, get out
    RSI_OVERSOLD = 45     # Entry only when oversold
    RSI_MAX = 50          # Don't enter if already recovered too much
    VOLUME_MIN = 1.3      # Need some volume to confirm bounce

    @property
    def strategy_name(self) -> str:
        return "MeanReversion"

    @property
    def required_timeframe(self) -> str:
        return "15m"

    def generate_signal(self, symbol, df_primary, df_daily,
                        regime_bullish, capital_per_trade, charges_estimate) -> TradeSetup:

        # No regime gate — mean reversion fires whenever RSI is oversold regardless of regime
        if df_primary is None or len(df_primary) < 30:
            return self._no_trade(symbol, "insufficient_data")
        if df_daily is None or len(df_daily) < 22:
            return self._no_trade(symbol, "insufficient_daily_data")

        c = df_primary["close"].values
        h = df_primary["high"].values
        l = df_primary["low"].values
        v = df_primary["volume"].values
        cur = c[-1]

        # ── Daily trend: stock shouldn't be in freefall ────────────────
        daily_c = df_daily["close"].values
        d_ema20 = self._ema(daily_c, 20)
        daily_drop_pct = (daily_c[-1] - daily_c[-5]) / daily_c[-5] * 100
        if daily_drop_pct < -5.0:
            return self._no_trade(symbol, f"daily_freefall_{daily_drop_pct:.1f}pct")

        # ── Intraday: price must be below 20 EMA (oversold condition) ──
        ema20 = self._ema(c, 20)
        if cur >= ema20[-1]:
            return self._no_trade(symbol, "not_oversold_above_ema20")

        # ── RSI oversold ───────────────────────────────────────────────
        rsi = self._rsi(c, 14)
        if rsi > self.RSI_OVERSOLD:
            return self._no_trade(symbol, f"rsi_not_oversold_{rsi:.1f}")
        if rsi < 15:
            return self._no_trade(symbol, f"rsi_extreme_{rsi:.1f}_possible_gap_down")

        # ── Bounce candle: current close > prev close (reversal attempt) ─
        if c[-1] <= c[-2]:
            return self._no_trade(symbol, "no_bounce_candle")

        # ── Volume confirmation ────────────────────────────────────────
        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v[:-1]))
        volume_spike = float(v[-1]) / max(avg_vol, 1)
        if volume_spike < self.VOLUME_MIN:
            return self._no_trade(symbol, f"volume_weak_{volume_spike:.2f}x")

        # ── Sizing ─────────────────────────────────────────────────────
        atr = self._atr(h, l, c, 14)
        swing_low = float(np.min(l[-5:]))  # recent swing low
        vp = self._vol_profile(symbol, df_daily)
        vol_regime = vp.regime if vp else "normal"
        sl = self._dynamic_sl(cur, swing_low, atr, vol_regime)
        rps = cur - sl
        if rps <= 0.01:
            return self._no_trade(symbol, "invalid_sl")

        # Target: T1 = quick 1.2x, T2 = mean (20 EMA)
        t1 = cur + rps * 1.2
        t2 = max(ema20[-1], cur + rps * 1.8)  # at least 1.8x, ideally EMA
        be = cur + rps * 0.8

        qty = int(capital_per_trade / cur)
        if qty < 1:
            return self._no_trade(symbol, "insufficient_capital")

        net_rr = (qty * (t2 - cur) - charges_estimate) / max(qty * rps, 0.01)
        if net_rr < self.MIN_RR:
            return self._no_trade(symbol, f"rr_{net_rr:.2f}_below_{self.MIN_RR}")

        quality = "A" if (rsi < 30 and volume_spike >= 2.0) else "B"

        return TradeSetup(
            signal=Signal.LONG, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=round(t1, 2), target_2=round(t2, 2),
            breakeven_trigger=round(be, 2), trailing_step=round(atr * 0.3, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality,
            reason=f"mean_rev_rsi{rsi:.0f}_vol{volume_spike:.1f}x_sl_{vol_regime}",
            max_hold_candles=self.MAX_HOLD_CANDLES,
            strategy_name=self.strategy_name, is_valid=True,
        )
