"""
Telegram Commander — listens for commands and free-form chat from the user.

Commands:
  /status  — regime, VIX, account value, open trades
  /start   — start the bot if not running
  /stop    — stop the bot gracefully
  /report  — today's P&L summary
  /help    — list all commands

Any other message is answered by Claude (claude-opus-4-6) with full bot context.

Run standalone:
  python utils/telegram_commander.py
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic
import anyio
import pytz
import requests
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

IST = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger("TelegramCommander")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PYTHON  = sys.executable
BOT_PID_FILE = ROOT / "journaling" / "bot.pid"


# ── Telegram helpers ──────────────────────────────────────────────────────────

def send(text: str):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        logger.warning(f"Send failed: {e}")


def get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
            timeout=35,
        )
        return r.json().get("result", [])
    except Exception:
        return []


# ── Bot process helpers ───────────────────────────────────────────────────────

def is_bot_running() -> bool:
    if not BOT_PID_FILE.exists():
        return False
    try:
        pid = int(BOT_PID_FILE.read_text().strip())
        # Check if process is alive (Windows)
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def start_bot(mode: str = "live") -> str:
    if mode not in ("live", "semi_auto", "paper"):
        return f"Unknown mode: {mode}. Use live, semi_auto, or paper."
    if is_bot_running():
        return "Bot is already running. Send /stop first."
    try:
        proc = subprocess.Popen(
            [PYTHON, str(ROOT / "main.py"), "--mode", mode],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        BOT_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        BOT_PID_FILE.write_text(str(proc.pid))
        mode_label = {"live": "🔴 LIVE", "semi_auto": "🟡 SEMI-AUTO", "paper": "📄 PAPER"}[mode]
        return f"Bot started in {mode_label} mode (PID {proc.pid})."
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
    status_icon = "🟢 Running" if running else "🔴 Stopped (next start: 8:55 AM)"
    today = datetime.now(IST).strftime("%Y-%m-%d")

    # Last log line
    log_file = ROOT / "journaling" / "logs" / f"bot_{today}.log"
    last_log = "No log for today yet"
    if log_file.exists():
        lines = [l.strip() for l in log_file.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        if lines:
            last_log = lines[-1].split("|")[-1].strip() if "|" in lines[-1] else lines[-1]

    # Account — prefer account_state.json, fall back to today's report
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
            regime_line = f"\nRegime  : {r.get('regime','N/A').upper()}  |  VIX: {r.get('vix', 0):.1f}"
        except Exception:
            pass

    return (
        f"🤖 <b>BOT STATUS</b>\n"
        f"——————————————————\n"
        f"Status  : {status_icon}\n"
        f"Account : {account_val}  |  Day P&amp;L: {daily_pnl}"
        f"{regime_line}\n"
        f"Time    : {datetime.now(IST).strftime('%I:%M %p IST')}\n"
        f"——————————————————\n"
        f"Last log: <i>{last_log[:80]}</i>"
    )


def get_report() -> str:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    reports_dir = ROOT / "journaling" / "reports"
    report_file = reports_dir / f"report_{today}.json"

    # If today's report doesn't exist, find the most recent one
    label = "TODAY'S REPORT"
    if not report_file.exists():
        reports = sorted(reports_dir.glob("report_*.json"), reverse=True) if reports_dir.exists() else []
        if reports:
            report_file = reports[0]
            date_str = report_file.stem.replace("report_", "")
            label = f"LAST REPORT ({date_str})"
        else:
            # No reports at all — show live account state
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
                f"📋 <b>NO REPORTS YET</b>\n"
                f"——————————————————\n"
                f"Bot hasn't completed a trading day yet.\n"
                f"Account : {account_val}\n"
                f"Day P&amp;L: {daily_pnl}\n"
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
            f"📋 <b>{label}</b>\n"
            f"——————————————————\n"
            f"{trades_line}"
            f"Net P&amp;L: Rs.{r.get('net_pnl', 0):+.2f}\n"
            f"Account: Rs.{r.get('account_value', 0):,.0f}\n"
            f"Regime : {r.get('regime', 'N/A').upper()}  |  VIX: {r.get('vix', 0):.1f}"
        )
    except Exception as e:
        return f"📋 Could not read report: {e}"


HELP_TEXT = (
    "🤖 <b>ZerodhaBot Commands</b>\n"
    "——————————————————\n"
    "/status    — bot status + account\n"
    "/start     — start bot in LIVE mode\n"
    "/runLive   — start bot in LIVE mode\n"
    "/runPaper  — start bot in PAPER mode\n"
    "/stop      — stop the bot\n"
    "/report    — today's P&amp;L report\n"
    "/help      — this message\n"
    "——————————————————\n"
    "💬 Or just <b>type anything</b> to ask Claude about the bot!"
)

# ── Claude AI chat ────────────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """You are an AI assistant embedded in ZerodhaBot, an automated intraday trading bot for Indian stock markets (NSE). The user messages you from Telegram while away from their PC.

You have full access to the bot's codebase at C:/Users/nithi/zerodhaBot. You can read files, edit code, run scripts, and make real improvements — exactly like Claude Code CLI.

Key facts:
- Live mode, Rs.10,000 capital, risk managed by capital tier (config/capital_tiers.py)
- Strategies: EMA pullback (strategies/ema_pullback.py), ETF momentum (strategies/etf_momentum.py)
- Kill switches: VIX >= 20 halts, VIX >= 30 kills all; NIFTY fall >= -1.5% halts, >= -2.5% kills
- /start and /runLive start real trades; /runPaper starts paper simulation
- Auto-starts Mon-Fri 8:55 AM IST via Windows Task Scheduler
- Config: config/config.yaml | Logs: journaling/logs/ | Reports: journaling/reports/

When the user asks you to make a change, DO IT — read the relevant file, make the edit, confirm what you changed.
Keep responses concise. No markdown — use plain text since this is Telegram."""


def build_bot_context() -> str:
    """Snapshot of current bot state prepended to every agent prompt."""
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
    """Run Claude agent with file access tools and return the result."""
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
    """Route user message to Claude agent and return the response."""
    if not os.getenv("ANTHROPIC_API_KEY", ""):
        return "⚠️ ANTHROPIC_API_KEY not set in .env — Claude chat is not available."
    try:
        return anyio.run(_run_agent, user_message)
    except Exception as e:
        logger.warning(f"Agent error: {e}")
        return f"⚠️ Agent error: {str(e)[:120]}"


# ── Command dispatcher ────────────────────────────────────────────────────────

def handle(text: str):
    if text.startswith("/"):
        cmd = text.strip().lower().split()[0]
        if cmd == "/status":
            send(get_status())
        elif cmd in ("/start", "/runlive"):
            msg = start_bot("live")
            send(f"{'✅' if 'started' in msg else '⚠️'} {msg}")
        elif cmd == "/runpaper":
            msg = start_bot("paper")
            send(f"{'✅' if 'started' in msg else '⚠️'} {msg}")
        elif cmd == "/stop":
            msg = stop_bot()
            send(f"{'✅' if 'stopped' in msg else '⚠️'} {msg}")
        elif cmd == "/report":
            send(get_report())
        elif cmd == "/help":
            send(HELP_TEXT)
        else:
            send(f"Unknown command: <code>{text[:40]}</code>\nSend /help for available commands.")
    else:
        # Free-form message — ask Claude
        send("💭 Thinking...")
        reply = ask_claude(text)
        send(reply)


# ── Main poll loop ────────────────────────────────────────────────────────────

def run():
    logger.info("Telegram Commander started. Listening for commands...")

    # Drain any messages that arrived while the bot was offline — don't replay them
    offset = 0
    stale = get_updates(offset)
    if stale:
        offset = stale[-1]["update_id"] + 1
        logger.info(f"Drained {len(stale)} stale updates (offset now {offset})")

    send("🤖 <b>ZerodhaBot Commander online.</b>\nSend /help for available commands.")

    while True:
        updates = get_updates(offset)
        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "")
            if chat_id != CHAT_ID:
                continue
            if text:
                logger.info(f"Message received: {text}")
                handle(text)
        time.sleep(1)


if __name__ == "__main__":
    run()
