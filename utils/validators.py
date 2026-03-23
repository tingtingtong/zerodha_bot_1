import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


def validate_ohlcv(df: pd.DataFrame, symbol: str = "") -> tuple:
    if df is None or len(df) == 0:
        return False, "empty_dataframe"
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        return False, f"missing_columns_{missing}"
    df.dropna(subset=["close"], inplace=True)
    if len(df) < 5:
        return False, "too_few_rows"
    if (df["high"] < df["low"]).any():
        return False, "high_lt_low"
    if (df["volume"] < 0).any():
        return False, "negative_volume"
    pct = df["close"].pct_change().abs()
    if (pct > 0.5).any():
        logger.warning(f"{symbol}: candle with >50% move detected — verify data")
    return True, "ok"


def validate_price(price: float, symbol: str = "", label: str = "price") -> bool:
    if price is None or price <= 0 or np.isnan(price) or np.isinf(price):
        logger.error(f"Invalid {label} for {symbol}: {price}")
        return False
    return True


def validate_quantity(qty: int) -> bool:
    return isinstance(qty, int) and qty > 0
