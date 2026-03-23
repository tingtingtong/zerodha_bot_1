import pytest
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.ema_pullback import EMAPullbackStrategy
from strategies.etf_momentum import ETFMomentumStrategy
from strategies.base_strategy import Signal


def make_bullish_df(n=50, start_price=2800.0, trend_up=True):
    """Create synthetic 15-min OHLCV with a bullish EMA setup."""
    prices = []
    p = start_price
    for i in range(n):
        change = np.random.normal(0.002 if trend_up else -0.001, 0.003)
        p = p * (1 + change)
        h = p * (1 + abs(np.random.normal(0, 0.003)))
        l = p * (1 - abs(np.random.normal(0, 0.003)))
        prices.append({
            "timestamp": datetime(2024, 3, 15, 9, 30) + timedelta(minutes=15 * i),
            "open": p * 0.999, "high": h, "low": l, "close": p,
            "volume": int(np.random.uniform(50000, 200000)),
        })
    return pd.DataFrame(prices)


def make_daily_df(n=120, start_price=2800.0):
    prices = []
    p = start_price
    for i in range(n):
        p = p * (1 + np.random.normal(0.0003, 0.01))
        prices.append({
            "timestamp": datetime(2023, 1, 1) + timedelta(days=i),
            "open": p * 0.999, "high": p * 1.01, "low": p * 0.99,
            "close": p, "volume": int(np.random.uniform(1e6, 5e6)),
        })
    return pd.DataFrame(prices)


def test_ema_pullback_no_trade_bad_regime():
    strat = EMAPullbackStrategy()
    df_15m = make_bullish_df()
    df_daily = make_daily_df()
    result = strat.generate_signal("RELIANCE", df_15m, df_daily,
                                    regime_bullish=False, capital_per_trade=8000,
                                    charges_estimate=50)
    assert result.signal == Signal.NO_TRADE
    assert result.rejection_reason == "regime_not_bullish"


def test_ema_pullback_insufficient_data():
    strat = EMAPullbackStrategy()
    df_small = make_bullish_df(n=10)
    df_daily = make_daily_df()
    result = strat.generate_signal("RELIANCE", df_small, df_daily,
                                    regime_bullish=True, capital_per_trade=8000,
                                    charges_estimate=50)
    assert result.signal == Signal.NO_TRADE


def test_etf_momentum_wrong_symbol():
    strat = ETFMomentumStrategy()
    df = make_bullish_df()
    result = strat.generate_signal("RELIANCE", df, make_daily_df(),
                                    regime_bullish=True, capital_per_trade=8000,
                                    charges_estimate=50)
    assert result.signal == Signal.NO_TRADE
    assert "not_an_etf" in result.rejection_reason


def test_setup_quality_is_valid_string():
    strat = EMAPullbackStrategy()
    df_15m = make_bullish_df(n=50)
    df_daily = make_daily_df()
    result = strat.generate_signal("RELIANCE", df_15m, df_daily,
                                    regime_bullish=True, capital_per_trade=8000,
                                    charges_estimate=50)
    if result.is_valid:
        assert result.setup_quality in ("A", "B", "C")


def test_trade_setup_risk_per_share():
    from strategies.base_strategy import TradeSetup, Signal
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
