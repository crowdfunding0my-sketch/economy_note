# Weekly JP stock screening wrapper for SoubaNote_WeeklyJPScan scheduled task.
# The scan takes ~14.5 hours (J-Quants Free plan rate limit), and the previous run was
# aborted mid-way (Win32 error 1067 / 0x8007042B "process terminated unexpectedly"),
# most likely because the PC went to sleep during execution. This wrapper prevents
# sleep for the duration of the scan using SetThreadExecutionState, then restores
# normal power behavior afterward.

$ErrorActionPreference = "Stop"
$ProjectRoot = "c:\Users\user\Desktop\AI\AI作業場\相場note"
Set-Location $ProjectRoot

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class SleepBlocker {
    [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@

# 16進リテラル0x80000000はPowerShell 5.1でInt32として負値にパースされ[uint32]変換に失敗することがあるため、10進数で指定する
$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1
$ES_DISPLAY_REQUIRED = [uint32]2

Write-Output "=== sleep prevention: ON ==="
[SleepBlocker]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED -bor $ES_DISPLAY_REQUIRED) | Out-Null

try {
    Write-Output "=== git pull ==="
    git pull origin main

    Write-Output "=== running jquants_screener.py (this takes ~14.5 hours) ==="
    & "$ProjectRoot\.venv\Scripts\python.exe" "$ProjectRoot\src\fetchers\jquants_screener.py"
}
finally {
    Write-Output "=== sleep prevention: OFF ==="
    [SleepBlocker]::SetThreadExecutionState($ES_CONTINUOUS) | Out-Null
}

