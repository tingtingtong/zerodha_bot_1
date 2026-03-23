import numpy as np
import pandas as pd
import logging
from .base_strategy import BaseStrategy, TradeSetup, Signal

logger = logging.getLogger(__name__)


class EMAPullbackStrategy(BaseStrategy):

    EMA_FAST = 9
    EMA_SLOW = 21
    MIN_VOL_MULT = 1.2
    MIN_RR = 1.8
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

        if not regime_bullish:
            return self._no_trade(symbol, "regime_not_bullish")
        if df_primary is None or len(df_primary) < 30:
            return self._no_trade(symbol, "insufficient_15m_data")

        ts = df_primary.iloc[-1].get("timestamp")
        if ts is not None:
            try:
                ts_str = pd.Timestamp(ts).strftime("%H:%M")
                if ts_str < self.NO_TRADE_BEFORE or ts_str > self.NO_TRADE_AFTER:
                    return self._no_trade(symbol, f"outside_window_{ts_str}")
            except Exception:
                pass

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

        pullback = any(l[-i] <= ema9[-i] * 1.003 for i in range(2, 5) if i < len(l))
        if not pullback:
            return self._no_trade(symbol, "no_pullback")

        rsi = self._rsi(c, 14)
        if rsi < 50:
            return self._no_trade(symbol, f"rsi_below_50_{rsi:.1f}")

        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v))
        if v[-1] < avg_vol * self.MIN_VOL_MULT:
            return self._no_trade(symbol, f"low_volume_{v[-1]/avg_vol:.2f}x")

        atr = self._atr(h, l, c, 14)
        pullback_low = float(np.min(l[-4:-1]))
        sl = max(pullback_low - atr * 0.3, cur - atr * 1.5)
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
        quality = "A" if rsi > 60 and vspike >= 1.5 else "B" if rsi >= 55 else "C"

        return TradeSetup(
            signal=Signal.LONG, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=round(t1, 2), target_2=round(t2, 2),
            breakeven_trigger=round(be, 2), trailing_step=round(atr * 0.5, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality,
            reason=f"ema_pullback_rsi{rsi:.0f}_vol{vspike:.1f}x",
            max_hold_candles=self.MAX_HOLD_CANDLES,
            strategy_name=self.strategy_name, is_valid=True,
        )
