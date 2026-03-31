#!/bin/bash
# ============================================================
# ZerodhaBot — Paper Mode Start Script (Cloud)
# No Zerodha login required. Simulated broker only.
# Runs bot from 9:00 AM to ~3:30 PM IST then exits.
# ============================================================

cd ~/zerodhaBot
source venv/bin/activate

DATE=$(date +%Y-%m-%d)
LOG="journaling/logs/bot_${DATE}.log"
mkdir -p journaling/logs

echo "[$(date)] ==============================" >> "$LOG"
echo "[$(date)] ZerodhaBot PAPER mode starting" >> "$LOG"
echo "[$(date)] Capital: Rs.2,00,000 (simulated)" >> "$LOG"
echo "[$(date)] ==============================" >> "$LOG"

# Sync system clock (important for accurate timestamps)
sudo timedatectl set-ntp true 2>/dev/null || true

# Run bot in paper mode — no login needed
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
