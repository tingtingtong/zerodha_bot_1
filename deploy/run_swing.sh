#!/bin/bash
# ============================================================
# Swing momentum sleeve (paper) — daily after market close
# Rebalances on the last trading day of the month; otherwise just marks equity.
# State: journaling/swing_state.json (gitignored, per-machine)
# ============================================================
cd ~/zerodhaBot || exit 1
source venv/bin/activate
export BOT_INSTANCE="GCP"
echo "[$(date)] swing run" >> journaling/swing_run.log
python -m swing.momentum_rebalancer >> journaling/swing_run.log 2>&1
