# Weekly Stage B wrapper for SoubaNote_WeeklyUSStageB scheduled task.
# Runs the Alpaca-based price-trend prescreen for US stocks and pushes the result to GitHub,
# so the cloud Stage C routine (Woodstock fundamentals check) can pull it.

$ErrorActionPreference = "Stop"
$ProjectRoot = "c:\Users\user\Desktop\AI\AI作業場\相場note"
Set-Location $ProjectRoot

Write-Output "=== git pull ==="
git pull origin main

Write-Output "=== running alpaca_price_trend.py (Stage B) ==="
& "$ProjectRoot\.venv\Scripts\python.exe" "$ProjectRoot\src\fetchers\alpaca_price_trend.py"

Write-Output "=== git commit and push ==="
git add output/us_trend_candidates.json
$changes = git diff --cached --name-only
if ($changes) {
    git commit -m "Stage B: update US trend candidates ($(Get-Date -Format 'yyyy-MM-dd'))"
    git push origin main
    Write-Output "pushed update"
} else {
    Write-Output "no changes, skipping push"
}
