"""
Tests for TradeJournal and AuditLogger — persistence, load, account state.
"""
import pytest
import sys
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from journaling.trade_journal import TradeJournal
from journaling.audit_logger import AuditLogger
from execution.trade_state_machine import TradeRecord, TradeState


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def journal(tmp_dir):
    return TradeJournal(log_dir=str(tmp_dir))


@pytest.fixture
def trade():
    return TradeRecord(
        symbol="RELIANCE", strategy="EMAPullback", setup_quality="A",
        entry_price=2800.0, entry_qty=3, stop_loss=2772.0,
        target_1=2842.0, target_2=2870.0,
        breakeven_trigger=2842.0, trailing_step=14.0,
    )


# ── Trade Journal ─────────────────────────────────────────────────────────

def test_save_trade_creates_file(journal, trade):
    journal.save_trade(trade)
    files = list(Path(journal.log_dir).glob("trades_*.json"))
    assert len(files) == 1


def test_saved_trade_has_correct_symbol(journal, trade):
    journal.save_trade(trade)
    fp = list(Path(journal.log_dir).glob("trades_*.json"))[0]
    data = json.loads(fp.read_text())
    assert data[0]["symbol"] == "RELIANCE"


def test_save_trade_updates_existing(journal, trade):
    journal.save_trade(trade)
    trade.transition(TradeState.ENTRY_ORDERED)
    journal.save_trade(trade)
    fp = list(Path(journal.log_dir).glob("trades_*.json"))[0]
    data = json.loads(fp.read_text())
    assert len(data) == 1  # not duplicated
    assert data[0]["state"] == "entry_ordered"


def test_save_multiple_trades(journal):
    t1 = TradeRecord(symbol="RELIANCE", strategy="EMA", setup_quality="A",
                     entry_price=2800, entry_qty=3, stop_loss=2772,
                     target_1=2842, target_2=2870, breakeven_trigger=2842, trailing_step=14)
    t2 = TradeRecord(symbol="TCS", strategy="EMA", setup_quality="B",
                     entry_price=3500, entry_qty=1, stop_loss=3465,
                     target_1=3556, target_2=3605, breakeven_trigger=3556, trailing_step=17)
    journal.save_trade(t1)
    journal.save_trade(t2)
    fp = list(Path(journal.log_dir).glob("trades_*.json"))[0]
    data = json.loads(fp.read_text())
    assert len(data) == 2


def test_load_open_trades_excludes_closed(journal):
    t_open = TradeRecord(symbol="RELIANCE", strategy="EMA", setup_quality="A",
                         entry_price=2800, entry_qty=3, stop_loss=2772,
                         target_1=2842, target_2=2870, breakeven_trigger=2842, trailing_step=14)
    t_closed = TradeRecord(symbol="TCS", strategy="EMA", setup_quality="B",
                           entry_price=3500, entry_qty=1, stop_loss=3465,
                           target_1=3556, target_2=3605, breakeven_trigger=3556, trailing_step=17)
    t_closed.transition(TradeState.ENTRY_ORDERED)
    t_closed.transition(TradeState.ENTRY_FILLED)
    t_closed.transition(TradeState.CLOSED_PROFIT, net_pnl=100.0)
    journal.save_trade(t_open)
    journal.save_trade(t_closed)
    open_trades = journal.load_open_trades()
    symbols = [t["symbol"] for t in open_trades]
    assert "RELIANCE" in symbols
    assert "TCS" not in symbols


# ── Account state ─────────────────────────────────────────────────────────

def test_save_and_load_account_state(tmp_dir):
    journal = TradeJournal(log_dir=str(tmp_dir))
    path = str(tmp_dir / "account_state.json")
    journal.save_account_state(12345.67, 234.50, path=path)
    loaded = journal.load_account_state(path=path)
    assert loaded == pytest.approx(12345.67, abs=0.01)


def test_load_account_state_uses_default_when_missing(tmp_dir):
    journal = TradeJournal(log_dir=str(tmp_dir))
    val = journal.load_account_state(path=str(tmp_dir / "nonexistent.json"), default=10000.0)
    assert val == 10000.0


def test_account_state_file_has_daily_pnl(tmp_dir):
    journal = TradeJournal(log_dir=str(tmp_dir))
    path = str(tmp_dir / "account_state.json")
    journal.save_account_state(20000.0, 150.0, path=path)
    data = json.loads(Path(path).read_text())
    assert data["daily_pnl"] == pytest.approx(150.0, abs=0.01)


def test_account_state_has_last_updated(tmp_dir):
    journal = TradeJournal(log_dir=str(tmp_dir))
    path = str(tmp_dir / "account_state.json")
    journal.save_account_state(20000.0, 0.0, path=path)
    data = json.loads(Path(path).read_text())
    assert "last_updated" in data


# ── Audit Logger ──────────────────────────────────────────────────────────

@pytest.fixture
def audit(tmp_dir):
    return AuditLogger(log_dir=str(tmp_dir))


def test_audit_signal_logged(audit, tmp_dir):
    audit.log_signal("RELIANCE", "EMAPullback", "A", "ema_pullback")
    files = list(tmp_dir.glob("audit_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().split("\n")
    entry = json.loads(lines[0])
    assert entry["event"] == "signal"
    assert entry["symbol"] == "RELIANCE"


def test_audit_risk_decision_logged(audit, tmp_dir):
    audit.log_risk_decision("TCS", "APPROVED", "rr_ok", qty=5)
    files = list(tmp_dir.glob("audit_*.jsonl"))
    lines = files[0].read_text().strip().split("\n")
    entry = json.loads(lines[0])
    assert entry["event"] == "risk_decision"
    assert entry["decision"] == "APPROVED"


def test_audit_kill_switch_logged(audit, tmp_dir):
    audit.log_kill_switch("vix_extreme", 10000.0, -300.0)
    files = list(tmp_dir.glob("audit_*.jsonl"))
    entry = json.loads(files[0].read_text().strip())
    assert entry["event"] == "kill_switch"
    assert entry["reason"] == "vix_extreme"


def test_audit_regime_logged(audit, tmp_dir):
    audit.log_regime("weak_bull", 16.5, "trade_long")
    files = list(tmp_dir.glob("audit_*.jsonl"))
    entry = json.loads(files[0].read_text().strip())
    assert entry["event"] == "regime"
    assert entry["regime"] == "weak_bull"


def test_audit_file_is_valid_jsonl(audit, tmp_dir):
    audit.log_signal("A", "strat1", "A", "r1")
    audit.log_signal("B", "strat2", "B", "r2")
    audit.log_risk_decision("A", "APPROVED", "ok")
    files = list(tmp_dir.glob("audit_*.jsonl"))
    for line in files[0].read_text().strip().split("\n"):
        entry = json.loads(line)  # must parse without error
        assert "ts" in entry
        assert "event" in entry


def test_audit_tier_change_logged(audit, tmp_dir):
    audit.log_tier_change("Nano", "Micro", 55000.0)
    files = list(tmp_dir.glob("audit_*.jsonl"))
    entry = json.loads(files[0].read_text().strip())
    assert entry["event"] == "tier_change"
    assert entry["old"] == "Nano"
    assert entry["new"] == "Micro"


def test_audit_error_logged(audit, tmp_dir):
    audit.log_error("data_fetch", "timeout", {"symbol": "RELIANCE"})
    files = list(tmp_dir.glob("audit_*.jsonl"))
    entry = json.loads(files[0].read_text().strip())
    assert entry["event"] == "error"
    assert entry["source"] == "data_fetch"
