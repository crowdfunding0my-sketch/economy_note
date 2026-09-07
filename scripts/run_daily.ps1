# Daily wrapper for SoubaNote_DailyMain scheduled task.
# Pulls the latest candidates (updated by the cloud Stage C routine) before running main.py.
#
# 2026-09-08追加: 実行結果をログファイルに残し、main.pyの終了コードをこのスクリプト自身の
# 終了コードとして返すようにした。これまではTask Schedulerの実行結果を確認しても
# 標準出力がどこにも残らず、かつpython.exeが異常終了してもPowerShell自体は
# エラーにならず「成功(0)」と表示されてしまうため、2026-09-08朝の実行がFRB発表取得の
# 後で失敗して記事が生成されなかったにもかかわらず、Task Schedulerには「Last Result: 0」
# と表示され、原因調査ができなかった（手動再実行で判明・復旧）。

$ErrorActionPreference = "Stop"
$ProjectRoot = "c:\Users\user\Desktop\AI\AI作業場\相場note"
Set-Location $ProjectRoot

$LogPath = Join-Path $ProjectRoot "output\daily_main_run.log"
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "output") | Out-Null

$exitCode = 0
try {
    Write-Output "=== git pull ===" | Tee-Object -FilePath $LogPath
    git pull origin main *>&1 | Tee-Object -FilePath $LogPath -Append
    if (-not $?) {
        Write-Warning "git pull failed, continuing with local cache"
    }

    Write-Output "=== running main.py ===" | Tee-Object -FilePath $LogPath -Append
    & "$ProjectRoot\.venv\Scripts\python.exe" "$ProjectRoot\src\main.py" *>&1 | Tee-Object -FilePath $LogPath -Append
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-Output "=== main.py がエラー終了しました (exit code $exitCode) ===" | Tee-Object -FilePath $LogPath -Append
    }
}
catch {
    Write-Output "=== 例外が発生しました: $_ ===" | Tee-Object -FilePath $LogPath -Append
    $exitCode = 1
}

exit $exitCode

