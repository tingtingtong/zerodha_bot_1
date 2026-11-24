#!/bin/bash
# ============================================================
# Set up cron jobs for ZerodhaBot on cloud
# Server timezone: Asia/Kolkata (IST) — cron times are IST
# ============================================================

START_SCRIPT="$HOME/zerodhaBot/deploy/start_paper.sh"
DEPLOY_SCRIPT="$HOME/zerodhaBot/deploy/auto_deploy.sh"
DASHBOARD_SCRIPT="$HOME/zerodhaBot/deploy/start_dashboard_cloud.sh"
chmod +x "$START_SCRIPT" "$DASHBOARD_SCRIPT"

# Remove any existing ZerodhaBot cron entries
crontab -l 2>/dev/null | grep -v "zerodhaBot\|run_swing\|auto_deploy\|start_paper\|start_bot\|start_dashboard" | crontab - 2>/dev/null || true

# 1) Auto-deploy: pull latest code at 8:30 AM IST, Mon-Fri
(crontab -l 2>/dev/null; echo "30 8 * * 1-5 bash $DEPLOY_SCRIPT >> $HOME/zerodhaBot/journaling/logs/deploy.log 2>&1") | crontab -

# 2) Start dashboard: 8:45 AM IST, every day (dashboard runs 24/7)
(crontab -l 2>/dev/null; echo "45 8 * * * bash $DASHBOARD_SCRIPT >> $HOME/zerodhaBot/journaling/logs/dashboard.log 2>&1") | crontab -

# 3) Start bot: 9:00 AM IST, Mon-Fri (headless login + bot)
(crontab -l 2>/dev/null; echo "0 9 * * 1-5 bash $START_SCRIPT >> $HOME/zerodhaBot/journaling/logs/cron.log 2>&1") | crontab -

# 4) Swing momentum sleeve (paper): 3:50 PM IST, Mon-Fri, after market close
SWING_SCRIPT="$HOME/zerodhaBot/deploy/run_swing.sh"
chmod +x "$SWING_SCRIPT"
(crontab -l 2>/dev/null; echo "50 15 * * 1-5 bash $SWING_SCRIPT >> $HOME/zerodhaBot/journaling/logs/swing_cron.log 2>&1") | crontab -

echo "Cron jobs set:"
crontab -l | grep -E "auto_deploy|start_paper|start_dashboard|run_swing"
echo ""
echo "Schedule (IST — server timezone is Asia/Kolkata):"
echo "  8:30 AM — git pull (auto-deploy latest code)"
echo "  8:45 AM — dashboard starts (http://35.208.234.101:8501)"
echo "  9:00 AM — bot starts (headless login + paper mode)"
