# 【監控電腦 BMS】台水智慧水表爬蟲安裝（請用「系統管理員」PowerShell 執行）
#   powershell -ExecutionPolicy Bypass -File C:\BMS_Export\water\install_water.ps1 -Sha <commit>
# 做的事：從 GitHub 下載程式 → 裝套件 → 存台水帳密到 Windows 認證管理員 → 補抓 2026-01-01 起的指針 → 試跑 → 建立每天 08:10、14:10 的工作排程
param([string]$Sha = 'main')
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Dir = 'C:\BMS_Export\water'
New-Item -ItemType Directory -Force -Path $Dir, "$Dir\data" | Out-Null
Set-Location $Dir
$base = "https://raw.githubusercontent.com/tsaitzuchien-star/Smart-Energy-Dashboard/$Sha"
$files = @{
  'tools/water/water_common.py'   = 'water_common.py'
  'tools/water/water_analysis.py' = 'water_analysis.py'
  'tools/water/water_live.py'     = 'water_live.py'
  'tools/water/water_backfill.py' = 'water_backfill.py'
  'tools/water/requirements.txt'  = 'requirements.txt'
  'calendar_tw.py'                = 'calendar_tw.py'
  'data/calendar_overrides.json'  = 'data\calendar_overrides.json'
}
foreach ($k in $files.Keys) {
  Invoke-WebRequest -UseBasicParsing -Uri "$base/$k" -OutFile (Join-Path $Dir $files[$k])
  Write-Host "下載 $k"
}
$py = (Get-Command python).Source
Write-Host "Python: $py"
& $py -m pip install -r requirements.txt
& $py -m playwright install chromium

Write-Host "`n=== 輸入台水水表e管家帳號密碼（只存在本機 Windows 認證管理員，不寫進任何檔案）==="
$has = & $py -c "import keyring;print(bool(keyring.get_password('CTIC_Water','account')))"
if ($has -ne 'True') { & $py water_common.py --set-login } else { Write-Host '已有帳密，略過（要重設請執行 python water_common.py --set-login）' }

Write-Host "`n=== 補抓 2026-01-01 起的智慧水表指針（每月查一次，約 3–8 分鐘）==="
& $py water_backfill.py --start 2026-01-01
if ($LASTEXITCODE -ne 0) { throw "補抓失敗，請看 $Dir\water.log" }

Write-Host "`n=== 試跑一次每日抓取（不推 LINE）==="
& $py water_live.py --no-notify
if ($LASTEXITCODE -ne 0) { throw "試跑失敗，請看 $Dir\water.log" }

$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
$action  = New-ScheduledTaskAction -Execute $pyw -Argument "`"$Dir\water_live.py`"" -WorkingDirectory $Dir
$t1 = New-ScheduledTaskTrigger -Daily -At '08:10'
$t2 = New-ScheduledTaskTrigger -Daily -At '14:10'
$set  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew -StartWhenAvailable
$user = "$env:USERDOMAIN\$env:USERNAME"
$prin = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName 'CTIC_Water_Live' -Action $action -Trigger @($t1, $t2) -Settings $set -Principal $prin -Force | Out-Null
Write-Host "`n完成：工作排程 CTIC_Water_Live 每天 08:10、14:10 執行，紀錄在 $Dir\water.log，資料同步到雲端 BMS自動匯出\自來水"
