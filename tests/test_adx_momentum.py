"""Tests for ADXMomentum strategy."""
import pytest
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.adx_momentum import ADXMomentumStrategy
from strategies.base_strategy import Signal


def make_15m(n=60, start_price=500.0, trend="up", seed=42):
    rng = np.random.default_rng(seed)
    prices, p = [], start_price
    for i in range(n):
        drift = 0.003 if trend == "up" else -0.003
        p = p * (1 + rng.normal(drift, 0.005))
        h = p * (1 + abs(rng.normal(0, 0.005)))
        l = p * (1 - abs(rng.normal(0, 0.005)))
        prices.append({
            "timestamp": datetime(2024, 3, 15, 9, 30) + timedelta(minutes=15 * i),
            "open": p * 0.999, "high": h, "low": l, "close": p,
            "volume": int(rng.uniform(50000, 200000)),
        })
    return pd.DataFrame(prices)


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


class TestADXMomentum:

    def test_strategy_name(self):
        assert ADXMomentumStrategy().strategy_name == "ADXMomentum"

    def test_required_timeframe(self):
        assert ADXMomentumStrategy().required_timeframe == "15m"

    def test_insufficient_data_rejected(self):
        strat = ADXMomentumStrategy()
        result = strat.generate_signal("RELIANCE", make_15m(n=10), make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.signal == Signal.NO_TRADE
        assert "insufficient" in result.rejection_reason

    def test_none_dataframe_rejected(self):
        strat = ADXMomentumStrategy()
        result = strat.generate_signal("RELIANCE", None, make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.signal == Signal.NO_TRADE

    def test_sideways_regime_blocked(self):
        strat = ADXMomentumStrategy()
        result = strat.generate_signal("RELIANCE", make_15m(), make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50, regime="sideways")
        assert result.signal == Signal.NO_TRADE
        assert "regime_blocked" in result.rejection_reason

    def test_insufficient_daily_data_rejected(self):
        strat = ADXMomentumStrategy()
        result = strat.generate_signal("RELIANCE", make_15m(), make_daily(n=10),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.signal == Signal.NO_TRADE
        assert "insufficient_daily" in result.rejection_reason

    def test_adx_calculation_returns_arrays(self):
        strat = ADXMomentumStrategy()
        df = make_15m(n=60)
        h = df["high"].values
        l = df["low"].values
        c = df["close"].values
        adx, plus_di, minus_di = strat._calc_adx(h, l, c, 14)
        assert len(adx) == 60
        assert len(plus_di) == 60
        assert len(minus_di) == 60

    def test_adx_too_short_data(self):
        strat = ADXMomentumStrategy()
        h = np.array([10, 11, 12])
        l = np.array([9, 10, 11])
        c = np.array([9.5, 10.5, 11.5])
        adx, plus_di, minus_di = strat._calc_adx(h, l, c, 14)
        assert all(np.isnan(adx))

    def test_valid_signal_has_positive_rr(self):
        strat = ADXMomentumStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 10:30:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("RELIANCE",
                                           make_15m(n=60, trend="up"),
                                           make_daily(trend="up"),
                                           regime_bullish=True, capital_per_trade=8000,
                                           charges_estimate=50)
        if result.is_valid:
            assert result.reward_risk_ratio >= 1.5
            assert result.entry_price > 0
            assert result.strategy_name == "ADXMomentum"
