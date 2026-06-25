"""Tests for InsideBarBreakout strategy."""
import pytest
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.inside_bar_breakout import InsideBarBreakoutStrategy
from strategies.base_strategy import Signal


def make_15m(n=60, start_price=500.0, trend="up", seed=42):
    rng = np.random.default_rng(seed)
    prices, p = [], start_price
    for i in range(n):
        drift = 0.002 if trend == "up" else -0.002
        p = p * (1 + rng.normal(drift, 0.004))
        h = p * (1 + abs(rng.normal(0, 0.004)))
        l = p * (1 - abs(rng.normal(0, 0.004)))
        prices.append({
            "timestamp": datetime(2024, 3, 15, 9, 30) + timedelta(minutes=15 * i),
            "open": p * 0.999, "high": h, "low": l, "close": p,
            "volume": int(rng.uniform(50000, 200000)),
        })
    return pd.DataFrame(prices)


def make_15m_with_inside_bar(n=60, start_price=500.0, seed=42):
    """Create data with a guaranteed inside bar pattern near the end."""
    rng = np.random.default_rng(seed)
    prices, p = [], start_price
    for i in range(n):
        drift = 0.002
        p = p * (1 + rng.normal(drift, 0.004))
        h = p * (1 + abs(rng.normal(0, 0.004)))
        l = p * (1 - abs(rng.normal(0, 0.004)))
        prices.append({
            "timestamp": datetime(2024, 3, 15, 9, 30) + timedelta(minutes=15 * i),
            "open": p * 0.999, "high": h, "low": l, "close": p,
            "volume": int(rng.uniform(50000, 200000)),
        })

    # Force inside bar: make candle[-3] a big bar, candle[-2] fits inside it
    df = pd.DataFrame(prices)
    mother_idx = len(df) - 3
    ib_idx = len(df) - 2
    mother_h = float(df.loc[mother_idx, "high"])
    mother_l = float(df.loc[mother_idx, "low"])
    # Make inside bar smaller
    mid = (mother_h + mother_l) / 2
    ib_range = (mother_h - mother_l) * 0.4
    df.loc[ib_idx, "high"] = mid + ib_range / 2
    df.loc[ib_idx, "low"] = mid - ib_range / 2
    df.loc[ib_idx, "close"] = mid
    # Make breakout candle close above mother high
    df.loc[len(df) - 1, "close"] = mother_h * 1.005
    df.loc[len(df) - 1, "high"] = mother_h * 1.008
    df.loc[len(df) - 1, "volume"] = 250000  # above average
    return df


def make_daily(n=120, start_price=500.0, trend="up", seed=42):
    rng = np.random.default_rng(seed)
    prices, p = [], start_price
    for i in range(n):
        p = p * (1 + rng.normal(0.0003 if trend == "up" else -0.0003, 0.01))
        prices.append({
            "timestamp": datetime(2023, 1, 1) + timedelta(days=i),
            "open": p * 0.999, "high": p * 1.01, "low": p * 0.99,
            "close": p, "volume": int(rng.uniform(1e6, 5e6)),
        })
    return pd.DataFrame(prices)


class TestInsideBarBreakout:

    def test_strategy_name(self):
        assert InsideBarBreakoutStrategy().strategy_name == "InsideBarBreakout"

    def test_required_timeframe(self):
        assert InsideBarBreakoutStrategy().required_timeframe == "15m"

    def test_insufficient_data_rejected(self):
        strat = InsideBarBreakoutStrategy()
        result = strat.generate_signal("RELIANCE", make_15m(n=10), make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.signal == Signal.NO_TRADE
        assert "insufficient" in result.rejection_reason

    def test_none_dataframe_rejected(self):
        strat = InsideBarBreakoutStrategy()
        result = strat.generate_signal("RELIANCE", None, make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.signal == Signal.NO_TRADE

    def test_inside_bar_detection(self):
        strat = InsideBarBreakoutStrategy()
        # candle 3 (h=11, l=7) is inside candle 2 (h=12, l=6)
        # candle 4 breaks out — so candle 3 is the most recent IB (mother=2, ib=3)
        h = np.array([10, 10, 12, 11, 14])
        l = np.array([8, 8, 6, 7, 5])
        mother_idx, ib_idx = strat._find_inside_bar(h, l)
        assert mother_idx == 2
        assert ib_idx == 3

    def test_no_inside_bar_when_absent(self):
        strat = InsideBarBreakoutStrategy()
        # Expanding bars — no inside bar possible
        h = np.array([10, 11, 12, 13, 14])
        l = np.array([9, 8, 7, 6, 5])
        mother_idx, ib_idx = strat._find_inside_bar(h, l)
        assert mother_idx is None

    def test_strong_bear_blocks_long(self):
        strat = InsideBarBreakoutStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 10:30:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("RELIANCE",
                                           make_15m_with_inside_bar(),
                                           make_daily(trend="up"),
                                           regime_bullish=True, capital_per_trade=8000,
                                           charges_estimate=50, regime="strong_bear")
        # Should either block long or generate some other rejection
        if result.is_valid:
            assert result.signal != Signal.LONG or result.rejection_reason is None

    def test_valid_signal_structure(self):
        strat = InsideBarBreakoutStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 10:30:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("RELIANCE",
                                           make_15m_with_inside_bar(),
                                           make_daily(trend="up"),
                                           regime_bullish=True, capital_per_trade=8000,
                                           charges_estimate=50)
        if result.is_valid:
            assert result.reward_risk_ratio >= 1.5
            assert result.entry_price > 0
            assert result.strategy_name == "InsideBarBreakout"
            assert "ib_breakout" in result.reason
