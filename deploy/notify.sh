#!/bin/bash
# Usage: notify.sh "message"  — sends a Telegram message via the bot's notifier.
cd ~/zerodhaBot || exit 1
source venv/bin/activate
MSG="$1" python -c "
import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from utils.notification import TelegramNotifier
TelegramNotifier().send(os.environ['MSG'])
" 2>/dev/null || echo "[$(date)] notify failed: $1"
