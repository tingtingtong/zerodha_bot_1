import logging
import os

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
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            resp = requests.post(
                url, json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
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
        icon = "✅" if net_pnl > 0 else "❌"
        self.send(
            f"{icon} <b>TRADE EXIT</b>\n"
            f"{symbol}  {qty} @ Rs.{exit_price:.2f}\n"
            f"Net P&L: Rs.{net_pnl:+.2f}  Reason: {reason}"
        )

    def send_kill_switch(self, reason, account_value, daily_pnl):
        self.send(
            f"🚨 <b>KILL SWITCH TRIGGERED</b>\n"
            f"Reason: {reason}\n"
            f"Daily P&L: Rs.{daily_pnl:+.2f}  Account: Rs.{account_value:,.0f}"
        )

    def send_daily_summary(self, report: dict):
        self.send(
            f"📊 <b>DAILY SUMMARY</b>\n"
            f"Trades: {report.get('trades', 0)}  W:{report.get('wins', 0)} L:{report.get('losses', 0)}\n"
            f"Net P&L: Rs.{report.get('net_pnl', 0):+.2f}\n"
            f"Account: Rs.{report.get('account_value', 0):,.0f}"
        )

    def send_hourly_status(self, hour: str, regime: str, vix: float,
                           account_value: float, daily_pnl: float,
                           open_trades: list, trades_today: int,
                           kill_switch: bool, steps: list = None):
        open_lines = ""
        if open_trades:
            for t in open_trades:
                pnl = t.get("unrealised_pnl", 0)
                icon = "🟢" if pnl >= 0 else "🔴"
                open_lines += f"  {icon} {t['symbol']}  qty={t['qty']}  P&amp;L: Rs.{pnl:+.0f}\n"
        else:
            open_lines = "  None\n"

        ks_line = "🚨 KILL SWITCH ACTIVE" if kill_switch else "✅ Normal"

        steps_lines = ""
        if steps:
            steps_lines = "\n<b>What the bot did this hour:</b>\n"
            for s in steps:
                steps_lines += f"  {s}\n"

        self.send(
            f"🕐 <b>HOURLY STATUS — {hour}</b>\n"
            f"——————————————————\n"
            f"Regime  : {regime.upper()}  |  VIX: {vix:.1f}\n"
            f"Account : Rs.{account_value:,.0f}  |  Day P&amp;L: Rs.{daily_pnl:+.2f}\n"
            f"Trades  : {trades_today} today\n"
            f"Risk    : {ks_line}\n"
            f"——————————————————\n"
            f"<b>Open Positions:</b>\n{open_lines}"
            f"{steps_lines}"
        )
