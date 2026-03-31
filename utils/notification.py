import logging
import os
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)


class TelegramNotifier:

    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._enabled = bool(self.token and self.chat_id)

    def send(self, message: str) -> bool:
        if not self._enabled:
            logger.debug(f"[NOTIFY disabled] {message[:80]}")
            return False
        import requests
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        if len(message) > 4000:
            message = message[:3990] + "\n...(truncated)"
        for parse_mode in ("HTML", None):
            try:
                payload = {"chat_id": self.chat_id, "text": message}
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                resp = requests.post(url, json=payload, timeout=8)
                resp.raise_for_status()
                return True
            except Exception as e:
                if parse_mode == "HTML":
                    message = re.sub(r"<[^>]+>", "", message).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                    logger.debug(f"HTML send failed, retrying as plain text: {e}")
                else:
                    logger.warning(f"Telegram send failed: {e}")
        return False

    def send_trade_entry(self, symbol, qty, entry, sl, t1, t2, quality):
        self.send(
            f"<b>TRADE ENTRY</b>\n"
            f"Symbol: {symbol}  Qty: {qty} @ Rs.{entry:.2f}\n"
            f"SL: Rs.{sl:.2f}  T1: Rs.{t1:.2f}  T2: Rs.{t2:.2f}\n"
            f"Quality: {quality}  Risk: Rs.{(entry - sl) * qty:.0f}"
        )

    def send_trade_exit(self, symbol, qty, exit_price, net_pnl, reason):
        icon = "+" if net_pnl > 0 else "-"
        self.send(
            f"[{icon}] <b>TRADE EXIT</b>\n"
            f"{symbol}  {qty} @ Rs.{exit_price:.2f}\n"
            f"Net P&amp;L: Rs.{net_pnl:+.2f}  Reason: {reason}"
        )

    def send_kill_switch(self, reason, account_value, daily_pnl):
        self.send(
            f"[!] <b>KILL SWITCH TRIGGERED</b>\n"
            f"Reason: {reason}\n"
            f"Daily P&amp;L: Rs.{daily_pnl:+.2f}  Account: Rs.{account_value:,.0f}"
        )

    def send_daily_summary(self, report: dict):
        opt_pnl = report.get("options_pnl", 0)
        opt_trades = report.get("options_trades", 0)
        opt_line = ""
        if opt_trades > 0:
            sign = "+" if opt_pnl >= 0 else ""
            opt_line = f"\nOptions P&amp;L: Rs.{sign}{opt_pnl:,.0f}  ({opt_trades} trades)"
        self.send(
            f"[=] <b>DAILY SUMMARY</b>\n"
            f"Trades: {report.get('trades', 0)}  W:{report.get('wins', 0)} L:{report.get('losses', 0)}\n"
            f"Win Rate: {report.get('win_rate', 0):.0f}%\n"
            f"Equity P&amp;L: Rs.{report.get('net_pnl', 0):+,.0f}"
            f"{opt_line}\n"
            f"Account: Rs.{report.get('account_value', 0):,.0f}"
        )

    def send_weekly_summary(self, week_pnl: float, week_trades: int,
                            week_wins: int, week_losses: int,
                            options_pnl: float, account_value: float,
                            starting_capital: float,
                            best_trade: dict = None, worst_trade: dict = None):
        """Friday EOD — full week recap sent to Telegram."""
        wr = round(week_wins / max(week_trades, 1) * 100)
        total_return_pct = (account_value - starting_capital) / max(starting_capital, 1) * 100
        combined_pnl = week_pnl + options_pnl

        best_line = ""
        if best_trade:
            best_line = (f"\nBest trade : {best_trade.get('symbol', '?')} "
                         f"Rs.{float(best_trade.get('net_pnl', 0)):+,.0f}")
        worst_line = ""
        if worst_trade:
            worst_line = (f"\nWorst trade: {worst_trade.get('symbol', '?')} "
                          f"Rs.{float(worst_trade.get('net_pnl', 0)):+,.0f}")

        opt_line = ""
        if options_pnl:
            sign = "+" if options_pnl >= 0 else ""
            opt_line = f"\nOptions P&amp;L : Rs.{sign}{options_pnl:,.0f}"

        sign = "+" if combined_pnl >= 0 else ""
        self.send(
            f"<b>WEEKLY SUMMARY — w/e {datetime.now().strftime('%d %b %Y')}</b>\n"
            f"--------------------\n"
            f"Trades    : {week_trades}  (W:{week_wins} L:{week_losses}  WR:{wr}%)\n"
            f"Equity P&amp;L : Rs.{week_pnl:+,.0f}"
            f"{opt_line}\n"
            f"Combined  : Rs.{sign}{combined_pnl:,.0f}\n"
            f"Account   : Rs.{account_value:,.0f}  ({total_return_pct:+.1f}% all-time)"
            f"{best_line}{worst_line}\n"
            f"--------------------\n"
            f"Next week: bot auto-starts Mon 9:00 AM IST"
        )

    def send_premarket_brief(self, regime: str, vix: float, account_value: float,
                             tier_name: str, max_risk_inr: float, max_trades: int,
                             watchlist: list, strategies: list,
                             blockers: list, trade_probability: str,
                             next_trading_day: date = None):
        """Daily pre-market analysis — sent ~9:00 AM before market opens."""

        regime_icon = {
            "strong_bull": "[++]", "weak_bull": "[+]",
            "sideways": "[=]", "weak_bear": "[-]", "strong_bear": "[--]",
        }.get(regime, "[?]")

        vix_note = " (elevated)" if vix >= 25 else " (caution)" if vix >= 20 else " (normal)"
        prob_icon = {"High": "[HIGH]", "Medium": "[MED]", "Low": "[LOW]"}.get(trade_probability, "[?]")

        wl_lines = ""
        for i, sym in enumerate(watchlist[:5], 1):
            wl_lines += f"  {i}. {sym}\n"
        if not wl_lines:
            wl_lines = "  No candidates qualified\n"

        blocker_lines = ""
        for b in blockers:
            blocker_lines += f"  [!] {b}\n"
        if not blocker_lines:
            blocker_lines = "  None - all systems go\n"

        next_day_note = ""
        if next_trading_day and next_trading_day != date.today():
            next_day_note = f"\nNext trading day: {next_trading_day.strftime('%a %d %b')}"

        self.send(
            f"<b>PRE-MARKET BRIEF — {date.today().strftime('%a %d %b %Y')}</b>\n"
            f"--------------------\n"
            f"\n<b>Market Conditions</b>\n"
            f"  Regime : {regime_icon} {regime.upper()}\n"
            f"  VIX    : {vix:.1f}{vix_note}\n"
            f"\n<b>Account — {tier_name} Tier</b>\n"
            f"  Capital    : Rs.{account_value:,.0f}\n"
            f"  Max risk   : Rs.{max_risk_inr:.0f} per trade\n"
            f"  Max trades : {max_trades} today\n"
            f"\n<b>Top Watchlist</b>\n{wl_lines}"
            f"\n<b>Active Strategies</b>\n"
            f"  {', '.join(strategies)}\n"
            f"\n<b>Trade Blockers</b>\n{blocker_lines}"
            f"\n<b>Trade Probability Today</b>\n"
            f"  {prob_icon} <b>{trade_probability}</b>\n"
            f"--------------------"
            f"{next_day_note}"
        )

    def send_hourly_status(self, hour: str, regime: str, vix: float,
                           account_value: float, daily_pnl: float,
                           open_trades: list, trades_today: int,
                           kill_switch: bool, steps: list = None):
        open_lines = ""
        if open_trades:
            for t in open_trades:
                pnl = t.get("unrealised_pnl", 0)
                icon = "[+]" if pnl >= 0 else "[-]"
                open_lines += f"  {icon} {t['symbol']}  qty={t['qty']}  P&amp;L: Rs.{pnl:+.0f}\n"
        else:
            open_lines = "  None\n"

        ks_line = "[!] KILL SWITCH ACTIVE" if kill_switch else "[OK] Normal"

        steps_lines = ""
        if steps:
            seen = set()
            unique_steps = []
            for s in reversed(steps):
                key = s[:60]
                if key not in seen:
                    seen.add(key)
                    unique_steps.append(s)
                if len(unique_steps) >= 10:
                    break
            unique_steps.reverse()
            steps_lines = "\n<b>Activity this period:</b>\n"
            for s in unique_steps:
                safe = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                steps_lines += f"  {safe}\n"

        self.send(
            f"<b>STATUS — {hour}</b>\n"
            f"--------------------\n"
            f"Regime  : {regime.upper()}  |  VIX: {vix:.1f}\n"
            f"Account : Rs.{account_value:,.0f}  |  Day P&amp;L: Rs.{daily_pnl:+.2f}\n"
            f"Trades  : {trades_today} today\n"
            f"Risk    : {ks_line}\n"
            f"--------------------\n"
            f"<b>Open Positions:</b>\n{open_lines}"
            f"{steps_lines}"
        )
