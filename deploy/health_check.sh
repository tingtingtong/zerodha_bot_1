#!/bin/bash
# ============================================================
# Bot health check — cron at 09:25 IST Mon-Fri (skips NSE holidays via bot log check)
# Alerts via Telegram if main.py is not running, today's log is empty, or disk is nearly full.
# ============================================================
cd ~/zerodhaBot || exit 1
source venv/bin/activate
python -c "
import sys; sys.path.insert(0, '.')
from datetime import datetime
from utils.time_utils import is_trading_day
sys.exit(0 if is_trading_day(datetime.now().date()) else 1)
" 2>/dev/null || { echo "[$(date)] not a trading day - skipped"; exit 0; }
DATE=$(date +%Y-%m-%d)
LOG="journaling/logs/bot_${DATE}.log"
PROBLEMS=""
pgrep -f "main.py --mode" >/dev/null || PROBLEMS="${PROBLEMS} bot process not running;"
[ -s "$LOG" ] || PROBLEMS="${PROBLEMS} today's log empty/missing;"
USED=$(df / | awk 'NR==2 {gsub("%","",$5); print $5}')
[ "$USED" -gt 85 ] && PROBLEMS="${PROBLEMS} disk ${USED}% full;"
if [ -n "$PROBLEMS" ]; then
    echo "[$(date)] UNHEALTHY:${PROBLEMS}"
    bash deploy/notify.sh "ZerodhaBot GCP ${DATE} UNHEALTHY:${PROBLEMS}"
else
    echo "[$(date)] healthy"
fi
