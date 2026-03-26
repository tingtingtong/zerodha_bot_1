"""
Tests for capital tier detection and percentage-based limits.
"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.capital_tiers import get_tier, get_tier_summary, CAPITAL_TIERS


def test_nano_tier_detected():
    tier = get_tier(10000)
    assert tier.name == "Nano"


def test_micro_tier_detected():
    tier = get_tier(50000)
    assert tier.name == "Micro"


def test_small_tier_detected():
    tier = get_tier(200000)
    assert tier.name == "Small"


def test_medium_tier_detected():
    tier = get_tier(500000)
    assert tier.name == "Medium"


def test_large_tier_detected():
    tier = get_tier(2000000)
    assert tier.name == "Large"


def test_below_minimum_returns_nano():
    tier = get_tier(5000)
    assert tier.name == "Nano"


def test_nano_max_trades_per_day():
    tier = get_tier(10000)
    assert tier.max_trades_per_day == 3


def test_nano_max_positions():
    tier = get_tier(10000)
    assert tier.max_open_positions == 2


def test_large_max_trades():
    tier = get_tier(2000000)
    assert tier.max_trades_per_day == 10


def test_all_tiers_have_percentage_risk():
    for tier in CAPITAL_TIERS:
        assert 0 < tier.risk_per_trade_pct < 1, f"{tier.name} risk % out of range"
        assert 0 < tier.daily_loss_pct < 1
        assert 0 < tier.weekly_dd_pct < 1
        assert 0 < tier.monthly_dd_pct < 1
        assert 0 < tier.deployment_pct < 1


def test_risk_decreases_with_larger_tier():
    nano = get_tier(10000)
    large = get_tier(2000000)
    assert large.risk_per_trade_pct < nano.risk_per_trade_pct


def test_tier_summary_returns_inr_values():
    summary = get_tier_summary(20000)
    assert summary["max_risk_per_trade_inr"] == pytest.approx(20000 * get_tier(20000).risk_per_trade_pct, rel=0.01)
    assert summary["max_daily_loss_inr"] > 0
    assert summary["max_deployment_inr"] > 0


def test_tier_summary_has_all_fields():
    summary = get_tier_summary(50000)
    for key in ("tier_name", "account_value", "max_risk_per_trade_inr",
                "max_daily_loss_inr", "max_deployment_inr",
                "max_trades_per_day", "max_open_positions", "segments"):
        assert key in summary


def test_tiers_cover_continuous_range():
    """No gap between tiers — every ₹ from min to max should map to a tier."""
    test_values = [10000, 49999, 50000, 199999, 200000, 499999,
                   500000, 1999999, 2000000, 10000000]
    for v in test_values:
        tier = get_tier(v)
        assert tier is not None, f"No tier for ₹{v}"


def test_nano_includes_etf_segment():
    tier = get_tier(10000)
    assert any("etf" in s for s in tier.segments)


def test_large_includes_futures():
    tier = get_tier(2000000)
    assert any("futures" in s for s in tier.segments)
