Set-Location "C:\Users\nithi\zerodhaBot"
$DATE = Get-Date -Format "yyyy-MM-dd"
$PYTHON = "C:\Users\nithi\AppData\Local\Programs\Python\Python312\python.exe"
# Use a separate scheduler log to avoid file lock conflict with main.py's own log handler
$LOG = "C:\Users\nithi\zerodhaBot\journaling\logs\scheduler_$DATE.log"

"[$(Get-Date)] Starting ZerodhaBot..." | Out-File -Append $LOG

# Step 1: Auto-login
"[$(Get-Date)] Running auto-login..." | Out-File -Append $LOG
& $PYTHON "C:\Users\nithi\zerodhaBot\brokers\zerodha_auto_login.py" >> $LOG 2>&1

if ($LASTEXITCODE -ne 0) {
    "[$(Get-Date)] Auto-login FAILED." | Out-File -Append $LOG
    & $PYTHON -c "
import sys; sys.path.insert(0,'C:\Users\nithi\zerodhaBot')
from dotenv import load_dotenv; load_dotenv('C:\Users\nithi\zerodhaBot\.env')
from utils.notification import TelegramNotifier
TelegramNotifier().send('🚨 <b>ZerodhaBot auto-login FAILED</b>\nBot did not start. Check logs.')
"
    exit 1
}

# Step 2: Start WhatsApp commander in background
"[$(Get-Date)] Starting WhatsApp commander..." | Out-File -Append $LOG
$commander = Start-Process -FilePath $PYTHON `
    -ArgumentList "C:\Users\nithi\zerodhaBot\utils\whatsapp_commander.py" `
    -WorkingDirectory "C:\Users\nithi\zerodhaBot" `
    -PassThru -WindowStyle Hidden
"[$(Get-Date)] Commander PID: $($commander.Id)" | Out-File -Append $LOG

# Step 3: Run bot (main.py writes its own bot_YYYY-MM-DD.log internally)
"[$(Get-Date)] Starting main bot loop..." | Out-File -Append $LOG
& $PYTHON "C:\Users\nithi\zerodhaBot\main.py" --mode paper
"[$(Get-Date)] Bot session ended." | Out-File -Append $LOG

# Step 4: Stop commander when bot exits
"[$(Get-Date)] Stopping Telegram commander..." | Out-File -Append $LOG
Stop-Process -Id $commander.Id -Force -ErrorAction SilentlyContinue
