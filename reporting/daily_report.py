"""
Daily EOD report generator — text summary + optional Telegram push.
Called by main.py after market close.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pytz

from backtesting.performance_metrics import PerformanceCalculator
from config.capital_tiers import get_tier
from journaling.trade_journal import TradeJournal

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class DailyReportGenerator:

    def __init__(self, log_dir: str = "journaling/logs",
                 report_dir: str = "journaling/reports"):
        self.journal = TradeJournal(log_dir)
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.calc = PerformanceCalculator()

    # ------------------------------------------------------------------
    def generate(self, account_value: float, daily_pnl: float,
                 starting_capital: float, notifier=None) -> str:
        """Build the EOD report string, save it, optionally send via Telegram."""
        today = datetime.now(IST).strftime("%Y-%m-%d")
        trades = self._load_today_trades(today)

        tier = get_tier(account_value)
        report_lines = self._build_report(
            today, account_value, daily_pnl, starting_capital, tier, trades
        )
        report_text = "\n".join(report_lines)

        # Save to file
        fp = self.report_dir / f"report_{today}.txt"
        fp.write_text(report_text, encoding="utf-8")
        logger.info("Daily report saved → %s", fp)

        # Push to Telegram if notifier provided
        if notifier:
            try:
                notifier.send(f"📊 EOD Report {today}\n\n{report_text}")
            except Exception as exc:
                logger.warning("Telegram push failed: %s", exc)

        return report_text

    # ------------------------------------------------------------------
    def _load_today_trades(self, date_str: str) -> List[dict]:
        fp = Path("journaling/logs") / f"trades_{date_str}.json"
        if not fp.exists():
            return []
        try:
            with open(fp) as f:
                return json.load(f)
        except Exception:
            return []

    def _build_report(self, date: str, account_value: float, daily_pnl: float,
                      starting_capital: float, tier, trades: List[dict]) -> List[str]:
        lines = []
        lines.append("=" * 60)
        lines.append(f"  ZERODHA BOT — DAILY REPORT  {date}")
        lines.append("=" * 60)

        # Account summary
        total_return_pct = ((account_value - starting_capital) / starting_capital) * 100
        lines.append(f"\n  Account Value  : ₹{account_value:>12,.2f}")
        lines.append(f"  Daily P&L      : ₹{daily_pnl:>+12,.2f}")
        lines.append(f"  All-time Return: {total_return_pct:>+8.2f}%")
        lines.append(f"  Current Tier   : {tier.name}  (risk {tier.risk_per_trade_pct:.1%}/trade)")

        # Trade summary
        lines.append(f"\n  Trades Today   : {len(trades)}")
        if trades:
            closed = [t for t in trades if t.get("net_pnl") is not None]
            wins   = [t for t in closed if float(t.get("net_pnl", 0)) > 0]
            losses = [t for t in closed if float(t.get("net_pnl", 0)) <= 0]
            gross_p = sum(float(t.get("net_pnl", 0)) for t in wins)
            gross_l = sum(float(t.get("net_pnl", 0)) for t in losses)
            charges = sum(float(t.get("charges", 0)) for t in closed)

            lines.append(f"  Closed Trades  : {len(closed)}  "
                         f"(W:{len(wins)}  L:{len(losses)})")
            if len(closed):
                wr = len(wins) / len(closed) * 100
                lines.append(f"  Win Rate       : {wr:.1f}%")
            lines.append(f"  Gross Profit   : ₹{gross_p:>+10,.2f}")
            lines.append(f"  Gross Loss     : ₹{gross_l:>+10,.2f}")
            lines.append(f"  Total Charges  : ₹{charges:>10,.2f}")

            lines.append("\n  Trade Details:")
            lines.append(f"  {'Symbol':<12} {'Side':<5} {'Qty':>4} "
                         f"{'Entry':>8} {'Exit':>8} {'P&L':>10} {'State':<20}")
            lines.append("  " + "-" * 72)
            for t in trades:
                symbol  = t.get("symbol", "?")[:12]
                side    = t.get("side", "?")[:4]
                qty     = t.get("qty", 0)
                entry   = t.get("entry_price", 0)
                exit_p  = t.get("exit_price") or t.get("entry_price", 0)
                pnl     = t.get("net_pnl", "open")
                state   = t.get("state", "?")[:20]
                pnl_str = f"₹{float(pnl):>+8,.0f}" if pnl not in (None, "open") else "  open  "
                lines.append(f"  {symbol:<12} {side:<5} {qty:>4} "
                              f"{float(entry):>8.2f} {float(exit_p):>8.2f} "
                              f"{pnl_str:>10} {state:<20}")

            # Historical performance from all logs
            perf = self._rolling_performance()
            if perf:
                lines.append(f"\n  30-day Rolling Performance:")
                lines.append(f"  {perf.summary()}")

        else:
            lines.append("  No trades today.")

        lines.append("\n" + "=" * 60)
        return lines

    def _rolling_performance(self, days: int = 30) -> Optional[object]:
        """Aggregate last N days of trade files for rolling metrics."""
        all_trades = []
        log_dir = Path("journaling/logs")
        for fp in sorted(log_dir.glob("trades_*.json"))[-days:]:
            try:
                with open(fp) as f:
                    day_trades = json.load(f)
                closed = [t for t in day_trades
                          if t.get("net_pnl") is not None
                          and t.get("state", "").startswith("closed")]
                all_trades.extend(closed)
            except Exception:
                continue
        if len(all_trades) < 5:
            return None
        try:
            from journaling.trade_journal import TradeJournal as TJ
            acct = TJ().load_account_state()
            return self.calc.calculate(all_trades, acct)
        except Exception:
            return None


# ── Module-level convenience functions (used by main.py) ──────────────────────

def generate_daily_report(
    trades: List[dict],
    account_value: float,
    starting_value: float,
    daily_pnl: float,
    regime: str = "",
    vix: float = 0.0,
    kill_switch_triggered: bool = False,
    rejected_trades: List[dict] = None,
) -> dict:
    """Return a report dict summarising today's session."""
    closed = [t for t in trades if t.get("net_pnl") is not None]
    wins   = [t for t in closed if float(t.get("net_pnl", 0)) > 0]
    losses = [t for t in closed if float(t.get("net_pnl", 0)) <= 0]
    net_pnl   = sum(float(t.get("net_pnl", 0)) for t in closed)
    charges   = sum(float(t.get("charges", 0)) for t in closed)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    return {
        "date": today,
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / max(len(closed), 1) * 100, 1),
        "net_pnl": round(net_pnl, 2),
        "gross_profit": round(sum(float(t.get("net_pnl", 0)) for t in wins), 2),
        "gross_loss": round(sum(float(t.get("net_pnl", 0)) for t in losses), 2),
        "total_charges": round(charges, 2),
        "account_value": round(account_value, 2),
        "daily_pnl": round(daily_pnl, 2),
        "starting_value": round(starting_value, 2),
        "total_return_pct": round((account_value - starting_value) / max(starting_value, 1) * 100, 2),
        "regime": regime,
        "vix": round(vix, 2),
        "kill_switch": kill_switch_triggered,
        "rejected_count": len(rejected_trades or []),
        "trade_details": trades,
    }


def format_daily_report(report: dict) -> str:
    """Return a human-readable report string."""
    lines = [
        "=" * 60,
        f"  ZERODHA BOT — DAILY REPORT  {report.get('date', '')}",
        "=" * 60,
        f"  Account Value  : Rs.{report['account_value']:>12,.2f}",
        f"  Daily P&L      : Rs.{report['daily_pnl']:>+12,.2f}",
        f"  All-time Return: {report['total_return_pct']:>+8.2f}%",
        f"  Regime         : {report.get('regime', 'N/A')}  VIX:{report.get('vix', 0):.1f}",
        f"  Kill Switch    : {'YES' if report['kill_switch'] else 'No'}",
        "",
        f"  Trades         : {report['trades']}  (W:{report['wins']}  L:{report['losses']})",
        f"  Win Rate       : {report['win_rate']:.1f}%",
        f"  Gross Profit   : Rs.{report['gross_profit']:>+10,.2f}",
        f"  Gross Loss     : Rs.{report['gross_loss']:>+10,.2f}",
        f"  Total Charges  : Rs.{report['total_charges']:>10,.2f}",
        f"  Rejected Scans : {report['rejected_count']}",
        "=" * 60,
    ]
    return "\n".join(lines)


def save_daily_report(report: dict, report_dir: str = "journaling/reports"):
    """Save report dict as JSON and formatted text."""
    rdir = Path(report_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    date_str = report.get("date", datetime.now(IST).strftime("%Y-%m-%d"))
    json_fp = rdir / f"report_{date_str}.json"
    txt_fp  = rdir / f"report_{date_str}.txt"
    with open(json_fp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    txt_fp.write_text(format_daily_report(report), encoding="utf-8")
    logger.info("Report saved: %s", json_fp)
