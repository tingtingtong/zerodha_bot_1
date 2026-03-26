"""
Tests for PositionSizer — qty calculation, size reduction, liquidity guard.
"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from risk.position_sizer import PositionSizer


@pytest.fixture
def sizer():
    return PositionSizer(account_value=20000)


def test_qty_calculated_from_risk(sizer):
    # Risk per trade = 20000 * 1% (Nano) = 200
    # entry=1000, sl=990 → rps=10 → by_risk=200/10=20
    # max_per_trade = 20000 * 40% = 8000 → by_capital = 8000/1000 = 8
    # qty = min(20, 8) = 8
    qty = sizer.calculate_qty(entry_price=1000, stop_loss=990)
    assert qty == 8


def test_qty_capped_by_capital(sizer):
    # Very tight SL → by_risk is huge, should be capped by capital
    qty = sizer.calculate_qty(entry_price=1000, stop_loss=999)
    by_capital = int(sizer.max_per_trade() / 1000)
    assert qty <= by_capital


def test_qty_zero_when_sl_equals_entry(sizer):
    qty = sizer.calculate_qty(entry_price=1000, stop_loss=1000)
    assert qty == 0


def test_qty_zero_when_sl_above_entry(sizer):
    qty = sizer.calculate_qty(entry_price=1000, stop_loss=1010)
    assert qty == 0


def test_size_multiplier_reduces_qty(sizer):
    qty_full = sizer.calculate_qty(entry_price=1000, stop_loss=990, size_multiplier=1.0)
    qty_half = sizer.calculate_qty(entry_price=1000, stop_loss=990, size_multiplier=0.5)
    assert qty_half <= qty_full


def test_liquidity_guard_caps_qty(sizer):
    # qty=100, avg_daily_volume=5000, max_participation=1% → max_qty=50
    capped = sizer.liquidity_guard(qty=100, avg_daily_volume=5000, max_participation=0.01)
    assert capped == 50


def test_liquidity_guard_allows_under_limit(sizer):
    qty = sizer.liquidity_guard(qty=10, avg_daily_volume=5000, max_participation=0.01)
    assert qty == 10


def test_liquidity_guard_zero_volume(sizer):
    qty = sizer.liquidity_guard(qty=10, avg_daily_volume=0)
    assert qty == 10


def test_max_risk_per_trade_scales_with_account():
    s1 = PositionSizer(account_value=10000)
    s2 = PositionSizer(account_value=100000)
    assert s2.max_risk_per_trade() > s1.max_risk_per_trade()


def test_max_daily_loss_is_percentage():
    sizer = PositionSizer(account_value=50000)
    tier = sizer.tier
    assert sizer.max_daily_loss() == pytest.approx(50000 * tier.daily_loss_pct, rel=0.01)


def test_max_deployable_is_percentage():
    sizer = PositionSizer(account_value=50000)
    tier = sizer.tier
    assert sizer.max_deployable() == pytest.approx(50000 * tier.deployment_pct, rel=0.01)


def test_update_recalculates_tier():
    sizer = PositionSizer(account_value=20000)
    old_risk = sizer.max_risk_per_trade()
    sizer.update(200000)
    assert sizer.max_risk_per_trade() != old_risk


def test_weekly_drawdown_limit():
    sizer = PositionSizer(account_value=10000)
    assert sizer.max_weekly_drawdown() == pytest.approx(10000 * sizer.tier.weekly_dd_pct, rel=0.01)


def test_monthly_drawdown_limit():
    sizer = PositionSizer(account_value=10000)
    assert sizer.max_monthly_drawdown() == pytest.approx(10000 * sizer.tier.monthly_dd_pct, rel=0.01)
