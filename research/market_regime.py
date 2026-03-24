import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    STRONG_BULL = "strong_bull"
    WEAK_BULL = "weak_bull"
    SIDEWAYS = "sideways"
    WEAK_BEAR = "weak_bear"
    STRONG_BEAR = "strong_bear"
    HIGH_VOLATILITY = "high_volatility"


@dataclass
class RegimeResult:
    regime: MarketRegime
    confidence: float
    above_200ema: bool
    above_50ema: bool
    above_20ema: bool
    ema50_slope_pct: float
    adx: float
    vix_level: float
    breadth_score: float
    adv_dec_ratio: float
    recommendation: str
    summary: str


class MarketRegimeDetector:

    VIX_HALT = 28.0
    VIX_EXTREME = 35.0
    BREADTH_BULL = 60.0
    BREADTH_BEAR = 40.0
    ADX_TRENDING = 25.0
    EMA_SLOPE_BULL = 0.08
    EMA_SLOPE_BEAR = -0.08

    def detect(self, nifty_daily: pd.DataFrame, vix: float = 15.0,
               breadth_pct: float = 55.0, advancing: int = 1000,
               declining: int = 500) -> RegimeResult:

        if len(nifty_daily) < 210:
            return RegimeResult(
                regime=MarketRegime.SIDEWAYS, confidence=0.3,
                above_200ema=False, above_50ema=False, above_20ema=False,
                ema50_slope_pct=0, adx=20, vix_level=vix,
                breadth_score=breadth_pct, adv_dec_ratio=1.0,
                recommendation="stay_flat", summary="insufficient_data",
            )

        c = nifty_daily["close"].values
        ema20 = self._ema(c, 20)
        ema50 = self._ema(c, 50)
        ema200 = self._ema(c, 200)
        cur = c[-1]

        above_200 = cur > ema200[-1]
        above_50 = cur > ema50[-1]
        above_20 = cur > ema20[-1]
        slope = (ema50[-1] - ema50[-10]) / ema50[-10] * 100 if ema50[-10] > 0 else 0
        adx = self._adx(nifty_daily)
        adv_dec = advancing / max(declining, 1)

        if vix >= self.VIX_EXTREME:
            return RegimeResult(
                regime=MarketRegime.HIGH_VOLATILITY, confidence=0.95,
                above_200ema=above_200, above_50ema=above_50, above_20ema=above_20,
                ema50_slope_pct=round(slope, 3), adx=round(adx, 1),
                vix_level=vix, breadth_score=breadth_pct, adv_dec_ratio=round(adv_dec, 2),
                recommendation="stay_flat", summary=f"extreme_vix_{vix:.1f}",
            )

        if above_200 and above_50 and slope > self.EMA_SLOPE_BULL:
            regime = MarketRegime.STRONG_BULL if (adx > self.ADX_TRENDING and breadth_pct > self.BREADTH_BULL) else MarketRegime.WEAK_BULL
            conf = 0.82 if regime == MarketRegime.STRONG_BULL else 0.62
            rec = "trade_long" if vix < self.VIX_HALT else "stay_flat"
        elif not above_200 and not above_50 and slope < self.EMA_SLOPE_BEAR:
            regime = MarketRegime.STRONG_BEAR if (adx > self.ADX_TRENDING and breadth_pct < self.BREADTH_BEAR) else MarketRegime.WEAK_BEAR
            conf = 0.82 if regime == MarketRegime.STRONG_BEAR else 0.62
            # STRONG_BEAR = stay flat; WEAK_BEAR = allow cautious longs if VIX permits
            rec = "stay_flat" if regime == MarketRegime.STRONG_BEAR else "trade_long"
        else:
            regime, conf, rec = MarketRegime.SIDEWAYS, 0.70, "stay_flat"

        if vix >= self.VIX_HALT:
            rec = "stay_flat"

        return RegimeResult(
            regime=regime, confidence=conf,
            above_200ema=above_200, above_50ema=above_50, above_20ema=above_20,
            ema50_slope_pct=round(slope, 3), adx=round(adx, 1),
            vix_level=vix, breadth_score=breadth_pct, adv_dec_ratio=round(adv_dec, 2),
            recommendation=rec,
            summary=f"{regime.value}_adx{adx:.0f}_vix{vix:.1f}",
        )

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        a = 2 / (period + 1)
        e = np.zeros(len(data))
        e[0] = data[0]
        for i in range(1, len(data)):
            e[i] = a * data[i] + (1 - a) * e[i - 1]
        return e

    def _adx(self, df: pd.DataFrame, period: int = 14) -> float:
        try:
            h, l, c = df["high"].values, df["low"].values, df["close"].values
            if len(c) < period + 5:
                return 20.0
            tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
            pdm = np.where((h[1:] - h[:-1]) > (l[:-1] - l[1:]), np.maximum(h[1:] - h[:-1], 0), 0)
            mdm = np.where((l[:-1] - l[1:]) > (h[1:] - h[:-1]), np.maximum(l[:-1] - l[1:], 0), 0)

            def ws(arr, n):
                """Wilder smoothing: seed = mean of first n, then r[i] = r[i-1] + (arr[i]-r[i-1])/n"""
                r = np.zeros(len(arr))
                r[n - 1] = arr[:n].mean()
                for i in range(n, len(arr)):
                    r[i] = r[i - 1] + (arr[i] - r[i - 1]) / n
                return r

            atr14 = ws(tr, period)
            pdi = 100 * ws(pdm, period) / np.maximum(atr14, 1e-9)
            mdi = 100 * ws(mdm, period) / np.maximum(atr14, 1e-9)
            dx = 100 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-9)
            adx_arr = ws(dx[period - 1:], period)
            return float(adx_arr[-1]) if len(adx_arr) > 0 else 20.0
        except Exception:
            return 20.0
