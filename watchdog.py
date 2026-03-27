"""
ZerodhaBot Watchdog — runs in the background, starts the bot at 9:00 AM IST
every weekday and restarts it if it crashes before 15:30 IST.
Add this to the Windows Startup folder to run on login.
"""
import subprocess
import sys
import time
import logging
from datetime import datetime, date
from pathlib import Path
import pytz
sys.path.insert(0, str(Path(__file__).parent))
from utils.time_utils import is_trading_day

LOG_FILE = Path(__file__).parent / "logs" / "watchdog.log"
LOG_FILE.parent.mkdir(exist_ok=True)

_handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
# Only add console handler if stdout supports unicode (not pythonw.exe)
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    _handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | watchdog | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
PYTHON = sys.executable
ROOT   = Path(__file__).parent
BOT_SCRIPT       = str(ROOT / "main.py")
COMMANDER_SCRIPT = str(ROOT / "utils" / "telegram_commander.py")
START_HOUR, START_MIN = 9, 0    # 9:00 AM IST
STOP_HOUR, STOP_MIN  = 15, 30  # 3:30 PM IST — stop restarting after this

_commander_proc = None  # global handle so watchdog can restart it if needed


def now_ist() -> datetime:
    return datetime.now(IST)


def is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5  # Mon=0 … Fri=4


def seconds_until(target_h: int, target_m: int) -> float:
    now = now_ist()
    target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    delta = (target - now).total_seconds()
    return delta if delta >= 0 else delta + 86400


def bot_log_path() -> str:
    return str(ROOT / "logs" / f"bot_{date.today()}.log")


def ensure_commander_running():
    """Start telegram_commander.py if it isn't already running."""
    global _commander_proc
    if _commander_proc is not None and _commander_proc.poll() is None:
        return  # still alive
    log_path = str(ROOT / "logs" / f"commander_{date.today()}.log")
    with open(log_path, "a", encoding="utf-8") as fout:
        _commander_proc = subprocess.Popen(
            [PYTHON, COMMANDER_SCRIPT],
            stdout=fout, stderr=fout,
            cwd=str(ROOT),
        )
    logger.info(f"Telegram Commander started (PID {_commander_proc.pid})")


def run_bot() -> int:
    log_path = bot_log_path()
    logger.info(f"Starting bot -> log: {log_path}")
    with open(log_path, "a", encoding="utf-8") as fout:
        proc = subprocess.Popen(
            [PYTHON, BOT_SCRIPT, "--mode", "live"],
            stdout=fout, stderr=fout,
            cwd=str(Path(__file__).parent),
        )
        logger.info(f"Bot PID: {proc.pid}")
        proc.wait()
        rc = proc.returncode
        logger.info(f"Bot exited with code {rc}")
        return rc


def main():
    logger.info("Watchdog started.")
    ensure_commander_running()  # Start Telegram commander immediately on boot
    while True:
        now = now_ist()
        ensure_commander_running()  # Restart if it crashed

        if not is_trading_day(now.date()):
            reason = "Weekend" if not is_weekday(now) else "Market holiday"
            logger.info(f"{reason} ({now.strftime('%Y-%m-%d %A')}) — sleeping 1h")
            time.sleep(3600)
            continue

        market_started = now.hour * 60 + now.minute >= START_HOUR * 60 + START_MIN
        market_closed  = now.hour * 60 + now.minute >= STOP_HOUR  * 60 + STOP_MIN

        if market_closed:
            # Wait until 9:00 AM next day
            wait = seconds_until(START_HOUR, START_MIN)
            logger.info(f"Market closed — sleeping {wait/3600:.1f}h until next 9:00 AM")
            time.sleep(wait + 30)
            continue

        if not market_started:
            wait = seconds_until(START_HOUR, START_MIN)
            logger.info(f"Pre-market — sleeping {wait/60:.1f} min until 9:00 AM")
            time.sleep(min(wait, 60))
            continue

        # It's a weekday between 9:00 and 15:30 — run (or restart) the bot
        run_bot()

        # After bot exits, check if we should restart
        now = now_ist()
        if now.hour * 60 + now.minute < STOP_HOUR * 60 + STOP_MIN:
            logger.warning("Bot exited before 15:30 — restarting in 30s...")
            time.sleep(30)
        else:
            logger.info("Bot finished after market close — done for today.")
            wait = seconds_until(START_HOUR, START_MIN)
            time.sleep(wait + 30)


if __name__ == "__main__":
    main()
