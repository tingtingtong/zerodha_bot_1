import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class TradeJournal:

    def __init__(self, log_dir: str = "journaling/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _daily_file(self) -> Path:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        return self.log_dir / f"trades_{today}.json"

    def save_trade(self, trade):
        fp = self._daily_file()
        trades = self._load(fp)
        record = trade.to_dict()
        record["state_history"] = trade.state_history
        idx = next((i for i, t in enumerate(trades)
                    if t.get("trade_id") == trade.trade_id), None)
        if idx is not None:
            trades[idx] = record
        else:
            trades.append(record)
        self._save(fp, trades)

    def load_open_trades(self) -> List[dict]:
        trades = self._load(self._daily_file())
        closed_states = {"closed_profit", "closed_loss", "closed_time",
                         "closed_emergency", "error"}
        return [t for t in trades if t.get("state") not in closed_states]

    def save_account_state(self, account_value: float, daily_pnl: float,
                           path: str = "journaling/account_state.json"):
        fp = Path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        open_trades = self.load_open_trades()
        with open(fp, "w") as f:
            json.dump({
                "account_value": round(account_value, 2),
                "daily_pnl": round(daily_pnl, 2),
                "last_updated": datetime.now(IST).isoformat(),
                "open_symbols": [t["symbol"] for t in open_trades],
            }, f, indent=2)

    def load_account_state(self, path: str = "journaling/account_state.json",
                           default: float = 20000.0) -> float:
        try:
            with open(path) as f:
                return float(json.load(f).get("account_value", default))
        except Exception:
            return default

    def _load(self, fp: Path) -> list:
        if fp.exists():
            try:
                with open(fp) as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save(self, fp: Path, data: list):
        with open(fp, "w") as f:
            json.dump(data, f, indent=2, default=str)
