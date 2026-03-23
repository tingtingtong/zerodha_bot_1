# ZerodhaBot - Semi-Auto Mode (you approve each trade)
# Bot generates signals, you press ENTER to execute or any key to skip

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  ZerodhaBot - Semi-Auto Mode" -ForegroundColor Cyan
Write-Host "  You approve each trade before execution" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# -- Step 1: Auto-login (skips if today's token already exists) ---------------
Write-Host "[1] Zerodha auto-login..." -ForegroundColor Yellow
python brokers/zerodha_auto_login.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Auto-login FAILED. Check .env for ZERODHA_* variables." -ForegroundColor Red
    Write-Host "    Or run manually: python brokers/zerodha_auth.py --token YOUR_REQUEST_TOKEN" -ForegroundColor Gray
    exit 1
}
Write-Host ""

# -- Step 2: Start dashboard --------------------------------------------------
Write-Host "[2] Starting dashboard at http://localhost:8501 ..." -ForegroundColor Yellow
Start-Process -NoNewWindow -FilePath "streamlit" -ArgumentList "run dashboard/app.py --server.headless true --server.port 8501"
Start-Sleep -Seconds 3
Write-Host ""

# -- Step 3: Start bot --------------------------------------------------------
Write-Host "[3] Starting bot in SEMI-AUTO mode..." -ForegroundColor Yellow
Write-Host "    ENTER = execute trade | any other key = skip" -ForegroundColor Gray
Write-Host ""
python main.py --mode semi_auto
