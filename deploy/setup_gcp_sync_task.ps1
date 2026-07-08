# ============================================================
# setup_gcp_sync_task.ps1
# Creates a Windows Task Scheduler task that syncs GCP logs
# to local every 5 minutes on weekdays 9:00 AM – 3:45 PM IST.
#
# Run ONCE as Administrator:
#   Right-click PowerShell -> Run as Administrator
#   cd C:\Users\nithi\zerodhaBot
#   .\deploy\setup_gcp_sync_task.ps1
# ============================================================

$TaskName   = "ZerodhaBot_GCPSync"
$ScriptPath = "C:\Users\nithi\zerodhaBot\deploy\sync_gcp_logs.ps1"
$LogPath    = "C:\Users\nithi\zerodhaBot\journaling\logs\gcp_sync_task.log"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Trigger: weekdays, start at 9:00 AM, repeat every 5 min for 6h45m (until ~3:45 PM)
$Trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "9:00AM"

$Trigger.RepetitionInterval = "PT5M"        # every 5 minutes
$Trigger.RepetitionDuration = "PT6H45M"     # for 6 hours 45 minutes (9:00 -> 15:45)

# Action: run PowerShell with the sync script
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`" >> `"$LogPath`" 2>&1"

# Run as current user
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit "PT2M" `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Syncs ZerodhaBot GCP logs to local every 5 min during market hours so Streamlit dashboard shows live GCP data." `
    -Force

Write-Host ""
Write-Host "Task '$TaskName' registered successfully."
Write-Host "Runs: Mon-Fri, 9:00 AM - 3:45 PM IST, every 5 minutes."
Write-Host "Sync log: $LogPath"
Write-Host ""
Write-Host "To run immediately (test):"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "To remove:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
