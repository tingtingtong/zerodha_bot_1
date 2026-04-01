#!/bin/bash
# ============================================================
# Set up cron jobs for ZerodhaBot on cloud (IST timezone)
# Oracle Cloud / Google Cloud Ubuntu VM
#
# Cron times are in UTC. IST = UTC+5:30
#   3:00 AM UTC = 8:30 AM IST  — git pull (auto-deploy)
#   3:30 AM UTC = 9:00 AM IST  — start bot
# ============================================================

START_SCRIPT="$HOME/zerodhaBot/deploy/start_paper.sh"
DEPLOY_SCRIPT="$HOME/zerodhaBot/deploy/auto_deploy.sh"
chmod +x "$START_SCRIPT"

# Remove any existing ZerodhaBot cron entries
crontab -l 2>/dev/null | grep -v "zerodhaBot\|auto_deploy\|start_paper\|start_bot" | crontab - 2>/dev/null || true

# 1) Auto-deploy: pull latest code at 3:00 AM UTC (8:30 AM IST), Mon-Fri
(crontab -l 2>/dev/null; echo "0 3 * * 1-5 bash $DEPLOY_SCRIPT >> $HOME/zerodhaBot/journaling/logs/deploy.log 2>&1") | crontab -

# 2) Start bot: 3:30 AM UTC (9:00 AM IST), Mon-Fri
(crontab -l 2>/dev/null; echo "30 3 * * 1-5 bash $START_SCRIPT >> $HOME/zerodhaBot/journaling/logs/cron.log 2>&1") | crontab -

echo "Cron jobs set:"
crontab -l | grep -E "auto_deploy|start_paper"
echo ""
echo "Schedule (IST):"
echo "  8:30 AM — git pull (auto-deploy latest code)"
echo "  9:00 AM — bot starts"
