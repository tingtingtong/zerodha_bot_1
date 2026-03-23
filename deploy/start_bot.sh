#!/bin/bash
# Daily startup script — run by cron at 8:45 AM IST (3:15 UTC)
# Logs to ~/zerodhaBot/journaling/logs/bot_YYYY-MM-DD.log

cd ~/zerodhaBot
source venv/bin/activate

DATE=$(date +%Y-%m-%d)
LOG="journaling/logs/bot_${DATE}.log"

echo "[$(date)] Starting ZerodhaBot..." >> "$LOG"

# Step 1: Sync system clock (critical for TOTP)
sudo timedatectl set-ntp true 2>/dev/null || true

# Step 2: Auto-login to Zerodha
echo "[$(date)] Running auto-login..." >> "$LOG"
python brokers/zerodha_auto_login.py >> "$LOG" 2>&1

if [ $? -ne 0 ]; then
    echo "[$(date)] Auto-login FAILED. Aborting." >> "$LOG"
    python -c "
import sys; sys.path.insert(0,'.')
from dotenv import load_dotenv; load_dotenv()
from utils.notification import TelegramNotifier
TelegramNotifier().send('🚨 <b>ZerodhaBot auto-login FAILED</b>\nBot did not start today. Check logs.')
" >> "$LOG" 2>&1
    exit 1
fi

# Step 3: Run the bot
echo "[$(date)] Starting main bot loop..." >> "$LOG"
python main.py --mode paper >> "$LOG" 2>&1

echo "[$(date)] Bot session ended." >> "$LOG"
