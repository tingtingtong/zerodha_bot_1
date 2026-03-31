#!/bin/bash
# ============================================================
# Set up cron jobs for ZerodhaBot on cloud (IST timezone)
# Oracle Cloud / Google Cloud Ubuntu VM
#
# Cron times are in UTC. IST = UTC+5:30
#   9:00 AM IST = 3:30 AM UTC
# ============================================================

SCRIPT="$HOME/zerodhaBot/deploy/start_paper.sh"
chmod +x "$SCRIPT"

# Remove any existing ZerodhaBot cron entries
crontab -l 2>/dev/null | grep -v "zerodhaBot" | crontab - 2>/dev/null || true

# Add new cron entry: 3:30 AM UTC = 9:00 AM IST, weekdays only (Mon-Fri)
(crontab -l 2>/dev/null; echo "30 3 * * 1-5 bash $SCRIPT >> $HOME/zerodhaBot/logs/cron.log 2>&1") | crontab -

echo "Cron job set:"
crontab -l | grep zerodhaBot

echo ""
echo "Bot will auto-start at 9:00 AM IST (3:30 AM UTC) every weekday."
echo "To verify: crontab -l"
echo "To check logs: tail -f ~/zerodhaBot/journaling/logs/bot_\$(date +%Y-%m-%d).log"
