# Weekly Strategy Evaluator Runner
# Invoked every Friday at 1:00 PM IST by Windows Task Scheduler.
# Uses the claude CLI in headless mode so the weekly-strategy-evaluator agent
# has full file-system tool access to read logs and produce the report.

$BotDir  = "C:\Users\nithi\zerodhaBot"
Set-Location $BotDir

$DATE    = Get-Date -Format "yyyy-MM-dd"
$LOG     = "$BotDir\journaling\logs\weekly_evaluator_$DATE.log"

"[$(Get-Date -Format 'HH:mm:ss')] Weekly strategy evaluation starting..." |
    Out-File -Encoding UTF8 $LOG

$ClaudeExe = "C:\Users\nithi\.local\bin\claude.exe"
$Prompt    = "It is Friday 1:00 PM IST. Run the weekly-strategy-evaluator agent now to analyze this week's trading performance across all active strategies and produce the weekly evaluation report."

& $ClaudeExe --dangerously-skip-permissions -p $Prompt >> $LOG 2>&1

if ($LASTEXITCODE -eq 0) {
    "[$(Get-Date -Format 'HH:mm:ss')] Weekly evaluation completed successfully." |
        Out-File -Append -Encoding UTF8 $LOG
} else {
    "[$(Get-Date -Format 'HH:mm:ss')] Weekly evaluation FAILED (exit code $LASTEXITCODE)." |
        Out-File -Append -Encoding UTF8 $LOG
}
