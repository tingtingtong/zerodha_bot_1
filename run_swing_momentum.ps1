# Daily run of the paper momentum sleeve. Marks to market every trading day and only
# trades on the last trading day of the month (or the first-ever run). Run AFTER 15:35 IST.
Set-Location $PSScriptRoot
python -m swing.momentum_rebalancer *>> journaling\swing_run.log
