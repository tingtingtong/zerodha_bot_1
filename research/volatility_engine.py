import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class VolatilityProfile:
    symbol: str
    atr_14: float
    atr_pct: float
    historical_vol_20d: float
    avg_range_pct: float
    is_expanding: bool
    regime: str
    suggested_sl_pct: float


class VolatilityEngine:

    def profile(self, symbol: str, df: pd.DataFrame) -> Optional[VolatilityProfile]:
        if df is None or len(df) < 20:
            return None
        h, l, c = df["high"].values, df["low"].values, df["close"].values
        cur = float(c[-1])

        atr14 = self._atr(h, l, c, 14)
        atr5 = self._atr(h, l, c, 5)
        atr_pct = (atr14 / cur) * 100 if cur > 0 else 0

        returns = np.diff(np.log(c[-22:]))
        hv_20d = float(np.std(returns) * np.sqrt(252) * 100) if len(returns) >= 5 else 0
        avg_range = float(np.mean((h[-20:] - l[-20:]) / c[-20:] * 100))
        expanding = atr5 > atr14 * 1.1

        if atr_pct < 0.8:
            regime = "low"
        elif atr_pct <= 2.5:
            regime = "normal"
        elif atr_pct <= 4.0:
            regime = "elevated"
        else:
            regime = "extreme"

        return VolatilityProfile(
            symbol=symbol, atr_14=round(atr14, 2), atr_pct=round(atr_pct, 2),
            historical_vol_20d=round(hv_20d, 2), avg_range_pct=round(avg_range, 2),
            is_expanding=expanding, regime=regime,
            suggested_sl_pct=round((atr14 * 1.2 / cur) * 100 if cur > 0 else 1.0, 2),
        )

    def _atr(self, h, l, c, n=14):
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        return float(np.mean(tr[-n:])) if len(tr) >= n else float(np.mean(tr)) if len(tr) else 0.5
