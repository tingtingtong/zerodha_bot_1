# Run this ONCE as Administrator in PowerShell.
# Registers a Windows Task Scheduler job that runs the weekly strategy
# evaluator every Friday at 1:00 PM (IST local time).
#
# Usage:
#   Right-click PowerShell -> Run as Administrator
#   .\deploy\setup_weekly_evaluator_task.ps1
#
# To verify:  Task Scheduler -> Task Scheduler Library -> ZerodhaBot_WeeklyEvaluator
# To run now: Start-ScheduledTask -TaskName "ZerodhaBot_WeeklyEvaluator"
# To remove:  Unregister-ScheduledTask -TaskName "ZerodhaBot_WeeklyEvaluator" -Confirm:$false

$BotDir       = "C:\Users\nithi\zerodhaBot"
$RunnerScript = "$BotDir\deploy\run_weekly_evaluator.ps1"

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunnerScript`"" `
    -WorkingDirectory $BotDir

$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Friday `
    -At "1:00PM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable   # fires as soon as possible if PC was off at 1 PM

Register-ScheduledTask `
    -TaskName    "ZerodhaBot_WeeklyEvaluator" `
    -Description "Runs the ZerodhaBot weekly strategy evaluator agent every Friday at 1:00 PM IST." `
    -Action      $Action `
    -Trigger     $Trigger `
    -Settings    $Settings `
    -RunLevel    Highest `
    -Force

Write-Host ""
Write-Host "Task registered: ZerodhaBot_WeeklyEvaluator" -ForegroundColor Green
Write-Host "Fires every Friday at 1:00 PM IST." -ForegroundColor Green
Write-Host "Logs -> journaling/logs/weekly_evaluator_YYYY-MM-DD.log" -ForegroundColor Cyan
Write-Host ""
Write-Host "To test right now:" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName 'ZerodhaBot_WeeklyEvaluator'" -ForegroundColor Yellow
