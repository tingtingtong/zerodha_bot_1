"""
Tests for daily report generation and formatting.
"""
import pytest
import sys
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from reporting.daily_report import generate_daily_report, format_daily_report, save_daily_report


def make_trade_dict(symbol="RELIANCE", net_pnl=100.0, charges=5.0,
                    state="closed_profit", strategy="EMAPullback"):
    return {
        "trade_id": f"T001_{symbol}",
        "symbol": symbol,
        "strategy": strategy,
        "quality": "A",
        "state": state,
        "entry_price": 2800.0,
        "entry_qty": 3,
        "exit_price": 2850.0,
        "net_pnl": net_pnl,
        "charges": charges,
        "regime_at_entry": "weak_bull",
    }


# ── Report generation ─────────────────────────────────────────────────────

def test_generate_report_no_trades():
    report = generate_daily_report(
        trades=[], account_value=10000, starting_value=10000,
        daily_pnl=0.0, regime="weak_bull", vix=15.0,
        kill_switch_triggered=False, rejected_trades=[],
    )
    assert isinstance(report, dict)
    assert report["trades"] == 0
    assert report["net_pnl"] == 0.0


def test_generate_report_with_trades():
    trades = [
        make_trade_dict("RELIANCE", net_pnl=150.0, charges=5.0),
        make_trade_dict("TCS", net_pnl=-40.0, charges=4.0, state="closed_loss"),
    ]
    report = generate_daily_report(
        trades=trades, account_value=10110, starting_value=10000,
        daily_pnl=110.0, regime="weak_bull", vix=15.0,
        kill_switch_triggered=False, rejected_trades=[],
    )
    assert report["trades"] == 2
    assert report["wins"] == 1
    assert report["losses"] == 1


def test_generate_report_net_pnl():
    trades = [
        make_trade_dict("RELIANCE", net_pnl=200.0),
        make_trade_dict("TCS", net_pnl=-50.0, state="closed_loss"),
    ]
    report = generate_daily_report(
        trades=trades, account_value=10150, starting_value=10000,
        daily_pnl=150.0, regime="strong_bull", vix=13.0,
        kill_switch_triggered=False, rejected_trades=[],
    )
    assert report["net_pnl"] == pytest.approx(150.0, abs=1.0)


def test_generate_report_records_regime():
    report = generate_daily_report(
        trades=[], account_value=10000, starting_value=10000,
        daily_pnl=0.0, regime="sideways", vix=22.0,
        kill_switch_triggered=False, rejected_trades=[],
    )
    assert report["regime"] == "sideways"
    assert report["vix"] == pytest.approx(22.0, abs=0.1)


def test_generate_report_kill_switch_flag():
    report = generate_daily_report(
        trades=[], account_value=9800, starting_value=10000,
        daily_pnl=-200.0, regime="weak_bear", vix=31.0,
        kill_switch_triggered=True, rejected_trades=[],
    )
    assert report["kill_switch"] is True


def test_generate_report_win_rate():
    trades = [
        make_trade_dict("A", net_pnl=100.0),
        make_trade_dict("B", net_pnl=80.0),
        make_trade_dict("C", net_pnl=-30.0, state="closed_loss"),
        make_trade_dict("D", net_pnl=50.0),
    ]
    report = generate_daily_report(
        trades=trades, account_value=10200, starting_value=10000,
        daily_pnl=200.0, regime="weak_bull", vix=14.0,
        kill_switch_triggered=False, rejected_trades=[],
    )
    assert report["wins"] == 3
    assert report["losses"] == 1
    assert report["win_rate"] == pytest.approx(75.0, abs=1.0)


def test_generate_report_charges_summed():
    trades = [
        make_trade_dict("A", net_pnl=100.0, charges=6.0),
        make_trade_dict("B", net_pnl=80.0, charges=5.5),
    ]
    report = generate_daily_report(
        trades=trades, account_value=10185, starting_value=10000,
        daily_pnl=185.0, regime="weak_bull", vix=14.0,
        kill_switch_triggered=False, rejected_trades=[],
    )
    assert report["total_charges"] == pytest.approx(11.5, abs=0.1)


# ── Report saving ─────────────────────────────────────────────────────────

def test_save_daily_report_creates_json():
    d = tempfile.mkdtemp()
    try:
        report = generate_daily_report(
            trades=[], account_value=10000, starting_value=10000,
            daily_pnl=0.0, regime="weak_bull", vix=15.0,
            kill_switch_triggered=False, rejected_trades=[],
        )
        save_daily_report(report, d)
        files = list(Path(d).glob("report_*.json"))
        assert len(files) == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_saved_report_is_valid_json():
    d = tempfile.mkdtemp()
    try:
        report = generate_daily_report(
            trades=[], account_value=10000, starting_value=10000,
            daily_pnl=0.0, regime="weak_bull", vix=15.0,
            kill_switch_triggered=False, rejected_trades=[],
        )
        save_daily_report(report, d)
        fp = list(Path(d).glob("report_*.json"))[0]
        loaded = json.loads(fp.read_text())
        assert loaded["trades"] == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── Format report ─────────────────────────────────────────────────────────

def test_format_daily_report_returns_string():
    report = generate_daily_report(
        trades=[], account_value=10000, starting_value=10000,
        daily_pnl=0.0, regime="weak_bull", vix=15.0,
        kill_switch_triggered=False, rejected_trades=[],
    )
    text = format_daily_report(report)
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_report_includes_pnl():
    trades = [make_trade_dict("RELIANCE", net_pnl=250.0)]
    report = generate_daily_report(
        trades=trades, account_value=10250, starting_value=10000,
        daily_pnl=250.0, regime="strong_bull", vix=12.0,
        kill_switch_triggered=False, rejected_trades=[],
    )
    text = format_daily_report(report)
    assert "250" in text or "P&L" in text.upper() or "pnl" in text.lower()
