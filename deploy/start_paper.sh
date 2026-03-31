#!/bin/bash
# ============================================================
# ZerodhaBot — Paper Mode Start Script (Cloud)
# Performs headless Zerodha login (requests + pyotp, no browser)
# then runs the bot with Zerodha data provider.
# ============================================================

cd ~/zerodhaBot
source venv/bin/activate

DATE=$(date +%Y-%m-%d)
LOG="journaling/logs/bot_${DATE}.log"
mkdir -p journaling/logs

echo "[$(date)] ==============================" >> "$LOG"
echo "[$(date)] ZerodhaBot PAPER mode starting" >> "$LOG"
echo "[$(date)] ==============================" >> "$LOG"

# Sync system clock (important for accurate timestamps and TOTP)
sudo timedatectl set-ntp true 2>/dev/null || true

# ------------------------------------------------------------------
# Headless Zerodha login (no Playwright/browser — works on GCP)
# Skips automatically if today's token already exists
# ------------------------------------------------------------------
echo "[$(date)] Running headless login..." >> "$LOG"
python brokers/zerodha_headless_login.py >> "$LOG" 2>&1
LOGIN_CODE=$?

if [ $LOGIN_CODE -ne 0 ]; then
    echo "[$(date)] ERROR: Headless login failed (code $LOGIN_CODE). Aborting." >> "$LOG"
    python -c "
import sys; sys.path.insert(0,'.')
try:
    from dotenv import load_dotenv; load_dotenv()
    from utils.notification import TelegramNotifier
    TelegramNotifier().send('ZerodhaBot FAILED to start: Zerodha login error. Check logs.')
except Exception: pass
" 2>/dev/null || true
    exit 1
fi

echo "[$(date)] Login OK. Starting bot..." >> "$LOG"

# Run bot in paper mode with Zerodha data
python main.py --mode paper >> "$LOG" 2>&1
EXIT_CODE=$?

echo "[$(date)] Bot exited (code $EXIT_CODE)" >> "$LOG"

# Send Telegram summary notification
python -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '.')
try:
    from dotenv import load_dotenv; load_dotenv()
    from utils.notification import TelegramNotifier
    state_file = Path('journaling/account_state.json')
    if state_file.exists():
        s = json.loads(state_file.read_text())
        cap = s.get('capital', 200000)
        dpnl = s.get('daily_pnl', 0)
        sign = '+' if dpnl >= 0 else ''
        msg = f'ZerodhaBot PAPER | {\"$(date +%Y-%m-%d)\"}\nAccount: Rs.{cap:,.0f}\nToday P&L: {sign}Rs.{dpnl:,.0f}'
        TelegramNotifier().send(msg)
except Exception as e:
    print(f'Notification failed: {e}')
" 2>/dev/null || true
