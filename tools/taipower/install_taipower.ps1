# 【監控電腦 BMS】台電 15 分鐘需量爬蟲安裝（請用「系統管理員」PowerShell 執行）
#   powershell -ExecutionPolicy Bypass -File C:\BMS_Export\taipower\install_taipower.ps1
# 做的事：裝套件 → 下載 Chromium → 存台電帳密到 Windows 認證管理員 → 併入舊資料並補缺漏 → 建立每 15 分鐘的工作排程
$ErrorActionPreference = 'Stop'
$Dir = 'C:\BMS_Export\taipower'
Set-Location $Dir
$py = (Get-Command python).Source
Write-Host "Python: $py"
& $py -m pip install -r requirements.txt
& $py -m playwright install chromium

Write-Host "`n=== 輸入台電 HVCS 帳號密碼（只存在本機 Windows 認證管理員，不寫進任何檔案）==="
$has = & $py -c "import keyring;print(bool(keyring.get_password('CTIC_Taipower','account')))"
if ($has -ne 'True') { & $py taipower_common.py --set-login } else { Write-Host '已有帳密，略過（要重設請執行 python taipower_common.py --set-login）' }
& $py -c "import onnxruntime"
if ($LASTEXITCODE -ne 0) { throw 'onnxruntime 載入失敗：請安裝 VC++ 執行階段 https://aka.ms/vs/17/release/vc_redist.x64.exe，或 pip install onnxruntime==1.16.3' }

$old = Join-Path (Get-ChildItem -Path 'G:\*\BMS*' -Directory | Select-Object -First 1).FullName '台電15分鐘\taipower_data.csv'
if (Test-Path $old) {
    Write-Host "`n=== 併入舊資料 $old，並補抓不完整的日子 ==="
    & $py taipower_backfill.py --import-old $old
} else {
    Write-Host "`n=== 找不到舊資料，從頭抓近 3 年（約 20–40 分鐘）==="
    & $py taipower_backfill.py
}

Write-Host "`n=== 試跑一次即時抓取 ==="
& $py taipower_live.py
if ($LASTEXITCODE -ne 0) { throw "試跑失敗，請看 $Dir\taipower.log" }

$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
$action  = New-ScheduledTaskAction -Execute $pyw -Argument "`"$Dir\taipower_live.py`"" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddMinutes(5)) -RepetitionInterval (New-TimeSpan -Minutes 15)
$set     = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 12) -MultipleInstances IgnoreNew -StartWhenAvailable
$user    = "$env:USERDOMAIN\$env:USERNAME"
$prin    = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName 'CTIC_Taipower_Live' -Action $action -Trigger $trigger -Settings $set -Principal $prin -Force | Out-Null
Write-Host "`n完成：工作排程 CTIC_Taipower_Live 每 15 分鐘（:05/:20/:35/:50）執行，紀錄在 $Dir\taipower.log"
