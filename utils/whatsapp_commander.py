"""
WhatsApp Commander — listens for commands and free-form chat via Green API.

Commands:
  /status  — regime, VIX, account value, open trades
  /start   — start the bot if not running
  /stop    — stop the bot gracefully
  /report  — today's P&L summary
  /help    — list all commands

Any other message is answered by Claude (claude-opus-4-6) with full bot context.

Run standalone:
  python utils/whatsapp_commander.py
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import anyio
import pytz
import requests
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

IST = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger("WhatsAppCommander")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

INSTANCE_ID  = os.getenv("GREENAPI_INSTANCE_ID", "")
API_TOKEN    = os.getenv("GREENAPI_API_TOKEN", "")
OWNER_CHAT   = os.getenv("WHATSAPP_OWNER_CHAT_ID", "")   # e.g. 919876543210@c.us
PYTHON       = sys.executable
BOT_PID_FILE = ROOT / "journaling" / "bot.pid"

BASE_URL = f"https://api.green-api.com/waInstance{INSTANCE_ID}"


# ── Green API helpers ─────────────────────────────────────────────────────────

def send(text: str):
    """Send a WhatsApp message to the owner."""
    if not INSTANCE_ID or not API_TOKEN or not OWNER_CHAT:
        logger.warning("Green API credentials not set — skipping send.")
        return
    try:
        requests.post(
            f"{BASE_URL}/sendMessage/{API_TOKEN}",
            json={"chatId": OWNER_CHAT, "message": text},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Send failed: {e}")


def receive_notification() -> dict | None:
    """Poll for one incoming notification. Returns None if nothing pending."""
    if not INSTANCE_ID or not API_TOKEN:
        return None
    try:
        r = requests.get(
            f"{BASE_URL}/receiveNotification/{API_TOKEN}",
            timeout=60,
        )
        if r.status_code == 200 and r.text.strip() and r.text.strip() != "null":
            return r.json()
        return None
    except requests.exceptions.Timeout:
        return None
    except Exception as e:
        logger.warning(f"Receive error: {e}")
        return None


def delete_notification(receipt_id: int):
    """Acknowledge and remove a processed notification."""
    try:
        requests.delete(
            f"{BASE_URL}/deleteNotification/{API_TOKEN}/{receipt_id}",
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Delete notification failed: {e}")


def extract_message(notification: dict) -> tuple[str, str]:
    """
    Extract (chat_id, text) from a Green API notification body.
    Returns ('', '') if not a text message.
    """
    body = notification.get("body", {})
    if body.get("typeWebhook") != "incomingMessageReceived":
        return "", ""

    chat_id = body.get("senderData", {}).get("chatId", "")
    msg_data = body.get("messageData", {})

    if msg_data.get("typeMessage") == "textMessage":
        text = msg_data.get("textMessageData", {}).get("textMessage", "")
        return chat_id, text

    if msg_data.get("typeMessage") == "extendedTextMessage":
        text = msg_data.get("extendedTextMessageData", {}).get("text", "")
        return chat_id, text

    return chat_id, ""


# ── Bot process helpers ───────────────────────────────────────────────────────

def is_bot_running() -> bool:
    if not BOT_PID_FILE.exists():
        return False
    try:
        pid = int(BOT_PID_FILE.read_text().strip())
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def start_bot() -> str:
    if is_bot_running():
        return "Bot is already running."
    try:
        proc = subprocess.Popen(
            [PYTHON, str(ROOT / "main.py"), "--mode", "paper"],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        BOT_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        BOT_PID_FILE.write_text(str(proc.pid))
        return f"Bot started (PID {proc.pid})."
    except Exception as e:
        return f"Failed to start bot: {e}"


def stop_bot() -> str:
    if not is_bot_running():
        return "Bot is not running."
    try:
        pid = int(BOT_PID_FILE.read_text().strip())
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        BOT_PID_FILE.unlink(missing_ok=True)
        return f"Bot stopped (PID {pid})."
    except Exception as e:
        return f"Failed to stop bot: {e}"


# ── Status helpers ────────────────────────────────────────────────────────────

def get_status() -> str:
    running = is_bot_running()
    status_icon = "Running" if running else "Stopped (next start: 8:55 AM)"
    today = datetime.now(IST).strftime("%Y-%m-%d")

    log_file = ROOT / "journaling" / "logs" / f"bot_{today}.log"
    last_log = "No log for today yet"
    if log_file.exists():
        lines = [l.strip() for l in log_file.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        if lines:
            last_log = lines[-1].split("|")[-1].strip() if "|" in lines[-1] else lines[-1]

    account_val = "N/A"
    daily_pnl   = "N/A"
    regime_line = ""
    account_file = ROOT / "journaling" / "account_state.json"
    report_file  = ROOT / "journaling" / "reports" / f"report_{today}.json"

    if account_file.exists():
        try:
            d = json.loads(account_file.read_text())
            account_val = f"Rs.{d.get('account_value', 0):,.0f}"
            daily_pnl   = f"Rs.{d.get('daily_pnl', 0):+.2f}"
        except Exception:
            pass

    if report_file.exists():
        try:
            r = json.loads(report_file.read_text())
            if account_val == "N/A":
                account_val = f"Rs.{r.get('account_value', 0):,.0f}"
                daily_pnl   = f"Rs.{r.get('daily_pnl', 0):+.2f}"
            regime_line = f"\nRegime: {r.get('regime','N/A').upper()}  |  VIX: {r.get('vix', 0):.1f}"
        except Exception:
            pass

    return (
        f"BOT STATUS\n"
        f"------------------\n"
        f"Status  : {status_icon}\n"
        f"Account : {account_val}  |  Day P&L: {daily_pnl}"
        f"{regime_line}\n"
        f"Time    : {datetime.now(IST).strftime('%I:%M %p IST')}\n"
        f"------------------\n"
        f"Last log: {last_log[:80]}"
    )


def get_report() -> str:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    reports_dir = ROOT / "journaling" / "reports"
    report_file = reports_dir / f"report_{today}.json"

    label = "TODAY'S REPORT"
    if not report_file.exists():
        reports = sorted(reports_dir.glob("report_*.json"), reverse=True) if reports_dir.exists() else []
        if reports:
            report_file = reports[0]
            date_str = report_file.stem.replace("report_", "")
            label = f"LAST REPORT ({date_str})"
        else:
            account_file = ROOT / "journaling" / "account_state.json"
            account_val = "N/A"
            daily_pnl = "N/A"
            if account_file.exists():
                try:
                    d = json.loads(account_file.read_text())
                    account_val = f"Rs.{d.get('account_value', 0):,.0f}"
                    daily_pnl   = f"Rs.{d.get('daily_pnl', 0):+.2f}"
                except Exception:
                    pass
            return (
                f"NO REPORTS YET\n"
                f"------------------\n"
                f"Bot hasn't completed a trading day yet.\n"
                f"Account : {account_val}\n"
                f"Day P&L : {daily_pnl}\n"
                f"Time    : {datetime.now(IST).strftime('%I:%M %p IST')}"
            )

    try:
        r = json.loads(report_file.read_text())
        trades_line = (
            f"Trades : {r.get('trades', 0)}  W:{r.get('wins', 0)}  L:{r.get('losses', 0)}\n"
            f"Gross  : Rs.{r.get('gross_profit', 0):+.2f} / Rs.{r.get('gross_loss', 0):+.2f}\n"
            f"Charges: Rs.{r.get('total_charges', 0):.2f}\n"
        ) if r.get('trades', 0) > 0 else "Trades : 0 — Stay flat day\n"

        return (
            f"{label}\n"
            f"------------------\n"
            f"{trades_line}"
            f"Net P&L : Rs.{r.get('net_pnl', 0):+.2f}\n"
            f"Account : Rs.{r.get('account_value', 0):,.0f}\n"
            f"Regime  : {r.get('regime', 'N/A').upper()}  |  VIX: {r.get('vix', 0):.1f}"
        )
    except Exception as e:
        return f"Could not read report: {e}"


HELP_TEXT = (
    "ZerodhaBot Commands\n"
    "------------------\n"
    "/status  — bot status + account\n"
    "/start   — start the bot\n"
    "/stop    — stop the bot\n"
    "/report  — today's P&L report\n"
    "/help    — this message\n"
    "------------------\n"
    "Or just type anything to ask Claude about the bot!"
)


# ── Claude AI chat ────────────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """You are an AI assistant embedded in ZerodhaBot, an automated intraday trading bot for Indian stock markets (NSE). The user messages you from WhatsApp while away from their PC.

You have full access to the bot's codebase at C:/Users/nithi/zerodhaBot. You can read files, edit code, run scripts, and make real improvements — exactly like Claude Code CLI.

Key facts:
- Paper mode, Rs.5,000 capital, Nano tier (1% risk/trade, max 2 positions, 3 trades/day)
- Strategies: EMA pullback (strategies/ema_pullback.py), ETF momentum (strategies/etf_momentum.py)
- Kill switches: VIX >= 20 halts, VIX >= 30 kills all; NIFTY fall >= -1.5% halts, >= -2.5% kills
- Auto-starts Mon-Fri 8:55 AM IST via Windows Task Scheduler
- Config: config/config.yaml | Logs: journaling/logs/ | Reports: journaling/reports/

When the user asks you to make a change, DO IT — read the relevant file, make the edit, confirm what you changed.
Keep responses concise. No markdown — use plain text since this is WhatsApp."""


def build_bot_context() -> str:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    lines = [f"[Bot snapshot — {datetime.now(IST).strftime('%I:%M %p IST, %d %b %Y')}]"]
    lines.append(f"Bot running: {is_bot_running()}")

    account_file = ROOT / "journaling" / "account_state.json"
    if account_file.exists():
        try:
            d = json.loads(account_file.read_text())
            lines.append(f"Account: Rs.{d.get('account_value', 0):,.0f} | Day P&L: Rs.{d.get('daily_pnl', 0):+.2f}")
        except Exception:
            pass

    report_file = ROOT / "journaling" / "reports" / f"report_{today}.json"
    if report_file.exists():
        try:
            r = json.loads(report_file.read_text())
            lines.append(f"Regime: {r.get('regime', 'N/A')} | VIX: {r.get('vix', 0):.1f} | Trades: {r.get('trades', 0)}")
        except Exception:
            pass

    log_file = ROOT / "journaling" / "logs" / f"bot_{today}.log"
    if log_file.exists():
        try:
            log_lines = [l.strip() for l in log_file.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
            lines.append("Last log: " + (log_lines[-1].split("|")[-1].strip() if log_lines else "none"))
        except Exception:
            pass

    return "\n".join(lines)


async def _run_agent(prompt: str) -> str:
    full_prompt = f"{build_bot_context()}\n\nUser request: {prompt}"
    result_parts = []
    async for message in query(
        prompt=full_prompt,
        options=ClaudeAgentOptions(
            cwd=str(ROOT),
            allowed_tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
            permission_mode="acceptEdits",
            system_prompt=AGENT_SYSTEM_PROMPT,
            max_turns=20,
        ),
    ):
        if isinstance(message, ResultMessage):
            result_parts.append(message.result)
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    result_parts.append(block.text.strip())

    return "\n".join(result_parts).strip() or "Done."


def ask_claude(user_message: str) -> str:
    if not os.getenv("ANTHROPIC_API_KEY", ""):
        return "ANTHROPIC_API_KEY not set in .env — Claude chat is not available."
    try:
        return anyio.run(_run_agent, user_message)
    except Exception as e:
        logger.warning(f"Agent error: {e}")
        return f"Agent error: {str(e)[:120]}"


# ── Command dispatcher ────────────────────────────────────────────────────────

def handle(text: str):
    if text.startswith("/"):
        cmd = text.strip().lower().split()[0]
        if cmd == "/status":
            send(get_status())
        elif cmd == "/start":
            msg = start_bot()
            send(msg)
        elif cmd == "/stop":
            msg = stop_bot()
            send(msg)
        elif cmd == "/report":
            send(get_report())
        elif cmd == "/help":
            send(HELP_TEXT)
        else:
            send(f"Unknown command: {text[:40]}\nSend /help for available commands.")
    else:
        send("Thinking...")
        reply = ask_claude(text)
        send(reply)


# ── Main poll loop ────────────────────────────────────────────────────────────

def run():
    logger.info("WhatsApp Commander started. Listening for messages...")
    send("ZerodhaBot Commander online.\nSend /help for available commands.")

    while True:
        notification = receive_notification()
        if not notification:
            continue  # long-poll already waited — immediately retry

        receipt_id = notification.get("receiptId")
        chat_id, text = extract_message(notification)

        # Always delete to clear the queue
        if receipt_id:
            delete_notification(receipt_id)

        # Only process messages from the owner
        if chat_id and text and chat_id == OWNER_CHAT:
            logger.info(f"Message received: {text[:60]}")
            handle(text)


if __name__ == "__main__":
    run()
