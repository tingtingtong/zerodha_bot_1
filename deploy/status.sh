#!/bin/bash
# Quick status check — run anytime to see if bot is alive
cd ~/zerodhaBot

echo "====== ZerodhaBot Status ======"
echo "Time (IST): $(TZ='Asia/Kolkata' date)"
echo ""

# Is bot running?
if pgrep -f "main.py" > /dev/null; then
    PID=$(pgrep -f "main.py")
    echo "Bot: RUNNING (PID $PID)"
else
    echo "Bot: NOT running"
fi

# Today's log tail
LOG="journaling/logs/bot_$(date +%Y-%m-%d).log"
if [ -f "$LOG" ]; then
    echo ""
    echo "--- Last 10 log lines ---"
    tail -10 "$LOG"
fi

# Account state
STATE="journaling/account_state.json"
if [ -f "$STATE" ]; then
    echo ""
    echo "--- Account State ---"
    python3 -c "
import json
s = json.load(open('$STATE'))
cap = s.get('capital', 0)
start = s.get('starting_capital', 200000)
pnl = cap - start
sign = '+' if pnl >= 0 else ''
print(f'  Capital:   Rs.{cap:,.0f}')
print(f'  Total P&L: {sign}Rs.{pnl:,.0f}')
print(f'  Daily P&L: Rs.{s.get(\"daily_pnl\",0):,.0f}')
"
fi
echo "==============================="
