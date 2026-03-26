"""
Tests for RiskEngine — kill switches, drawdowns, position limits, trade checks.
"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from risk.risk_engine import RiskEngine, RiskDecision


BASE_CONFIG = {
    "min_rr_ratio": 1.5,
    "cooldown_minutes_after_loss": 0,
    "consecutive_loss_halt": 3,
    "min_trade_value": 500,
    "setup_quality_min": "B",
    "size_reduction_after_losses": 0.5,
}


@pytest.fixture
def engine():
    return RiskEngine(account_value=20000, config=BASE_CONFIG)


def approved_trade(engine, **overrides):
    kwargs = dict(symbol="RELIANCE", entry_price=2800, stop_loss=2772,
                  proposed_qty=7, open_positions_value=0,
                  setup_quality="B", charges_estimate=6)
    kwargs.update(overrides)
    return engine.check_trade(**kwargs)


# ── Happy path ────────────────────────────────────────────────────────────

def test_approved_trade(engine):
    result = approved_trade(engine)
    assert result.decision == RiskDecision.APPROVED
    assert result.adjusted_qty > 0


def test_approved_trade_has_qty(engine):
    result = approved_trade(engine)
    assert result.adjusted_qty >= 1


# ── Kill switch ───────────────────────────────────────────────────────────

def test_kill_switch_blocks_all_trades(engine):
    engine.kill_switch_active = True
    result = approved_trade(engine)
    assert result.decision == RiskDecision.KILL_SWITCH


def test_market_kill_switch_vix_extreme(engine):
    kill, reason = engine.check_market_kill_switch(
        nifty_chg_pct=0, vix=31, api_errors=0, data_stale_min=0)
    assert kill
    assert "vix" in reason.lower()


def test_market_kill_switch_nifty_crash(engine):
    kill, reason = engine.check_market_kill_switch(
        nifty_chg_pct=-3.0, vix=15, api_errors=0, data_stale_min=0)
    assert kill
    assert "nifty" in reason.lower()


def test_market_kill_switch_api_errors(engine):
    kill, reason = engine.check_market_kill_switch(
        nifty_chg_pct=0, vix=15, api_errors=3, data_stale_min=0)
    assert kill


def test_market_kill_switch_stale_data(engine):
    kill, reason = engine.check_market_kill_switch(
        nifty_chg_pct=0, vix=15, api_errors=0, data_stale_min=6)
    assert kill


def test_no_kill_switch_normal_conditions(engine):
    kill, _ = engine.check_market_kill_switch(
        nifty_chg_pct=0.5, vix=15, api_errors=0, data_stale_min=0)
    assert not kill


# ── Daily loss limit ──────────────────────────────────────────────────────

def test_daily_loss_limit_rejects(engine):
    engine.daily_pnl = -600  # 3% of 20k — beyond Nano tier 2.5% limit
    result = approved_trade(engine)
    assert result.decision in (RiskDecision.KILL_SWITCH, RiskDecision.REJECTED)


def test_daily_loss_within_limit_approved(engine):
    engine.daily_pnl = -100  # small loss, well within limit
    result = approved_trade(engine)
    assert result.decision == RiskDecision.APPROVED


# ── Setup quality ─────────────────────────────────────────────────────────

def test_c_grade_rejected(engine):
    result = approved_trade(engine, setup_quality="C")
    assert result.decision == RiskDecision.REJECTED
    assert "quality" in result.reason


def test_a_grade_approved(engine):
    result = approved_trade(engine, setup_quality="A")
    assert result.decision == RiskDecision.APPROVED


# ── Consecutive losses ────────────────────────────────────────────────────

def test_two_losses_reduces_size(engine):
    engine.consecutive_losses = 2
    result = approved_trade(engine)
    if result.decision == RiskDecision.APPROVED:
        assert result.size_multiplier == 0.5


def test_three_losses_halt(engine):
    engine.consecutive_losses = 3
    result = approved_trade(engine)
    assert result.decision == RiskDecision.REJECTED


def test_win_resets_consecutive_losses(engine):
    engine.consecutive_losses = 2
    engine.record_result(100.0, "RELIANCE")
    assert engine.consecutive_losses == 0


def test_loss_increments_counter(engine):
    engine.record_result(-50.0, "TCS")
    assert engine.consecutive_losses == 1


# ── Trade / position limits ───────────────────────────────────────────────

def test_max_trades_per_day_limit(engine):
    from config.capital_tiers import get_tier
    tier = get_tier(20000)
    engine.trade_count_today = tier.max_trades_per_day
    result = approved_trade(engine)
    assert result.decision == RiskDecision.REJECTED


def test_open_positions_limit(engine):
    from config.capital_tiers import get_tier
    tier = get_tier(20000)
    engine.open_positions_count = tier.max_open_positions
    result = approved_trade(engine)
    assert result.decision == RiskDecision.REJECTED


# ── SL validation ─────────────────────────────────────────────────────────

def test_sl_above_entry_rejected(engine):
    result = engine.check_trade(symbol="X", entry_price=2800, stop_loss=2850,
                                proposed_qty=5, open_positions_value=0,
                                setup_quality="A", charges_estimate=10)
    assert result.decision == RiskDecision.REJECTED


# ── Reset ─────────────────────────────────────────────────────────────────

def test_reset_daily_clears_state(engine):
    engine.daily_pnl = -300
    engine.trade_count_today = 5
    engine.kill_switch_active = True
    engine.reset_daily()
    assert engine.daily_pnl == 0
    assert engine.trade_count_today == 0
    assert not engine.kill_switch_active


def test_reset_daily_keeps_account_value(engine):
    engine.record_result(500.0, "RELIANCE")
    val_before = engine.account_value
    engine.reset_daily()
    assert engine.account_value == val_before


# ── Account tracking ──────────────────────────────────────────────────────

def test_account_increases_on_profit(engine):
    starting = engine.account_value
    engine.record_result(150.0, "RELIANCE")
    assert engine.account_value == starting + 150.0


def test_account_decreases_on_loss(engine):
    starting = engine.account_value
    engine.record_result(-100.0, "TCS")
    assert engine.account_value == starting - 100.0


def test_daily_pnl_tracked(engine):
    engine.record_result(200.0, "RELIANCE")
    engine.record_result(-50.0, "TCS")
    assert engine.daily_pnl == pytest.approx(150.0, abs=0.01)
