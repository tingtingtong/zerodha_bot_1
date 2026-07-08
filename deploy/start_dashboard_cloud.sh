#!/bin/bash
# ============================================================
# start_dashboard_cloud.sh
# Starts the Streamlit dashboard on GCP.
# Accessible at http://35.208.234.101:8501 from any device.
# Kills any existing dashboard process before starting fresh.
# ============================================================

cd ~/zerodhaBot || exit 1
source venv/bin/activate

DASHBOARD="dashboard/app.py"
PIDFILE="journaling/dashboard.pid"
LOG="journaling/logs/dashboard_$(date +%Y-%m-%d).log"

mkdir -p journaling/logs

# Kill existing dashboard if running
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Stopping existing dashboard (PID $OLD_PID)"
        kill "$OLD_PID" 2>/dev/null
        sleep 2
    fi
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Streamlit dashboard..."
nohup streamlit run "$DASHBOARD" \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.fileWatcherType none \
    --browser.gatherUsageStats false \
    >> "$LOG" 2>&1 &

DASHBOARD_PID=$!
echo $DASHBOARD_PID > "$PIDFILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Dashboard started (PID $DASHBOARD_PID) -> http://35.208.234.101:8501"
