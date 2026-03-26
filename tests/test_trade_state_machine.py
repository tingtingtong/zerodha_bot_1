"""
Tests for TradeRecord state machine — transitions, history, validation.
"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from execution.trade_state_machine import TradeRecord, TradeState, VALID_TRANSITIONS


@pytest.fixture
def trade():
    return TradeRecord(
        symbol="RELIANCE", strategy="EMAPullback", setup_quality="A",
        entry_price=2800.0, entry_qty=5, stop_loss=2772.0,
        target_1=2842.0, target_2=2870.0,
        breakeven_trigger=2842.0, trailing_step=14.0,
    )


# ── Initial state ──────────────────────────────────────────────────────────

def test_initial_state_is_signal_generated(trade):
    assert trade.state == TradeState.SIGNAL_GENERATED


def test_remaining_qty_equals_entry_qty(trade):
    assert trade.remaining_qty == 5


def test_state_history_has_initial_entry(trade):
    assert len(trade.state_history) >= 1
    assert "signal_generated" in trade.state_history[0]


def test_trade_id_generated(trade):
    assert trade.trade_id.startswith("T")
    assert len(trade.trade_id) > 8


# ── Valid transitions ──────────────────────────────────────────────────────

def test_signal_to_entry_ordered(trade):
    result = trade.transition(TradeState.ENTRY_ORDERED)
    assert trade.state == TradeState.ENTRY_ORDERED
    assert "→" in result


def test_entry_ordered_to_filled(trade):
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED)
    assert trade.state == TradeState.ENTRY_FILLED


def test_entry_filled_to_sl_placed(trade):
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED)
    trade.transition(TradeState.SL_PLACED)
    assert trade.state == TradeState.SL_PLACED


def test_full_profit_path(trade):
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED)
    trade.transition(TradeState.SL_PLACED)
    trade.transition(TradeState.TARGET_1_HIT)
    trade.transition(TradeState.BREAKEVEN_MOVED)
    trade.transition(TradeState.TRAILING_ACTIVE)
    trade.transition(TradeState.CLOSED_PROFIT)
    assert trade.state == TradeState.CLOSED_PROFIT


def test_transition_sets_kwargs(trade):
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED, entry_price=2805.0)
    assert trade.entry_price == 2805.0


def test_sl_order_id_set_on_transition(trade):
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED)
    trade.transition(TradeState.SL_PLACED, sl_order_id="SL_001")
    assert trade.sl_order_id == "SL_001"


# ── Invalid transitions ────────────────────────────────────────────────────

def test_invalid_transition_blocked(trade):
    # Cannot jump from SIGNAL_GENERATED to CLOSED_PROFIT directly
    result = trade.transition(TradeState.CLOSED_PROFIT)
    assert trade.state == TradeState.SIGNAL_GENERATED  # unchanged
    assert "BLOCKED" in result


def test_terminal_state_no_further_transitions(trade):
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED)
    trade.transition(TradeState.CLOSED_PROFIT, net_pnl=150.0)
    result = trade.transition(TradeState.CLOSED_LOSS)
    assert trade.state == TradeState.CLOSED_PROFIT  # stays closed_profit
    assert "BLOCKED" in result


def test_error_state_is_terminal(trade):
    trade.transition(TradeState.ERROR)
    result = trade.transition(TradeState.ENTRY_ORDERED)
    assert "BLOCKED" in result


# ── State predicates ──────────────────────────────────────────────────────

def test_is_open_after_entry_filled(trade):
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED)
    assert trade.is_open()


def test_is_open_after_sl_placed(trade):
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED)
    trade.transition(TradeState.SL_PLACED)
    assert trade.is_open()


def test_is_closed_after_close(trade):
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED)
    trade.transition(TradeState.CLOSED_LOSS, net_pnl=-50.0)
    assert trade.is_closed()
    assert not trade.is_open()


def test_is_not_open_initially(trade):
    assert not trade.is_open()


# ── State history ─────────────────────────────────────────────────────────

def test_state_history_grows_with_transitions(trade):
    initial_len = len(trade.state_history)
    trade.transition(TradeState.ENTRY_ORDERED)
    trade.transition(TradeState.ENTRY_FILLED)
    assert len(trade.state_history) == initial_len + 2


def test_invalid_transition_not_added_to_history(trade):
    initial_len = len(trade.state_history)
    trade.transition(TradeState.CLOSED_PROFIT)  # invalid
    assert len(trade.state_history) == initial_len


# ── Serialisation ─────────────────────────────────────────────────────────

def test_to_dict_has_required_fields(trade):
    d = trade.to_dict()
    for field in ("trade_id", "symbol", "strategy", "state", "entry_price",
                  "entry_qty", "stop_loss", "target_1", "target_2"):
        assert field in d


def test_to_dict_state_is_string(trade):
    d = trade.to_dict()
    assert isinstance(d["state"], str)


# ── VALID_TRANSITIONS completeness ────────────────────────────────────────

def test_all_states_in_valid_transitions():
    for state in TradeState:
        assert state in VALID_TRANSITIONS, f"{state} missing from VALID_TRANSITIONS"
