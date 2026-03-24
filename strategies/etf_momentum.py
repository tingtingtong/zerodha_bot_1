import numpy as np
import pandas as pd
import logging
from .base_strategy import BaseStrategy, TradeSetup, Signal

logger = logging.getLogger(__name__)
ETF_SYMBOLS = {"NIFTYBEES", "BANKBEES", "GOLDBEES", "JUNIORBEES", "ITBEES"}


class ETFMomentumStrategy(BaseStrategy):

    MIN_RR = 1.4
    MAX_HOLD_CANDLES = 20

    @property
    def strategy_name(self) -> str:
        return "ETFMomentum"

    @property
    def required_timeframe(self) -> str:
        return "15m"

    def generate_signal(self, symbol, df_primary, df_daily,
                        regime_bullish, capital_per_trade, charges_estimate) -> TradeSetup:

        if symbol not in ETF_SYMBOLS:
            return self._no_trade(symbol, "not_an_etf_symbol")
        if not regime_bullish:
            return self._no_trade(symbol, "regime_not_bullish")
        if df_primary is None or len(df_primary) < 25:
            return self._no_trade(symbol, "insufficient_data")
        if df_daily is None or len(df_daily) < 55:
            return self._no_trade(symbol, "insufficient_daily_data")

        daily_c = df_daily["close"].values
        d_ema50 = self._ema(daily_c, 50)
        if daily_c[-1] < d_ema50[-1]:
            return self._no_trade(symbol, "below_daily_50ema")

        c = df_primary["close"].values
        h = df_primary["high"].values
        l = df_primary["low"].values
        v = df_primary["volume"].values
        ema20 = self._ema(c, 20)
        cur = c[-1]

        near_ema = abs(cur - ema20[-1]) / ema20[-1] < 0.015  # 1.5% tolerance works across 15m/1h/1d
        bouncing = cur > ema20[-1] and l[-1] <= ema20[-1] * 1.008
        if not (near_ema or bouncing):
            return self._no_trade(symbol, "not_near_20ema")

        rsi = self._rsi(c, 14)
        if rsi < 48 or rsi > 72:
            return self._no_trade(symbol, f"rsi_out_of_range_{rsi:.1f}")

        atr = self._atr(h, l, c, 14)
        sl = ema20[-1] - atr * 0.8
        rps = cur - sl
        if rps <= 0.01:
            return self._no_trade(symbol, "invalid_sl")

        t1 = cur + rps * 1.6
        t2 = cur + rps * 2.8
        be = cur + rps * 1.0
        qty = int(capital_per_trade / cur)
        if qty < 1:
            return self._no_trade(symbol, "insufficient_capital")

        net_rr = (qty * (t2 - cur) - charges_estimate) / max(qty * rps, 0.01)
        if net_rr < self.MIN_RR:
            return self._no_trade(symbol, f"rr_{net_rr:.2f}_below_{self.MIN_RR}")

        avg_vol = float(np.mean(v[-20:-1])) if len(v) > 20 else float(np.mean(v))
        volume_spike = float(v[-1]) / max(avg_vol, 1)
        quality = "A" if (rsi > 60 and cur > ema20[-1] and volume_spike >= 1.5) else "B"
        return TradeSetup(
            signal=Signal.LONG, symbol=symbol,
            entry_price=round(cur, 2), stop_loss=round(sl, 2),
            target_1=round(t1, 2), target_2=round(t2, 2),
            breakeven_trigger=round(be, 2), trailing_step=round(atr * 0.4, 2),
            risk_amount=round(qty * rps, 2), reward_risk_ratio=round(net_rr, 2),
            setup_quality=quality, reason=f"etf_ema20_bounce_rsi{rsi:.0f}",
            max_hold_candles=self.MAX_HOLD_CANDLES,
            strategy_name=self.strategy_name, is_valid=True,
        )
