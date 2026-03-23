# Run this once as Administrator in PowerShell
# Creates a Windows Task Scheduler job to start ZerodhaBot every weekday at 8:55 AM IST

$BotDir = "C:\Users\nithi\zerodhaBot"
$PythonExe = (Get-Command python).Source
$ScriptPath = "$BotDir\deploy\run_bot_daily.ps1"

# Create the daily runner script
@"
Set-Location "$BotDir"
`$DATE = Get-Date -Format "yyyy-MM-dd"
`$LOG = "$BotDir\journaling\logs\bot_`$DATE.log"

"[`$(Get-Date)] Starting ZerodhaBot..." | Out-File -Append `$LOG

# Step 1: Auto-login
"[`$(Get-Date)] Running auto-login..." | Out-File -Append `$LOG
& "$PythonExe" "$BotDir\brokers\zerodha_auto_login.py" >> `$LOG 2>&1

if (`$LASTEXITCODE -ne 0) {
    "[`$(Get-Date)] Auto-login FAILED." | Out-File -Append `$LOG
    & "$PythonExe" -c "
import sys; sys.path.insert(0,'$BotDir')
from dotenv import load_dotenv; load_dotenv('$BotDir\.env')
from utils.notification import TelegramNotifier
TelegramNotifier().send('🚨 <b>ZerodhaBot auto-login FAILED</b>\nBot did not start. Check logs.')
"
    exit 1
}

# Step 2: Run bot
"[`$(Get-Date)] Starting main bot loop..." | Out-File -Append `$LOG
& "$PythonExe" "$BotDir\main.py" --mode paper >> `$LOG 2>&1
"[`$(Get-Date)] Bot session ended." | Out-File -Append `$LOG
"@ | Out-File -Encoding UTF8 $ScriptPath

# Register the scheduled task
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`""

# Mon-Fri at 08:55 AM
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "08:55AM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -RunOnlyIfNetworkAvailable `
    -WakeToRun

Register-ScheduledTask `
    -TaskName "ZerodhaBot" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -RunLevel Highest `
    -Force

Write-Host ""
Write-Host "Task Scheduler job created successfully!" -ForegroundColor Green
Write-Host "ZerodhaBot will auto-start Mon-Fri at 08:55 AM IST." -ForegroundColor Green
Write-Host ""
Write-Host "To verify: Task Scheduler -> Task Scheduler Library -> ZerodhaBot"
Write-Host "To run manually now: Start-ScheduledTask -TaskName 'ZerodhaBot'"
Write-Host "To remove: Unregister-ScheduledTask -TaskName 'ZerodhaBot'"
