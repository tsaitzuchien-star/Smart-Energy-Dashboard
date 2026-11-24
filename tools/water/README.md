# 台水智慧水表（水表e管家）爬蟲與用水戰情室

| 檔案 | 用途 |
|---|---|
| water_common.py | 登入（驗證碼 OCR）、抓首頁狀態與「用水指針查詢」、解析、存檔。帳密存在 Windows 認證管理員（`--set-login`，服務名稱 CTIC_Water），不寫在程式裡 |
| water_analysis.py | 判讀規則（監控電腦告警與雲端 water.json 共用）：日用量、夜間 00–06、7 天合計基線、假日基線、單日暴增、資料中斷、台水異常通報 |
| water_live.py | 工作排程 CTIC_Water_Live 每天 08:10、14:10：抓首頁＋最近 14 天指針 → 判讀 → 新告警推 LINE＋GitHub ems-feed → 複製到雲端「BMS自動匯出\自來水」 |
| water_backfill.py | 補抓歷史（預設 2026-01-01 起，每月查一次）；`--import-old` 併入 Cliff 原版 water_data.csv |
| install_water.ps1 | 監控電腦一次安裝（從 GitHub 下載程式、裝套件、存帳密、補抓、建排程） |

雲端 `build_water.py`（ems_live 工作流程每 15 分鐘）讀雲端檔案 → `ems-live/water.json` → 網站 `ctic-ems-dashboard/water.html`。

## 判讀重點
- 日用量＝隔天 00 時指針 − 當天 00 時指針。台水頁面的「估算量」只算到 15 時，會少算約 45%（10/25–11/23：356 vs 642 度）。
- 園區是受水池／水塔補水，指針一次跳約 21–22 度；夜間一筆大跳升多半是半夜補水，不是漏水。
- 水塔上游漏水看夜間指針是否「每一段都在走」連續 3 晚；水塔下游漏水看 7 天合計與假日用量是否高於基線。

## 存檔（C:\BMS_Export\water）
`water_readings.csv`（date,hour,reading）、`water_estimates.csv`（date,est）、`water_status.json`、`water.log`、`raw\`（原始頁面文字，核對解析用）、`water_alert_state.json`（已推過的告警）。
