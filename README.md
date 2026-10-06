# 中創園區契約容量暨空調聯防（Smart-Energy-Dashboard）

| 檔案 | 用途 |
|---|---|
| `app.py` | Streamlit 戰情室畫面（只負責介面） |
| `forecast.py` | 需量預測引擎（app 與 auto_log 共用） |
| `weather.py` | 氣象抓取：Open-Meteo ECMWF + Visual Crossing 備援 |
| `calendar_tw.py` | 台灣上班日／假日判斷（含補班），自動取得當年與隔年行事曆 |
| `data/calendar_overrides.json` | 臨時變更（颱風假、公告調整）手動加在這裡，優先於線上資料 |
| `auto_log.py` | 每天 18:00（GitHub Actions）把真實氣象算出的預測寫入 Google Sheet |
| `get_weather.py` | 氣象署測站資料記錄 |
| `tests/` | `python -m unittest discover -s tests` |

## 行事曆
每天快取更新一次；線上資料不完整或連不上時，畫面頂端會顯示警告並暫以「僅週末休假」判斷，
此時請在 `data/calendar_overrides.json` 補上平日假日（`holidays`）或補班日（`workdays`）。

## Google Sheet 紀錄說明
`auto_log.py` 寫入的是**預測值**（氣象預報 + 預設參數：進駐率 100%、無場地租借、18:00 下班），不是台電實測需量。
新資料列的「資料版本」欄為 `v2-實算預測`；該欄為空的舊列是早期的示意假資料。
氣象全部斷線時不會寫入。

### 預測與實測比對（第二個工作表）
同一次執行另把**明日逐時預測**寫進「預測與實測比對」工作表，一個預測時段一列
（目前為 08:00–18:00 每兩小時），欄位：日期、時間、星期、台電時段、契約上限、預測需量、**實測需量**、誤差。
實測資料請貼到「實測需量」工作表（日期｜時間｜需量 kW，每小時一列，需量填該小時內 15 分鐘平均需量的最大值），
比對表的「實測需量」欄會自動查同日同時段的值，誤差（實測−預測，正值代表低估）與誤差 % 也會自動算出。
兩個工作表不存在時會自動建立；同一天重跑不會重複寫入。

## 機密
`VC_API_KEY`、`GOOGLE_CREDENTIALS` 放在 Streamlit secrets／GitHub Secrets。
