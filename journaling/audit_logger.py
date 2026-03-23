import json
import logging
from datetime import datetime
from pathlib import Path
import pytz

IST = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger(__name__)


class AuditLogger:
    """Append-only structured audit log — every decision and API call recorded."""

    def __init__(self, log_dir: str = "journaling/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _file(self) -> Path:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        return self.log_dir / f"audit_{today}.jsonl"

    def log(self, event_type: str, data: dict):
        entry = {"ts": datetime.now(IST).isoformat(), "event": event_type, **data}
        with open(self._file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def log_signal(self, symbol: str, strategy: str, quality: str, reason: str):
        self.log("signal", {"symbol": symbol, "strategy": strategy,
                             "quality": quality, "reason": reason})

    def log_risk_decision(self, symbol: str, decision: str, reason: str, qty: int = 0):
        self.log("risk_decision", {"symbol": symbol, "decision": decision,
                                    "reason": reason, "qty": qty})

    def log_order(self, action: str, symbol: str, qty: int, price: float,
                  order_id: str, status: str):
        self.log("order", {"action": action, "symbol": symbol, "qty": qty,
                            "price": price, "order_id": order_id, "status": status})

    def log_kill_switch(self, reason: str, account: float, daily_pnl: float):
        self.log("kill_switch", {"reason": reason, "account": account,
                                  "daily_pnl": daily_pnl})

    def log_error(self, source: str, error: str, context: dict = None):
        self.log("error", {"source": source, "error": error,
                            "context": context or {}})

    def log_regime(self, regime: str, vix: float, recommendation: str):
        self.log("regime", {"regime": regime, "vix": vix,
                             "recommendation": recommendation})

    def log_tier_change(self, old_tier: str, new_tier: str, capital: float):
        self.log("tier_change", {"old": old_tier, "new": new_tier,
                                  "capital": capital})
