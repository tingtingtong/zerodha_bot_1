"""
Options Layer — Grade-A signal amplifier (paper mode simulation).

When the equity strategy fires a Grade-A signal, this layer ALSO buys
NIFTY weekly ATM options as a leveraged amplifier:
  - LONG signal → buy ATM Call (CE)
  - SHORT signal → buy ATM Put (PE)

P&L is simulated via a delta approximation:
  option_move ≈ delta × NIFTY_move
  NIFTY_move  ≈ stock_move × correlation_factor

Exits are aligned with the parent equity trade:
  T1 hit → close 50% of option lots
  T2/SL/time exit → close remaining lots
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

LOT_SIZE = 25            # NIFTY lot size
STRIKE_STEP = 50         # NIFTY strike interval (50-point grid)
WEEKLY_PREMIUM_PCT = 0.004  # ATM weekly premium ≈ 0.4% of spot (e.g. ₹88 for 22,000 NIFTY)
ATM_DELTA = 0.50         # ATM option delta (standard Black-Scholes approximation)
STOCK_NIFTY_CORR = 0.35  # Average stock-to-NIFTY correlation for NIFTY-200 stocks


@dataclass
class OptionTrade:
    trade_id: str             # Matches parent equity TradeRecord.trade_id
    symbol: str               # e.g. "NIFTY26APR22000CE"
    option_type: str          # "CE" or "PE"
    strike: int
    lots: int
    lot_size: int
    entry_premium: float      # Per share (unit) at entry
    nifty_spot: float         # NIFTY spot at entry (for P&L calc)
    capital_deployed: float   # lots × lot_size × premium
    equity_symbol: str        # Parent equity symbol for logging
    entry_time: datetime = field(default_factory=lambda: datetime.now(IST))
    closed: bool = False
    lots_closed: int = 0      # Lots already closed (partial exits)
    realized_pnl: float = 0.0


class OptionsLayer:
    """
    Manages Grade-A options amplification trades in paper/simulation mode.
    Instantiated once in main.py, called around equity trade lifecycle.
    """

    MAX_POSITIONS = 3          # Max concurrent options positions

    def __init__(self, total_capital: float,
                 enabled: bool = True,
                 capital_pct: float = 0.10):
        """
        total_capital  — current account value (used to size allocations)
        enabled        — False → all methods are no-ops (easy kill switch)
        capital_pct    — fraction of total capital per options trade (default 10%)
        """
        self.total_capital = total_capital
        self.enabled = enabled
        self.capital_pct = capital_pct
        self.active: Dict[str, OptionTrade] = {}    # trade_id → OptionTrade
        self.completed: List[OptionTrade] = []
        self.total_pnl = 0.0

    # ── Public API ────────────────────────────────────────────────────────

    def open_option(self, trade_id: str, equity_symbol: str,
                    direction: str, nifty_spot: float) -> Optional[OptionTrade]:
        """
        Called after a Grade-A equity entry is confirmed filled.
        Returns the OptionTrade created, or None if skipped.
        """
        if not self.enabled:
            return None
        if trade_id in self.active:
            return None  # Already has an option (shouldn't happen)
        if len(self.active) >= self.MAX_POSITIONS:
            logger.info(f"[OPTIONS] Max positions reached ({self.MAX_POSITIONS}) — skip {equity_symbol}")
            return None
        if nifty_spot <= 100:
            logger.warning(f"[OPTIONS] NIFTY spot implausible ({nifty_spot}) — skip")
            return None

        option_type = "CE" if direction == "long" else "PE"
        strike = int(round(nifty_spot / STRIKE_STEP) * STRIKE_STEP)
        entry_premium = round(nifty_spot * WEEKLY_PREMIUM_PCT, 1)

        alloc = self.total_capital * self.capital_pct
        cost_per_lot = entry_premium * LOT_SIZE
        lots = max(1, int(alloc / cost_per_lot))
        capital_deployed = round(lots * cost_per_lot, 2)

        # Option symbol — e.g. "NIFTY26APR22000CE"
        exp_tag = datetime.now(IST).strftime("%y%b").upper()
        sym = f"NIFTY{exp_tag}{strike}{option_type}"

        ot = OptionTrade(
            trade_id=trade_id, symbol=sym, option_type=option_type,
            strike=strike, lots=lots, lot_size=LOT_SIZE,
            entry_premium=entry_premium, nifty_spot=nifty_spot,
            capital_deployed=capital_deployed, equity_symbol=equity_symbol,
        )
        self.active[trade_id] = ot
        logger.info(
            f"[OPTIONS OPEN ] {option_type} {sym} | {lots} lots × "
            f"Rs.{entry_premium:.0f}/unit = Rs.{capital_deployed:,.0f} deployed | "
            f"parent equity: {equity_symbol}"
        )
        return ot

    def partial_close(self, trade_id: str,
                      equity_entry: float, equity_current: float,
                      close_fraction: float = 0.5) -> float:
        """
        Close a fraction of option lots. Called at T1 hit.
        Returns P&L for the lots closed.
        """
        ot = self.active.get(trade_id)
        if not ot or ot.closed:
            return 0.0
        lots_open = ot.lots - ot.lots_closed
        lots_to_close = max(1, int(lots_open * close_fraction))
        return self._close_lots(ot, lots_to_close, equity_entry, equity_current,
                                label="partial (T1)")

    def close_all(self, trade_id: str,
                  equity_entry: float, equity_exit: float) -> float:
        """
        Close remaining option lots. Called at full equity trade exit.
        Returns P&L and removes trade from active.
        """
        ot = self.active.get(trade_id)
        if not ot:
            return 0.0
        lots_open = ot.lots - ot.lots_closed
        pnl = 0.0
        if lots_open > 0:
            pnl = self._close_lots(ot, lots_open, equity_entry, equity_exit,
                                   label="full close")
        ot.closed = True
        self.completed.append(ot)
        del self.active[trade_id]
        return pnl

    def get_summary(self) -> dict:
        return {
            "active_options": len(self.active),
            "completed_options": len(self.completed),
            "total_options_pnl": round(self.total_pnl, 2),
        }

    # ── Internal ──────────────────────────────────────────────────────────

    def _close_lots(self, ot: OptionTrade, lots: int,
                    eq_entry: float, eq_exit: float, label: str) -> float:
        """Simulate option P&L for `lots` being closed."""
        lots = min(lots, ot.lots - ot.lots_closed)
        if lots <= 0:
            return 0.0

        # Stock move → estimated NIFTY move → option premium change
        if eq_entry > 0:
            eq_move_pct = (eq_exit - eq_entry) / eq_entry
        else:
            eq_move_pct = 0.0
        if ot.option_type == "PE":
            eq_move_pct = -eq_move_pct  # Put profits when price falls

        nifty_move = ot.nifty_spot * eq_move_pct * STOCK_NIFTY_CORR
        exit_premium = max(0.0, ot.entry_premium + ATM_DELTA * nifty_move)
        pnl = round((exit_premium - ot.entry_premium) * lots * LOT_SIZE, 2)

        ot.lots_closed += lots
        ot.realized_pnl += pnl
        self.total_pnl += pnl

        sign = "+" if pnl >= 0 else ""
        logger.info(
            f"[OPTIONS CLOSE] {label} | {ot.symbol} {lots} lots "
            f"entry Rs.{ot.entry_premium:.1f} -> exit Rs.{exit_premium:.1f} "
            f"| P&L: {sign}Rs.{pnl:,.0f}"
        )
        return pnl
