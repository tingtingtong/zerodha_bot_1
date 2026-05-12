"""
Local paper-trading runner — starts bot at 8:55 AM IST every trading day.
No Zerodha login required (uses yfinance fallback).
Run: python run_paper_local.py
"""
import subprocess
import sys
import time
import logging
import os
import atexit
from datetime import datetime, date
from pathlib import Path
import pytz

sys.path.insert(0, str(Path(__file__).parent))
from utils.time_utils import is_trading_day

IST = pytz.timezone("Asia/Kolkata")
ROOT = Path(__file__).parent
LOG_FILE = ROOT / "journaling" / "logs" / "paper_runner.log"
LOCK_FILE = ROOT / "journaling" / "runner.lock"


def acquire_lock():
    """Exit immediately if another runner is already running."""
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            # Check if that PID is actually alive
            import subprocess as _sp
            result = _sp.run(["wmic", "process", "where", f"processid={pid}",
                               "get", "processid"], capture_output=True, text=True)
            if str(pid) in result.stdout:
                print(f"Another runner is already running (PID {pid}). Exiting.")
                sys.exit(0)
        except Exception:
            pass  # stale lock — overwrite it
    LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: LOCK_FILE.unlink(missing_ok=True))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | paper_runner | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

START_HOUR, START_MIN = 8, 55
STOP_HOUR, STOP_MIN = 15, 30


def now_ist() -> datetime:
    return datetime.now(IST)


def seconds_until(h: int, m: int) -> float:
    now = now_ist()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    delta = (target - now).total_seconds()
    return delta if delta >= 0 else delta + 86400


def kill_existing_bots():
    """Kill any stray main.py processes before starting a fresh one."""
    import subprocess as _sp
    try:
        result = _sp.run(
            ["wmic", "process", "where", "commandline like '%main.py%'",
             "get", "processid"],
            capture_output=True, text=True
        )
        pids = [int(p.strip()) for p in result.stdout.splitlines()
                if p.strip().isdigit()]
        for pid in pids:
            _sp.run(["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True)
            logger.info(f"Killed stray bot PID {pid}")
    except Exception as e:
        logger.debug(f"kill_existing_bots: {e}")


def run_bot():
    kill_existing_bots()
    today_log = str(ROOT / "journaling" / "logs" / f"bot_{date.today()}.log")
    logger.info(f"Starting paper bot -> {today_log}")
    with open(today_log, "a", encoding="utf-8") as fout:
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "main.py"), "--mode", "paper"],
            stdout=fout, stderr=fout,
            cwd=str(ROOT),
        )
        logger.info(f"Bot PID: {proc.pid}")
        proc.wait()
        logger.info(f"Bot exited (code {proc.returncode})")


def main():
    logger.info("Paper runner started — waiting for 8:55 AM IST each trading day")
    while True:
        now = now_ist()

        if not is_trading_day(now.date()):
            logger.info(f"Non-trading day ({now.strftime('%A %Y-%m-%d')}) — sleeping 1h")
            time.sleep(3600)
            continue

        mins_now = now.hour * 60 + now.minute
        start_mins = START_HOUR * 60 + START_MIN
        stop_mins = STOP_HOUR * 60 + STOP_MIN

        if mins_now >= stop_mins:
            wait = seconds_until(START_HOUR, START_MIN)
            logger.info(f"Market closed — sleeping {wait/3600:.1f}h until tomorrow 8:55 AM")
            time.sleep(wait + 30)
            continue

        if mins_now < start_mins:
            wait = seconds_until(START_HOUR, START_MIN)
            logger.info(f"Pre-market — sleeping {wait/60:.1f} min until 8:55 AM")
            time.sleep(min(wait, 60))
            continue

        # Trading window: run bot
        run_bot()

        # If bot exits before market close, restart after 30s (crash recovery)
        now = now_ist()
        if now.hour * 60 + now.minute < stop_mins:
            logger.warning("Bot exited early — restarting in 30s")
            time.sleep(30)
        else:
            wait = seconds_until(START_HOUR, START_MIN)
            logger.info(f"Done for today — sleeping until tomorrow 8:55 AM ({wait/3600:.1f}h)")
            time.sleep(wait + 30)


if __name__ == "__main__":
    acquire_lock()
    main()
