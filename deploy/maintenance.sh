#!/bin/bash
# ============================================================
# Weekly log housekeeping + disk alert (cloud)
# - gzip bot_*.log older than 7 days; delete bot_*.log.gz older than 60 days
# - Telegram alert if root disk > 85% full
# Trade journals / audit / reports are never touched.
# ============================================================
cd ~/zerodhaBot || exit 1
LOGS=journaling/logs
find "$LOGS" -maxdepth 1 -name 'bot_*.log' -mtime +7 -exec gzip -f {} + 2>/dev/null
find "$LOGS" -maxdepth 1 -name 'bot_*.log.gz' -mtime +60 -delete 2>/dev/null
for f in cron.log deploy.log dashboard.log swing_cron.log; do
    [ -f "$LOGS/$f" ] && [ "$(stat -c %s "$LOGS/$f")" -gt 20000000 ] && tail -n 20000 "$LOGS/$f" > "$LOGS/$f.tmp" && mv "$LOGS/$f.tmp" "$LOGS/$f"
done
USED=$(df / | awk 'NR==2 {gsub("%","",$5); print $5}')
echo "[$(date)] maintenance done, disk ${USED}% used"
if [ "$USED" -gt 85 ]; then
    bash deploy/notify.sh "ZerodhaBot GCP: disk ${USED}% full - bot may fail to write logs"
fi
