# ============================================================
# sync_gcp_logs.ps1
# Pulls today's trade logs + account state from GCP to local
# so the Streamlit dashboard shows live GCP bot data.
#
# Runs every 5 minutes during market hours via Task Scheduler.
# Set up once with: deploy\setup_gcp_sync_task.ps1 (as Admin)
# ============================================================

$INSTANCE   = "zerodhabot"
$ZONE       = "us-central1-a"
$GCP_USER   = "shetty_nith"
$GCP_ROOT   = "/home/shetty_nith/zerodhaBot"
$LOCAL_ROOT = "C:\Users\nithi\zerodhaBot"
$LOG_FILE   = "$LOCAL_ROOT\journaling\logs\gcp_sync.log"

$_gcmd = Get-Command gcloud -ErrorAction SilentlyContinue
$GCLOUD = if ($_gcmd) { $_gcmd.Source } else { "C:\Users\nithi\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" }

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line -Encoding UTF8
}

# Ensure local dirs exist
New-Item -ItemType Directory -Force -Path "$LOCAL_ROOT\journaling\logs"  | Out-Null
New-Item -ItemType Directory -Force -Path "$LOCAL_ROOT\reporting\output" | Out-Null

$today = Get-Date -Format "yyyy-MM-dd"

Log "--- GCP sync start (today=$today) ---"

function GcpScp($src, $dest) {
    & $GCLOUD compute scp --zone=$ZONE --quiet `
        "${GCP_USER}@${INSTANCE}:${src}" $dest 2>&1 | Out-Null
    return $LASTEXITCODE
}

# 1. Account state (most important — drives dashboard header value)
$rc = GcpScp "$GCP_ROOT/journaling/account_state.json" "$LOCAL_ROOT\journaling\account_state.json"
if ($rc -eq 0) { Log "account_state.json OK" } else { Log "account_state.json SKIP (not found or error)" }

# 2. Today's trade log
$rc = GcpScp "$GCP_ROOT/journaling/logs/trades_$today.json" "$LOCAL_ROOT\journaling\logs\trades_$today.json"
if ($rc -eq 0) { Log "trades_$today.json OK" } else { Log "trades_$today.json SKIP" }

# 3. Today's audit log
$rc = GcpScp "$GCP_ROOT/journaling/logs/audit_$today.jsonl" "$LOCAL_ROOT\journaling\logs\audit_$today.jsonl"
if ($rc -eq 0) { Log "audit_$today.jsonl OK" } else { Log "audit_$today.jsonl SKIP" }

# 4. Today's daily report
$rc = GcpScp "$GCP_ROOT/reporting/output/report_$today.json" "$LOCAL_ROOT\reporting\output\report_$today.json"
if ($rc -eq 0) { Log "report_$today.json OK" } else { Log "report_$today.json SKIP" }

# 5. Historical reports for Analytics page (last 60 days — skips files already present)
#    Only pulls files that don't exist locally yet (avoids re-downloading old closed days)
$startDate = (Get-Date).AddDays(-60)
$d = $startDate
while ($d -lt (Get-Date).AddDays(-1)) {
    $ds = $d.ToString("yyyy-MM-dd")
    $localReport = "$LOCAL_ROOT\reporting\output\report_$ds.json"
    $localTrades = "$LOCAL_ROOT\journaling\logs\trades_$ds.json"
    if (-not (Test-Path $localReport)) {
        GcpScp "$GCP_ROOT/reporting/output/report_$ds.json" "$LOCAL_ROOT\reporting\output\report_$ds.json" | Out-Null
    }
    if (-not (Test-Path $localTrades)) {
        GcpScp "$GCP_ROOT/journaling/logs/trades_$ds.json" "$LOCAL_ROOT\journaling\logs\trades_$ds.json" | Out-Null
    }
    $d = $d.AddDays(1)
}

Log "--- GCP sync complete ---"
