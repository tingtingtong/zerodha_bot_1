import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from risk.risk_engine import RiskEngine, RiskDecision


@pytest.fixture
def engine():
    config = {
        "min_rr_ratio": 1.8,
        "cooldown_minutes_after_loss": 0,  # Disable for tests
        "consecutive_loss_halt": 3,
        "min_trade_value": 500,
        "setup_quality_min": "B",
        "size_reduction_after_losses": 0.5,
    }
    return RiskEngine(account_value=20000, config=config)


def test_approved_trade(engine):
    result = engine.check_trade(
        symbol="RELIANCE", entry_price=2800, stop_loss=2772,
        proposed_qty=7, open_positions_value=0,
        setup_quality="B", charges_estimate=6,  # realistic ~₹6 for 2 shares intraday
    )
    assert result.decision == RiskDecision.APPROVED
    assert result.adjusted_qty > 0


def test_daily_loss_limit(engine):
    engine.daily_pnl = -600  # 3% of 20k
    result = engine.check_trade(
        symbol="TCS", entry_price=3500, stop_loss=3465,
        proposed_qty=5, open_positions_value=0,
        setup_quality="A", charges_estimate=50,
    )
    assert result.decision in (RiskDecision.KILL_SWITCH, RiskDecision.REJECTED)


def test_kill_switch_blocks_all(engine):
    engine.kill_switch_active = True
    result = engine.check_trade(
        symbol="INFY", entry_price=1500, stop_loss=1485,
        proposed_qty=10, open_positions_value=0,
        setup_quality="A", charges_estimate=30,
    )
    assert result.decision == RiskDecision.KILL_SWITCH


def test_c_grade_rejected(engine):
    result = engine.check_trade(
        symbol="WIPRO", entry_price=500, stop_loss=495,
        proposed_qty=10, open_positions_value=0,
        setup_quality="C", charges_estimate=20,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "quality" in result.reason


def test_consecutive_loss_reduction(engine):
    engine.consecutive_losses = 2
    result = engine.check_trade(
        symbol="HDFCBANK", entry_price=1600, stop_loss=1584,
        proposed_qty=10, open_positions_value=0,
        setup_quality="A", charges_estimate=40,
    )
    if result.decision == RiskDecision.APPROVED:
        # Size should be reduced
        assert result.size_multiplier == 0.5


def test_consecutive_loss_halt(engine):
    engine.consecutive_losses = 3
    result = engine.check_trade(
        symbol="SBIN", entry_price=600, stop_loss=594,
        proposed_qty=5, open_positions_value=0,
        setup_quality="A", charges_estimate=20,
    )
    assert result.decision == RiskDecision.REJECTED


def test_max_trades_per_day(engine):
    from config.capital_tiers import get_tier
    tier = get_tier(20000)
    engine.trade_count_today = tier.max_trades_per_day
    result = engine.check_trade(
        symbol="LT", entry_price=3000, stop_loss=2970,
        proposed_qty=3, open_positions_value=0,
        setup_quality="A", charges_estimate=50,
    )
    assert result.decision == RiskDecision.REJECTED


def test_reset_daily(engine):
    engine.daily_pnl = -300
    engine.trade_count_today = 5
    engine.kill_switch_active = True
    engine.reset_daily()
    assert engine.daily_pnl == 0
    assert engine.trade_count_today == 0
    assert not engine.kill_switch_active


def test_account_updates_after_trade(engine):
    starting = engine.account_value
    engine.record_result(150.0, "RELIANCE")
    assert engine.account_value == starting + 150.0
    engine.record_result(-100.0, "TCS")
    assert engine.account_value == starting + 50.0
