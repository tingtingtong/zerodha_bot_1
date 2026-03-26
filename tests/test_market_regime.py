"""
Tests for MarketRegimeDetector — regime classification from NIFTY daily data.
"""
import pytest
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from research.market_regime import MarketRegimeDetector, MarketRegime


def make_nifty_daily(n=220, start=22000, trend="up", seed=42):
    rng = np.random.default_rng(seed)
    prices, p = [], float(start)
    for i in range(n):
        drift = 0.0005 if trend == "up" else -0.0005
        p = p * (1 + rng.normal(drift, 0.008))
        prices.append({
            "timestamp": datetime(2023, 1, 1) + timedelta(days=i),
            "open": p * 0.999, "high": p * 1.01,
            "low": p * 0.99, "close": p,
            "volume": int(rng.uniform(3e8, 8e8)),
        })
    return pd.DataFrame(prices)


@pytest.fixture
def detector():
    return MarketRegimeDetector()


# ── Insufficient data ─────────────────────────────────────────────────────

def test_insufficient_data_returns_stay_flat(detector):
    small_df = make_nifty_daily(n=50)
    result = detector.detect(small_df, vix=15)
    assert result.recommendation == "stay_flat"
    assert result.summary == "insufficient_data"


def test_insufficient_data_low_confidence(detector):
    small_df = make_nifty_daily(n=50)
    result = detector.detect(small_df, vix=15)
    assert result.confidence < 0.5


# ── VIX extremes ──────────────────────────────────────────────────────────

def test_extreme_vix_returns_high_volatility(detector):
    df = make_nifty_daily(n=220, trend="up")
    result = detector.detect(df, vix=36)
    assert result.regime == MarketRegime.HIGH_VOLATILITY
    assert result.recommendation == "stay_flat"


def test_vix_halt_threshold_returns_stay_flat(detector):
    df = make_nifty_daily(n=220, trend="up")
    result = detector.detect(df, vix=29)
    assert result.recommendation == "stay_flat"


def test_normal_vix_allows_trading(detector):
    df = make_nifty_daily(n=220, trend="up")
    result = detector.detect(df, vix=14)
    assert result.recommendation != "stay_flat" or result.regime in (
        MarketRegime.SIDEWAYS, MarketRegime.STRONG_BEAR)


# ── Regime detection ──────────────────────────────────────────────────────

def test_strong_uptrend_detected_as_bull(detector):
    df = make_nifty_daily(n=220, trend="up", seed=1)
    result = detector.detect(df, vix=14, breadth_pct=65, advancing=1200, declining=300)
    assert result.regime in (MarketRegime.STRONG_BULL, MarketRegime.WEAK_BULL)


def test_strong_downtrend_detected_as_bear(detector):
    df = make_nifty_daily(n=220, trend="down", seed=2)
    result = detector.detect(df, vix=14, breadth_pct=30, advancing=300, declining=1200)
    assert result.regime in (MarketRegime.STRONG_BEAR, MarketRegime.WEAK_BEAR,
                              MarketRegime.SIDEWAYS)


def test_bull_regime_recommends_trade(detector):
    df = make_nifty_daily(n=220, trend="up", seed=3)
    result = detector.detect(df, vix=14, breadth_pct=65, advancing=1200, declining=300)
    if result.regime in (MarketRegime.STRONG_BULL, MarketRegime.WEAK_BULL):
        assert result.recommendation == "trade_long"


def test_strong_bear_recommends_stay_flat(detector):
    df = make_nifty_daily(n=220, trend="down", seed=4)
    result = detector.detect(df, vix=14, breadth_pct=25, advancing=200, declining=1400)
    if result.regime == MarketRegime.STRONG_BEAR:
        assert result.recommendation == "stay_flat"


# ── Result fields ─────────────────────────────────────────────────────────

def test_result_has_all_fields(detector):
    df = make_nifty_daily(n=220)
    result = detector.detect(df, vix=15)
    assert hasattr(result, "regime")
    assert hasattr(result, "confidence")
    assert hasattr(result, "above_200ema")
    assert hasattr(result, "above_50ema")
    assert hasattr(result, "ema50_slope_pct")
    assert hasattr(result, "adx")
    assert hasattr(result, "recommendation")
    assert hasattr(result, "summary")


def test_confidence_between_0_and_1(detector):
    df = make_nifty_daily(n=220)
    result = detector.detect(df, vix=15)
    assert 0 <= result.confidence <= 1


def test_recommendation_is_valid_string(detector):
    df = make_nifty_daily(n=220)
    result = detector.detect(df, vix=15)
    assert result.recommendation in ("trade_long", "stay_flat", "trade_cautious")
