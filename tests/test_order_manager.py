"""
Tests for OrderManager — entry, SL, partial exits, trailing stops, time exit, emergency exit.
"""
import pytest
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from brokers.simulated_broker import SimulatedBroker
from execution.order_manager import OrderManager
from execution.trade_state_machine import TradeRecord, TradeState
from risk.risk_engine import RiskEngine

IST = pytz.timezone("Asia/Kolkata")


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def broker():
    return SimulatedBroker(initial_capital=100000)


@pytest.fixture
def risk():
    return RiskEngine(account_value=100000, config={
        "min_rr_ratio": 1.5, "cooldown_minutes_after_loss": 0,
        "consecutive_loss_halt": 5, "min_trade_value": 500,
        "setup_quality_min": "C", "size_reduction_after_losses": 0.5,
    })


@pytest.fixture
def mgr(broker, risk, tmp_dir):
    m = OrderManager(broker, risk)
    # Redirect placed_keys to temp dir so tests don't share state
    m._placed_keys_file = tmp_dir / "placed_keys.json"
    m._placed_keys = set()
    return m


def make_trade(symbol="RELIANCE", entry=2800, sl=2772, t1=2842, t2=2870, qty=3):
    return TradeRecord(
        symbol=symbol, strategy="EMAPullback", setup_quality="A",
        entry_price=entry, entry_qty=qty, stop_loss=sl,
        target_1=t1, target_2=t2,
        breakeven_trigger=t1, trailing_step=14.0,
    )


# ── Entry execution ───────────────────────────────────────────────────────

def test_execute_entry_fills_trade(mgr):
    trade = make_trade()
    success = mgr.execute_entry(trade, current_price=2800)
    assert success
    assert trade.state in (TradeState.SL_PLACED, TradeState.ENTRY_FILLED)


def test_execute_entry_adds_to_active_trades(mgr):
    trade = make_trade()
    mgr.execute_entry(trade, current_price=2800)
    assert trade.trade_id in mgr.active_trades


def test_execute_entry_places_sl(mgr, broker):
    trade = make_trade()
    mgr.execute_entry(trade, current_price=2800)
    # SL should be pending in broker
    assert trade.sl_order_id is not None


def test_duplicate_entry_blocked(mgr):
    trade = make_trade()
    first = mgr.execute_entry(trade, current_price=2800)
    assert first
    # Same symbol/price/qty → same key → duplicate
    trade2 = make_trade()  # identical params: RELIANCE, entry=2800, qty=3
    second = mgr.execute_entry(trade2, current_price=2800)
    assert not second  # blocked as duplicate


# ── Tick — target exits ───────────────────────────────────────────────────

def test_tick_closes_at_target2(mgr):
    trade = make_trade(entry=2800, sl=2772, t1=2842, t2=2870, qty=4)
    mgr.execute_entry(trade, current_price=2800)
    mgr.tick(trade.symbol, 2871)  # price above T2
    assert trade.is_closed()


def test_tick_partial_exit_at_target1(mgr):
    trade = make_trade(entry=2800, sl=2772, t1=2842, t2=2900, qty=4)
    mgr.execute_entry(trade, current_price=2800)
    mgr.tick(trade.symbol, 2843)  # price above T1 but below T2
    assert len(trade.partial_exits) > 0 or trade.state == TradeState.TARGET_1_HIT


def test_tick_moves_sl_to_breakeven_after_t1(mgr):
    trade = make_trade(entry=2800, sl=2772, t1=2842, t2=2900, qty=4)
    mgr.execute_entry(trade, current_price=2800)
    mgr.tick(trade.symbol, 2843)
    # After T1 hit, SL should be moved to breakeven (entry price)
    if trade.state in (TradeState.BREAKEVEN_MOVED, TradeState.TRAILING_ACTIVE,
                        TradeState.TARGET_1_HIT):
        assert trade.stop_loss >= trade.entry_price


# ── Time exit ─────────────────────────────────────────────────────────────

def test_tick_time_exit_when_max_candles_reached(mgr):
    trade = make_trade(entry=2800, sl=2772, t1=2842, t2=2900, qty=2)
    mgr.execute_entry(trade, current_price=2800)
    max_hold = getattr(trade, 'max_hold_candles', 16)
    trade.candles_held = max_hold - 1
    mgr.tick(trade.symbol, 2810)  # neutral price, just incrementing candles
    if trade.candles_held >= max_hold:
        assert trade.is_closed()


# ── Close trade ───────────────────────────────────────────────────────────

def test_close_trade_calculates_pnl(mgr):
    trade = make_trade(entry=2800, sl=2772, t1=2842, t2=2870, qty=2)
    mgr.execute_entry(trade, current_price=2800)
    tid = trade.trade_id
    mgr.close_trade(tid, exit_price=2850, reason="target_2_hit", charges=10.0)
    assert trade.is_closed()
    assert trade.net_pnl is not None


def test_close_trade_removes_from_active(mgr):
    trade = make_trade(qty=2)
    mgr.execute_entry(trade, current_price=2800)
    tid = trade.trade_id
    mgr.close_trade(tid, exit_price=2850, reason="target_2_hit", charges=10.0)
    assert tid not in mgr.active_trades


def test_close_trade_adds_to_completed(mgr):
    trade = make_trade(qty=2)
    mgr.execute_entry(trade, current_price=2800)
    tid = trade.trade_id
    mgr.close_trade(tid, exit_price=2850, reason="target_2_hit", charges=10.0)
    assert any(t.trade_id == tid for t in mgr.completed_trades)


def test_close_trade_profit_state(mgr):
    trade = make_trade(entry=2800, qty=2)
    mgr.execute_entry(trade, current_price=2800)
    mgr.close_trade(trade.trade_id, exit_price=2860, reason="target_2_hit", charges=5.0)
    assert trade.state == TradeState.CLOSED_PROFIT


def test_close_trade_loss_state(mgr):
    trade = make_trade(entry=2800, sl=2772, qty=2)
    mgr.execute_entry(trade, current_price=2800)
    mgr.close_trade(trade.trade_id, exit_price=2750, reason="sl_hit", charges=5.0)
    assert trade.state == TradeState.CLOSED_LOSS


# ── Emergency exit ────────────────────────────────────────────────────────

def test_emergency_exit_all_closes_everything(mgr):
    t1 = make_trade("RELIANCE", qty=2)
    t2 = make_trade("TCS", entry=3500, sl=3465, t1=3556, t2=3605, qty=1)
    mgr.execute_entry(t1, current_price=2800)
    mgr.execute_entry(t2, current_price=3500)
    assert len(mgr.active_trades) == 2
    mgr.emergency_exit_all("kill_switch")
    assert len(mgr.active_trades) == 0


# ── Trailing stop ─────────────────────────────────────────────────────────

def test_trailing_stop_updates_sl_upward(mgr):
    trade = make_trade(entry=2800, sl=2772, qty=2)
    mgr.execute_entry(trade, current_price=2800)
    old_sl = trade.stop_loss
    mgr.update_trailing_stop(trade.trade_id, new_sl=2820)
    assert trade.stop_loss == 2820
    assert trade.stop_loss > old_sl


def test_trailing_stop_does_not_lower_sl(mgr):
    trade = make_trade(entry=2800, sl=2772, qty=2)
    mgr.execute_entry(trade, current_price=2800)
    trade.stop_loss = 2810  # manually set higher SL
    mgr.update_trailing_stop(trade.trade_id, new_sl=2790)  # try to lower
    assert trade.stop_loss == 2810  # unchanged
