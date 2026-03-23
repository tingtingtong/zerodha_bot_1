# ZerodhaBot - Backtest Runner
# Usage: .\run_backtest.ps1 NIFTYBEES etf_momentum 2026-01-20 2026-03-14 20000

param(
    [string]$Symbol   = "NIFTYBEES",
    [string]$Strategy = "etf_momentum",
    [string]$Start    = "2026-01-20",
    [string]$End      = "2026-03-14",
    [float] $Capital  = 20000
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  ZerodhaBot Backtest" -ForegroundColor Cyan
Write-Host "  $Symbol | $Strategy | $Start to $End | Rs.$Capital" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

python backtest_runner.py --symbol $Symbol --strategy $Strategy --start $Start --end $End --capital $Capital
