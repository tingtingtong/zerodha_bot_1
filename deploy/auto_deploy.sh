#!/bin/bash
# ============================================================
# ZerodhaBot Auto-Deploy — runs at 8:30 AM IST before bot start
# Pulls latest code from GitHub, sends Telegram alert if updated
# ============================================================

cd ~/zerodhaBot || exit 1

BEFORE=$(git rev-parse HEAD)
git fetch origin master --quiet 2>&1
AFTER=$(git rev-parse origin/master)

if [ "$BEFORE" = "$AFTER" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No new commits. Already up to date."
    exit 0
fi

# New commits — pull and log what changed
echo "[$(date '+%Y-%m-%d %H:%M:%S')] New commits found. Pulling..."
git pull --ff-only origin master 2>&1

COMMITS=$(git log --oneline "$BEFORE".."$AFTER" 2>/dev/null | head -5)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Updated to $(git rev-parse --short HEAD)"
echo "$COMMITS"

# Notify via Telegram
source venv/bin/activate 2>/dev/null || true
python - <<PYEOF
import os, requests
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('${HOME}/zerodhaBot/.env'))
token = os.getenv('TELEGRAM_BOT_TOKEN', '')
chat  = os.getenv('TELEGRAM_CHAT_ID', '')
if token and chat:
    msg = "🚀 <b>ZerodhaBot auto-deployed</b>\nNew code pulled from GitHub:\n<pre>${COMMITS}</pre>"
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
        timeout=5,
    )
PYEOF

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deploy complete."
