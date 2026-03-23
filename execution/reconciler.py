import json
import logging
from pathlib import Path
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)


class Reconciler:

    def __init__(self, broker: BrokerBase, journal_path: str = "journaling/account_state.json"):
        self.broker = broker
        self.journal_path = journal_path

    def reconcile(self) -> dict:
        broker_positions = {p.symbol: p for p in self.broker.get_positions()}

        known_symbols = set()
        try:
            fp = Path(self.journal_path)
            if fp.exists():
                with open(fp) as f:
                    data = json.load(f)
                # Load open trade symbols from today's trade file if available
                known_symbols = set(data.get("open_symbols", []))
        except Exception as e:
            logger.warning(f"Journal read failed during reconcile: {e}")

        orphaned = [s for s in broker_positions if s not in known_symbols]
        ghost = [s for s in known_symbols if s not in broker_positions]

        result = {
            "broker_positions": len(broker_positions),
            "known_positions": len(known_symbols),
            "orphaned": orphaned,
            "ghost_trades": ghost,
            "status": "clean" if not orphaned and not ghost else "mismatch",
        }

        if orphaned:
            logger.critical(
                f"ORPHANED POSITIONS: {orphaned}. "
                "Bot has no record. Place protective SL immediately!"
            )
        if ghost:
            logger.warning(f"Ghost trades in journal but not in broker: {ghost}")

        return result
