# 台電計費電表 15 分鐘需量（HVCS）爬蟲與儀表板

| 檔案 | 用途 |
|---|---|
| taipower_common.py | 登入（驗證碼 OCR）、抓某一天、解析、存檔。帳密存在 Windows 認證管理員（`--set-login`），不寫在程式裡 |
| taipower_live.py | 工作排程每 15 分鐘：抓今天＋昨天，補最近 7 天不完整日；最近 35 天複製到雲端「BMS自動匯出\台電15分鐘」 |
| taipower_backfill.py | 補近 N 年不滿 96 筆的日子；`--import-old` 併入舊版 taipower_data.csv |
| dashboard.py | Streamlit 儀表板：今日（分時段契約線＋90% 告警、昨天／上週／去年同星期疊圖）、歷史、每月各時段最高與超約、熱圖、年度、資料品質 |
| install_taipower.ps1 | 監控電腦一次安裝 |
| backtest.py | 用台電資料回測需量預測模型 |

資料欄位與舊版相同：時間（15 分鐘區間結束）,時段,需量_kW,契約容量_kW,使用率_%。

其他電腦看儀表板（不登入台電，只讀雲端副本）：
```
set TAIPOWER_DIR=G:\我的雲端硬碟\BMS自動匯出\台電15分鐘
streamlit run dashboard.py
```
