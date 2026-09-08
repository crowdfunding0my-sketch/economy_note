# Daily wrapper for SoubaNote_DailyMain scheduled task.
# Pulls the latest candidates (updated by the cloud Stage C routine) before running main.py.
#
# 2026-09-08追加: 実行結果をログファイルに残し、main.pyの終了コードをこのスクリプト自身の
# 終了コードとして返すようにした。これまではTask Schedulerの実行結果を確認しても
# 標準出力がどこにも残らず、かつpython.exeが異常終了してもPowerShell自体は
# エラーにならず「成功(0)」と表示されてしまうため、2026-09-08朝の実行がFRB発表取得の
# 後で失敗して記事が生成されなかったにもかかわらず、Task Schedulerには「Last Result: 0」
# と表示され、原因調査ができなかった（手動再実行で判明・復旧）。
#
# 2026-09-09修正: 上記対応で$ErrorActionPreference="Stop"にした状態で`git pull`の出力を
# `*>&1`（stderrをstdoutにマージ）してパイプしたところ、gitが正常時にstderrへ書く
# 定型メッセージ（"From https://github.com/..."等）まで終端エラー(NativeCommandError)
# として扱われてしまい、git pull成功時でも即座に例外でスクリプト全体が落ちる不具合が
# 発生した（2026-09-09朝の自動実行が本文生成まで到達しなかった原因）。
# ネイティブexeのstderrを`2>&1`/`*>&1`でマージしつつ$ErrorActionPreference="Stop"を
# 使うのは危険なため、$ErrorActionPreferenceは既定の"Continue"のままにし、
# 失敗検知は$LASTEXITCODEの明示チェックのみで行うようにした。

$ProjectRoot = "c:\Users\user\Desktop\AI\AI作業場\相場note"
Set-Location $ProjectRoot

$LogPath = Join-Path $ProjectRoot "output\daily_main_run.log"
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "output") | Out-Null

$exitCode = 0

Write-Output "=== git pull ===" | Tee-Object -FilePath $LogPath
# gitは正常時もstderrに定型メッセージ("From https://..."等)を書くため、2>&1でマージすると
# NativeCommandErrorとして余計に表示されノイズになる。git pullの成否は$LASTEXITCODEだけで
# 判定できるので、ここではstderrをマージしない（pythonの方は例外調査に使うので引き続きマージする）。
git pull origin main | Tee-Object -FilePath $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Output "=== git pull failed (exit code $LASTEXITCODE), continuing with local cache ===" | Tee-Object -FilePath $LogPath -Append
}

Write-Output "=== running main.py ===" | Tee-Object -FilePath $LogPath -Append
& "$ProjectRoot\.venv\Scripts\python.exe" "$ProjectRoot\src\main.py" 2>&1 | Tee-Object -FilePath $LogPath -Append
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Output "=== main.py がエラー終了しました (exit code $exitCode) ===" | Tee-Object -FilePath $LogPath -Append
}

exit $exitCode


