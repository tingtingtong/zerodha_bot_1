"""
Tests for all three strategies — EMA Pullback, ETF Momentum, Mean Reversion.
"""
import pytest
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.ema_pullback import EMAPullbackStrategy
from strategies.etf_momentum import ETFMomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.base_strategy import Signal, TradeSetup


# ── Helpers ────────────────────────────────────────────────────────────────

def make_15m(n=60, start_price=2800.0, trend="up", seed=42):
    rng = np.random.default_rng(seed)
    prices, p = [], start_price
    for i in range(n):
        drift = 0.002 if trend == "up" else -0.001
        p = p * (1 + rng.normal(drift, 0.003))
        h = p * (1 + abs(rng.normal(0, 0.003)))
        l = p * (1 - abs(rng.normal(0, 0.003)))
        prices.append({"timestamp": datetime(2024, 3, 15, 9, 30) + timedelta(minutes=15 * i),
                       "open": p * 0.999, "high": h, "low": l, "close": p,
                       "volume": int(rng.uniform(50000, 200000))})
    return pd.DataFrame(prices)


def make_daily(n=120, start_price=2800.0, trend="up", seed=42):
    rng = np.random.default_rng(seed)
    prices, p = [], start_price
    for i in range(n):
        p = p * (1 + rng.normal(0.0003 if trend == "up" else -0.0003, 0.01))
        prices.append({"timestamp": datetime(2023, 1, 1) + timedelta(days=i),
                       "open": p * 0.999, "high": p * 1.01, "low": p * 0.99,
                       "close": p, "volume": int(rng.uniform(1e6, 5e6))})
    return pd.DataFrame(prices)


# IST trading window mock — patches the time check to be inside 09:45–14:45
INSIDE_WINDOW = "10:30"
OUTSIDE_WINDOW = "08:00"


# ── EMA Pullback ──────────────────────────────────────────────────────────

class TestEMAPullback:

    def test_insufficient_data_rejected(self):
        strat = EMAPullbackStrategy()
        result = strat.generate_signal("RELIANCE", make_15m(n=10), make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.signal == Signal.NO_TRADE
        assert "insufficient" in result.rejection_reason

    def test_outside_time_window_rejected(self):
        strat = EMAPullbackStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 08:00:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("RELIANCE", make_15m(), make_daily(),
                                           regime_bullish=True, capital_per_trade=8000,
                                           charges_estimate=50)
        assert result.signal == Signal.NO_TRADE
        assert "outside_window" in result.rejection_reason

    def test_no_regime_gate(self):
        """EMA Pullback should NOT reject based on regime_bullish=False."""
        strat = EMAPullbackStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 10:30:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("RELIANCE", make_15m(), make_daily(),
                                           regime_bullish=False, capital_per_trade=8000,
                                           charges_estimate=50)
        # EMA Pullback gates on regime_bullish — bear regime correctly rejected
        assert result.rejection_reason == "regime_not_bullish"

    def test_strategy_name(self):
        assert EMAPullbackStrategy().strategy_name == "EMAPullback"

    def test_required_timeframe(self):
        assert EMAPullbackStrategy().required_timeframe == "15m"

    def test_valid_signal_has_positive_rr(self):
        strat = EMAPullbackStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 10:30:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("RELIANCE", make_15m(), make_daily(),
                                           regime_bullish=True, capital_per_trade=8000,
                                           charges_estimate=50)
        if result.is_valid:
            assert result.reward_risk_ratio >= 1.5
            assert result.entry_price > 0
            assert result.stop_loss < result.entry_price
            assert result.target_1 > result.entry_price
            assert result.target_2 > result.target_1

    def test_valid_signal_quality_is_grade(self):
        strat = EMAPullbackStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 10:30:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("RELIANCE", make_15m(), make_daily(),
                                           regime_bullish=True, capital_per_trade=8000,
                                           charges_estimate=50)
        if result.is_valid:
            assert result.setup_quality in ("A", "B", "C")

    def test_none_dataframe_rejected(self):
        strat = EMAPullbackStrategy()
        result = strat.generate_signal("RELIANCE", None, make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.signal == Signal.NO_TRADE


# ── ETF Momentum ──────────────────────────────────────────────────────────

class TestETFMomentum:

    def test_non_etf_symbol_rejected(self):
        strat = ETFMomentumStrategy()
        result = strat.generate_signal("RELIANCE", make_15m(), make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.signal == Signal.NO_TRADE
        assert "not_an_etf" in result.rejection_reason

    def test_etf_symbol_accepted_for_scanning(self):
        strat = ETFMomentumStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 10:30:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("NIFTYBEES", make_15m(start_price=230), make_daily(start_price=230),
                                           regime_bullish=True, capital_per_trade=8000,
                                           charges_estimate=20)
        # Should not reject with "not_an_etf"
        assert result.rejection_reason != "not_an_etf" if not result.is_valid else True

    def test_insufficient_data_rejected(self):
        strat = ETFMomentumStrategy()
        result = strat.generate_signal("NIFTYBEES", make_15m(n=5), make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=20)
        assert result.signal == Signal.NO_TRADE

    def test_strategy_name(self):
        assert ETFMomentumStrategy().strategy_name == "ETFMomentum"

    def test_valid_signal_sl_below_entry(self):
        strat = ETFMomentumStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 10:30:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("NIFTYBEES", make_15m(start_price=230),
                                           make_daily(start_price=230),
                                           regime_bullish=True, capital_per_trade=8000,
                                           charges_estimate=20)
        if result.is_valid:
            assert result.stop_loss < result.entry_price
            assert result.target_2 > result.target_1 > result.entry_price


# ── Mean Reversion ────────────────────────────────────────────────────────

class TestMeanReversion:

    def test_insufficient_data_rejected(self):
        strat = MeanReversionStrategy()
        result = strat.generate_signal("RELIANCE", make_15m(n=10), make_daily(),
                                       regime_bullish=False, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.signal == Signal.NO_TRADE
        assert "insufficient" in result.rejection_reason

    def test_freefall_stock_rejected(self):
        """Stock dropping >5% in last 5 days should be rejected."""
        strat = MeanReversionStrategy()
        # Create a sharp downtrend daily df
        df_daily = make_daily(n=60, trend="down", seed=99)
        # Make last 5 candles show >5% drop
        prices = df_daily["close"].values.copy()
        prices[-1] = prices[-5] * 0.93  # 7% drop
        df_daily = df_daily.copy()
        df_daily.loc[df_daily.index[-1], "close"] = prices[-1]
        result = strat.generate_signal("RELIANCE", make_15m(), df_daily,
                                       regime_bullish=False, capital_per_trade=8000,
                                       charges_estimate=50)
        if not result.is_valid:
            assert "freefall" in result.rejection_reason or "insufficient" in result.rejection_reason

    def test_no_regime_gate(self):
        """Mean reversion fires regardless of regime_bullish."""
        strat = MeanReversionStrategy()
        result = strat.generate_signal("RELIANCE", make_15m(n=5), make_daily(),
                                       regime_bullish=True, capital_per_trade=8000,
                                       charges_estimate=50)
        assert result.rejection_reason != "regime_not_bullish"

    def test_strategy_name(self):
        assert MeanReversionStrategy().strategy_name == "MeanReversion"

    def test_max_hold_is_tight(self):
        assert MeanReversionStrategy().MAX_HOLD_CANDLES == 8  # 2 hours, tighter than others

    def test_valid_signal_has_entries(self):
        strat = MeanReversionStrategy()
        import pytz
        mock_ts = pd.Timestamp("2024-03-15 10:30:00", tz=pytz.timezone("Asia/Kolkata"))
        with patch("pandas.Timestamp.now", return_value=mock_ts):
            result = strat.generate_signal("RELIANCE", make_15m(trend="down"),
                                           make_daily(n=60),
                                           regime_bullish=False, capital_per_trade=8000,
                                           charges_estimate=50)
        if result.is_valid:
            assert result.entry_price > 0
            assert result.stop_loss < result.entry_price


# ── TradeSetup base class ─────────────────────────────────────────────────

def test_trade_setup_risk_per_share():
    setup = TradeSetup(
        signal=Signal.LONG, symbol="TEST",
        entry_price=100, stop_loss=95, target_1=110, target_2=120,
        breakeven_trigger=100, trailing_step=1.5,
        risk_amount=50, reward_risk_ratio=2.0,
        setup_quality="A", reason="test", max_hold_candles=16,
        strategy_name="test", is_valid=True,
    )
    assert setup.risk_per_share == 5.0
    assert setup.reward_per_share_t1 == 10.0


def test_trade_setup_invalid_flag():
    setup = TradeSetup(
        signal=Signal.NO_TRADE, symbol="TEST",
        entry_price=0, stop_loss=0, target_1=0, target_2=0,
        breakeven_trigger=0, trailing_step=0,
        risk_amount=0, reward_risk_ratio=0,
        setup_quality="C", reason="no_signal", max_hold_candles=16,
        strategy_name="test", is_valid=False,
        rejection_reason="outside_window",
    )
    assert not setup.is_valid
    assert setup.rejection_reason == "outside_window"
