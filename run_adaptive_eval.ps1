# Run the adaptive strategy evaluator
# Schedule this weekly (Sunday evening) via Task Scheduler
# Or run manually: .\run_adaptive_eval.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Running Adaptive Strategy Evaluator..." -ForegroundColor Cyan
python -m adaptive.strategy_evaluator --weeks 4

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nEvaluation complete. Check adaptive/adaptive_state.json for results." -ForegroundColor Green
} else {
    Write-Host "`nEvaluation failed!" -ForegroundColor Red
}
