import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import adaptive.strategy_evaluator as ev
from risk.risk_engine import RiskEngine


def test_size_multiplier_scales_risk_budget():
    # Tight stop, small account: risk budget (not the per-trade cap) decides qty
    full = RiskEngine(200_000, {"min_rr_ratio": 1.0}).check_trade(
        "X", 500, 480, 1, 0, "B", 10, 0, size_multiplier=1.0)
    half = RiskEngine(200_000, {"min_rr_ratio": 1.0}).check_trade(
        "X", 500, 480, 1, 0, "B", 10, 0, size_multiplier=0.5)
    assert half.adjusted_qty == pytest.approx(full.adjusted_qty / 2, abs=1)


def test_size_multiplier_stacks_with_loss_reduction():
    r = RiskEngine(200_000, {"min_rr_ratio": 1.0})
    base = r.check_trade("X", 500, 480, 1, 0, "B", 10, 0, size_multiplier=1.0).adjusted_qty
    r.consecutive_losses = 2  # engine halves size after 2 losses
    both = r.check_trade("X", 500, 480, 1, 0, "B", 10, 0, size_multiplier=0.5).adjusted_qty
    assert both == pytest.approx(base / 4, abs=1)


@pytest.fixture
def isolated(monkeypatch):
    root = Path(tempfile.mkdtemp())
    logs = root / "logs"
    logs.mkdir()
    monkeypatch.setattr(ev, "TRADE_LOG_DIR", logs)
    monkeypatch.setattr(ev, "ADAPTIVE_STATE_PATH", root / "state.json")
    monkeypatch.setattr(ev, "ADAPTIVE_LOG_DIR", root / "evals")
    yield logs
    shutil.rmtree(root, ignore_errors=True)


def _write_day(logs, strategy, n, pnl, days_ago=1):
    d = (datetime.now(ev.IST) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    trades = [{"strategy": strategy, "net_pnl": pnl, "charges": 5.0,
               "regime_at_entry": "sideways"} for _ in range(n)]
    (logs / f"trades_{d}.json").write_text(json.dumps(trades))


def test_disabled_strategy_stays_disabled_without_new_trades(isolated):
    _write_day(isolated, "ORB", 12, -100.0)
    first = ev.evaluate(4)
    assert "ORB" in first.disabled_strategies
    # Another strategy keeps trading; ORB (disabled) produces nothing new
    _write_day(isolated, "VWAP", 3, 50.0, days_ago=0)
    second = ev.evaluate(1)  # window no longer contains ORB's trades at all
    assert second.strategy_weights["ORB"] == 0.0
    assert second.disabled_strategies["ORB"] == first.disabled_strategies["ORB"]  # original date kept


def test_open_trades_are_not_counted_as_losses(isolated):
    d = datetime.now(ev.IST).strftime("%Y-%m-%d")
    (isolated / f"trades_{d}.json").write_text(json.dumps(
        [{"strategy": "ORB", "net_pnl": None, "state": "sl_placed"}] * 20))
    m = ev.compute_strategy_metrics(ev.load_trades(1))
    assert "ORB" not in m or m["ORB"].total_trades == 0


def test_costs_are_subtracted_when_journal_has_none():
    gross = {"net_pnl": 100.0, "charges": 0.0, "entry_price": 500.0,
             "exit_price": 505.0, "entry_qty": 100}
    assert ev._cost_adjusted_pnl(gross) < 100.0
    recorded = dict(gross, charges=20.0)
    assert ev._cost_adjusted_pnl(recorded) == 100.0
