import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class StockScore:
    symbol: str
    total_score: float
    liquidity_score: float
    momentum_score: float
    trend_score: float
    volatility_score: float
    volume_score: float
    avg_daily_volume: int
    avg_daily_turnover: float
    atr_14: float
    atr_pct: float
    rsi_14: float
    price: float
    above_20ema: bool
    above_50ema: bool
    above_200ema: bool
    volume_spike: float
    has_upcoming_event: bool
    is_penny: bool
    is_illiquid: bool
    is_too_volatile: bool
    tradeable: bool
    rejection_reason: Optional[str] = None
    sector: str = ""
    sector_trend: str = "neutral"


class StockScreener:

    MIN_TURNOVER = 5_00_00_000
    MIN_VOLUME = 2_00_000
    MIN_PRICE = 50.0
    MAX_PRICE = 10_000.0
    MIN_DATA_DAYS = 60
    MIN_ATR_PCT = 0.5
    MAX_ATR_PCT = 5.0
    PREFERRED_TURNOVER = 20_00_00_000
    PREFERRED_MAX_ATR = 3.0

    W_LIQ, W_MOM, W_TREND, W_VOL, W_VSPIKE = 30, 25, 25, 10, 10

    def score(self, symbol: str, daily_df: pd.DataFrame,
              sector: str = "", sector_trend: str = "neutral",
              upcoming_event: bool = False) -> StockScore:

        if daily_df is None or len(daily_df) < self.MIN_DATA_DAYS:
            return self._reject(symbol, "insufficient_history", 0)

        c = daily_df["close"].values
        h = daily_df["high"].values
        l = daily_df["low"].values
        v = daily_df["volume"].values
        cur = float(c[-1])

        if cur < self.MIN_PRICE:
            return self._reject(symbol, "penny_stock", cur)
        if cur > self.MAX_PRICE:
            return self._reject(symbol, "price_too_high", cur)

        avg_vol = float(np.mean(v[-30:]))
        avg_to = avg_vol * cur

        if avg_vol < self.MIN_VOLUME:
            return self._reject(symbol, "illiquid_volume", cur)
        if avg_to < self.MIN_TURNOVER:
            return self._reject(symbol, "illiquid_turnover", cur)

        atr14 = self._atr(h, l, c, 14)
        atr_pct = (atr14 / cur) * 100

        if atr_pct < self.MIN_ATR_PCT:
            return self._reject(symbol, "too_flat", cur)
        if atr_pct > self.MAX_ATR_PCT:
            return self._reject(symbol, "too_volatile", cur)
        if upcoming_event:
            return self._reject(symbol, "upcoming_event", cur)

        rsi = self._rsi(c, 14)
        ema20 = self._ema(c, 20)
        ema50 = self._ema(c, 50)
        ema200 = self._ema(c, 200)

        avg_vol_20 = float(np.mean(v[-21:-1])) if len(v) > 21 else avg_vol
        vspike = float(v[-1]) / max(avg_vol_20, 1)

        liq = self.W_LIQ * min(avg_to / self.PREFERRED_TURNOVER, 1.0)
        mom = (self.W_MOM if 50 <= rsi <= 70
               else self.W_MOM * 0.55 if (40 <= rsi < 50 or 70 < rsi <= 78)
               else self.W_MOM * 0.15)
        trend = (10 if cur > ema200[-1] else 0) + (8 if cur > ema50[-1] else 0) + (7 if cur > ema20[-1] else 0)
        vol_score = self.W_VOL if 1.0 <= atr_pct <= self.PREFERRED_MAX_ATR else self.W_VOL * 0.5
        vspike_score = (self.W_VSPIKE if vspike >= 1.5
                        else self.W_VSPIKE * 0.65 if vspike >= 1.0
                        else self.W_VSPIKE * 0.25)

        return StockScore(
            symbol=symbol, total_score=round(liq + mom + trend + vol_score + vspike_score, 1),
            liquidity_score=round(liq, 1), momentum_score=round(mom, 1),
            trend_score=round(trend, 1), volatility_score=round(vol_score, 1),
            volume_score=round(vspike_score, 1),
            avg_daily_volume=int(avg_vol), avg_daily_turnover=avg_to,
            atr_14=round(atr14, 2), atr_pct=round(atr_pct, 2),
            rsi_14=round(rsi, 1), price=cur,
            above_20ema=cur > ema20[-1], above_50ema=cur > ema50[-1],
            above_200ema=cur > ema200[-1], volume_spike=round(vspike, 2),
            has_upcoming_event=upcoming_event, is_penny=False,
            is_illiquid=False, is_too_volatile=False, tradeable=True,
            sector=sector, sector_trend=sector_trend,
        )

    def _reject(self, symbol, reason, price):
        return StockScore(
            symbol=symbol, total_score=0, liquidity_score=0, momentum_score=0,
            trend_score=0, volatility_score=0, volume_score=0,
            avg_daily_volume=0, avg_daily_turnover=0, atr_14=0, atr_pct=0,
            rsi_14=50, price=price, above_20ema=False, above_50ema=False,
            above_200ema=False, volume_spike=0, has_upcoming_event=False,
            is_penny=False, is_illiquid=False, is_too_volatile=False,
            tradeable=False, rejection_reason=reason,
        )

    def _atr(self, h, l, c, n=14):
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        return float(np.mean(tr[-n:])) if len(tr) >= n else float(np.mean(tr)) if len(tr) else 0.5

    def _rsi(self, c, n=14):
        if len(c) < n + 1:
            return 50.0
        d = np.diff(c)
        ag = float(np.mean(np.where(d > 0, d, 0.0)[-n:]))
        al = float(np.mean(np.where(d < 0, -d, 0.0)[-n:]))
        return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))

    def _ema(self, data, n):
        a = 2 / (n + 1)
        e = np.zeros(len(data))
        e[0] = data[0]
        for i in range(1, len(data)):
            e[i] = a * data[i] + (1 - a) * e[i - 1]
        return e
