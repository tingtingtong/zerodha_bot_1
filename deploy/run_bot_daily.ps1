Set-Location "C:\Users\nithi\zerodhaBot"
$DATE = Get-Date -Format "yyyy-MM-dd"
$PYTHON = "C:\Users\nithi\AppData\Local\Programs\Python\Python312\python.exe"
$LOG = "C:\Users\nithi\zerodhaBot\journaling\logs\scheduler_$DATE.log"
$BOT_MODE = "paper"

"[$(Get-Date)] Starting ZerodhaBot (mode: $BOT_MODE)..." | Out-File -Append $LOG

# Step 1: Wait for internet before attempting login
"[$(Get-Date)] Waiting for network..." | Out-File -Append $LOG
$networkReady = $false
for ($i = 1; $i -le 12; $i++) {
    if (Test-Connection -ComputerName "kite.zerodha.com" -Count 1 -Quiet) {
        $networkReady = $true
        "[$(Get-Date)] Network ready." | Out-File -Append $LOG
        break
    }
    "[$(Get-Date)] Network not ready, retrying in 10s ($i/12)..." | Out-File -Append $LOG
    Start-Sleep -Seconds 10
}

if (-not $networkReady) {
    "[$(Get-Date)] Network unavailable after 2 minutes. Aborting." | Out-File -Append $LOG
    & $PYTHON -c "
import sys; sys.path.insert(0,'C:\Users\nithi\zerodhaBot')
from dotenv import load_dotenv; load_dotenv('C:\Users\nithi\zerodhaBot\.env')
from utils.notification import TelegramNotifier
TelegramNotifier().send('🚨 <b>ZerodhaBot failed to start</b>\nNo internet connection at startup.')
"
    exit 1
}

# Step 2: Auto-login with retry
"[$(Get-Date)] Running auto-login..." | Out-File -Append $LOG
$loginSuccess = $false
for ($i = 1; $i -le 3; $i++) {
    & $PYTHON "C:\Users\nithi\zerodhaBot\brokers\zerodha_auto_login.py" >> $LOG 2>&1
    if ($LASTEXITCODE -eq 0) {
        $loginSuccess = $true
        "[$(Get-Date)] Auto-login succeeded (attempt $i)." | Out-File -Append $LOG
        break
    }
    "[$(Get-Date)] Auto-login failed (attempt $i/3), retrying in 20s..." | Out-File -Append $LOG
    Start-Sleep -Seconds 20
}

if (-not $loginSuccess) {
    "[$(Get-Date)] Auto-login FAILED after 3 attempts." | Out-File -Append $LOG
    & $PYTHON -c "
import sys; sys.path.insert(0,'C:\Users\nithi\zerodhaBot')
from dotenv import load_dotenv; load_dotenv('C:\Users\nithi\zerodhaBot\.env')
from utils.notification import TelegramNotifier
TelegramNotifier().send('🚨 <b>ZerodhaBot auto-login FAILED</b>\nBot did not start. Check logs.')
"
    exit 1
}

# Step 3: Start WhatsApp commander in background
"[$(Get-Date)] Starting WhatsApp commander..." | Out-File -Append $LOG
$commander = Start-Process -FilePath $PYTHON `
    -ArgumentList "C:\Users\nithi\zerodhaBot\utils\whatsapp_commander.py" `
    -WorkingDirectory "C:\Users\nithi\zerodhaBot" `
    -PassThru -WindowStyle Hidden
"[$(Get-Date)] Commander PID: $($commander.Id)" | Out-File -Append $LOG

# Step 4: Run bot
"[$(Get-Date)] Starting main bot loop..." | Out-File -Append $LOG
& $PYTHON "C:\Users\nithi\zerodhaBot\main.py" --mode $BOT_MODE
"[$(Get-Date)] Bot session ended." | Out-File -Append $LOG

# Step 5: Stop commander when bot exits
"[$(Get-Date)] Stopping WhatsApp commander..." | Out-File -Append $LOG
Stop-Process -Id $commander.Id -Force -ErrorAction SilentlyContinue
