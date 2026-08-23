# Daily wrapper for SoubaNote_DailyMain scheduled task.
# Pulls the latest candidates (updated by the cloud Stage C routine) before running main.py.

$ErrorActionPreference = "Stop"
$ProjectRoot = "c:\Users\user\Desktop\AI\AI作業場\相場note"
Set-Location $ProjectRoot

Write-Output "=== git pull ==="
git pull origin main
if (-not $?) {
    Write-Warning "git pull failed, continuing with local cache"
}

Write-Output "=== running main.py ==="
& "$ProjectRoot\.venv\Scripts\python.exe" "$ProjectRoot\src\main.py"
