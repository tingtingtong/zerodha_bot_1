"""
Global pre-market cues: Dow futures, S&P futures, GIFT Nifty.

GIFT Nifty (formerly SGX Nifty) is not available on Yahoo Finance.
We use Nifty spot (^NSEI) as a reference and note the limitation.
"""

import logging
import time
import pytz
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_TICKERS = {
    "dow_futures":  ("YM=F",   "Dow Fut"),
    "sp_futures":   ("ES=F",   "S&P Fut"),
    "nifty_spot":   ("^NSEI",  "Nifty"),
}

_cache: dict = {}          # key -> (result, fetched_at)
_CACHE_TTL = 15 * 60       # 15 minutes


def fetch_global_cues() -> dict:
    """
    Returns dict with keys: dow_futures, sp_futures, nifty_spot, gift_nifty_note.
    Each market entry: {"price": float, "change_pct": float, "label": str} or None on failure.
    """
    now_ts = time.time()
    cached, cached_at = _cache.get("global_cues", (None, 0))
    if cached is not None and (now_ts - cached_at) < _CACHE_TTL:
        logger.debug("[global_cues] cache hit")
        return cached

    import yfinance as yf
    result = {}

    for key, (ticker, label) in _TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True)
            if len(hist) >= 2:
                last  = float(hist["Close"].iloc[-1])
                prev  = float(hist["Close"].iloc[-2])
                chg   = round((last - prev) / prev * 100, 2) if prev > 0 else 0.0
                result[key] = {"price": last, "change_pct": chg, "label": label}
            else:
                result[key] = None
        except Exception as e:
            logger.warning(f"[global_cues] {ticker} failed: {e}")
            result[key] = None

    # GIFT Nifty is not on Yahoo Finance — surface a note for the pre-market brief
    result["gift_nifty_note"] = "GIFT Nifty: check NSE IFSC (not available via free API)"

    _cache["global_cues"] = (result, now_ts)
    logger.info(
        "[global_cues] "
        + "  ".join(
            f"{v['label']} {v['change_pct']:+.2f}%"
            for v in result.values()
            if isinstance(v, dict) and "change_pct" in v
        )
    )
    return result
