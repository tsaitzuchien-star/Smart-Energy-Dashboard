"""每日 18:00（GitHub Actions）把「真實氣象 + 同一套預測引擎」的結果寫進 Google Sheet。

與 app.py 共用 weather.py / calendar_tw.py / forecast.py，數字與戰情室畫面一致。
注意：寫入的是「預測值」（依氣象預報與預設參數推算），不是台電實測需量。
預設參數：進駐率 100%、無場地租借、18:00 準時下班、磁浮不補償、API 輻射推太陽能。
氣象全部斷線時不寫入（寧可缺一天，也不寫假資料），並以非 0 狀態結束讓 Actions 顯示失敗。
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta

from calendar_tw import load_calendar
from forecast import ForecastInputs, compute_forecast, off_days_before
from weather import TW_TZ, fetch_smart_weather

log = logging.getLogger("auto_log")

DATA_VERSION = "v2-實算預測"          # 物理模型
DATA_VERSION_CAL = "v3-實測校正"      # 實測校正模型（model/demand_model.json）


def data_version(fc):
    return DATA_VERSION_CAL if fc.get("model_used") else DATA_VERSION
HEADERS = [
    "紀錄時間", "假設進駐率(%)", "今日最高氣溫(°C)", "今日最高輻射(W/m²)",
    "今日最危險時段", "今日最高需量(kW)", "明日預估高溫(°C)",
    "明日太陽能峰值(kW)", "明日最危險時段", "明日預估最高需量(kW)", "建議今晚儲冰(小時)",
    "氣象來源", "明日是否假日", "季別", "資料版本",
]
SHEET_NAME = "中創園區空調戰情大數據"

# 第二個工作表：明日逐時預測，一時段一列；「實測需量」欄自動從「實測需量」工作表查同日同時段的值，再算誤差
COMPARE_TAB = "預測與實測比對"
COMPARE_HEADERS = [
    "日期", "時間", "星期", "台電時段", "契約上限(kW)", "預測需量(kW)", "實測需量(kW)",
    "誤差(實測−預測 kW)", "誤差(%)", "預測產生時間", "資料版本",
]
WEEKDAYS = "一二三四五六日"
# 第三個工作表：監控廠商的實測需量（人工貼上或自動匯入），每小時一列
ACTUAL_TAB = "實測需量"
ACTUAL_HEADERS = ["日期", "時間", "需量(kW)"]
_A, _B = 'INDIRECT("A"&ROW())', 'INDIRECT("B"&ROW())'
_MATCH = f"'{ACTUAL_TAB}'!A:A,{_A},'{ACTUAL_TAB}'!B:B,{_B}"
# 停機/保養時段在實測表留空白列；只算有數值的列，否則 MAXIFS 會回 0
ACTUAL_LOOKUP = f'=IF(COUNTIFS({_MATCH},\'{ACTUAL_TAB}\'!C:C,"<>")=0,"",MAXIFS(\'{ACTUAL_TAB}\'!C:C,{_MATCH}))'
_G, _F = 'INDIRECT("G"&ROW())', 'INDIRECT("F"&ROW())'
ERR_KW = f'=IF({_G}="","",{_G}-{_F})'
ERR_PCT = f'=IF(OR({_G}="",{_G}=0),"",ROUND(({_G}-{_F})/{_G}*100,1))'


def build_row(now, w, cal, inp=None):
    """由真實氣象與行事曆算出一列資料；氣象斷線回傳 None。"""
    if w["status_code"] == 0:
        return None
    inp = inp or ForecastInputs()
    tmr = now + timedelta(days=1)
    tmr_hol = cal.is_holiday(tmr.date())
    fc = compute_forecast(w, inp, now, cal.is_holiday(now.date()), tmr_hol,
                          prev_off_days=off_days_before(tmr.date(), cal.is_holiday))

    temps_today = list(w["all_temps_today"].values())
    today_max_temp = max(temps_today) if temps_today else w["temp"]
    rads = [h["rad"] for h in w["today_hourly"].values()]
    today_max_rad = max(rads) if rads else w["rad"]
    solar_peak = max((d["h_solar"] for d in fc["calc_tmr"].values()), default=fc["est_solar"])

    return [
        w["fetch_time"], inp.occupancy_rate, round(today_max_temp, 1), round(today_max_rad, 1),
        fc["today_worst_hour"], round(fc["today_max_net"], 1), round(w["tmr_temp"], 1),
        round(solar_peak, 1), fc["worst_hour"], round(fc["max_net_grid_demand"], 1),
        round(fc["suggested_ice_hrs"], 1),
        w["source"], "是" if tmr_hol else "否", fc["season_tag"], data_version(fc),
    ]


def build_compare_rows(now, w, cal, inp=None):
    """明日逐時預測，每個預測時段一列；氣象斷線回傳 []。"""
    if w["status_code"] == 0:
        return []
    inp = inp or ForecastInputs()
    tmr = now + timedelta(days=1)
    fc = compute_forecast(w, inp, now, cal.is_holiday(now.date()), cal.is_holiday(tmr.date()),
                          prev_off_days=off_days_before(tmr.date(), cal.is_holiday))
    day = tmr.strftime("%Y-%m-%d")
    return [
        [day, h, WEEKDAYS[tmr.weekday()], d["period"], d["current_limit"], round(d["h_net"], 1), ACTUAL_LOOKUP,
         ERR_KW, ERR_PCT, w["fetch_time"], data_version(fc)]
        for h, d in fc["calc_tmr"].items()
    ]


def _ensure_tab(book, title, headers):
    import gspread
    try:
        ws = book.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=title, rows=1000, cols=len(headers))
    if ws.row_values(1) != headers:
        ws.update(range_name="A1", values=[headers])
    return ws


def write_compare_rows(book, rows):
    """寫入比對工作表；同一天已寫過就略過（Actions 重跑不重複）。也確保「實測需量」工作表存在。"""
    _ensure_tab(book, ACTUAL_TAB, ACTUAL_HEADERS)
    ws = _ensure_tab(book, COMPARE_TAB, COMPARE_HEADERS)
    if rows[0][0] in ws.col_values(1):
        log.info("ℹ️ %s 的逐時預測已存在，略過", rows[0][0])
        return
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    log.info("✅ 成功寫入 %d 筆逐時預測到「%s」", len(rows), COMPARE_TAB)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    creds_json_str = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json_str:
        log.error("❌ 找不到 GOOGLE_CREDENTIALS 環境變數")
        return 1

    now = datetime.now(TW_TZ)
    w = fetch_smart_weather(vc_key=os.environ.get("VC_API_KEY") or None, now=now)
    cal = load_calendar({now.year, (now + timedelta(days=1)).year})
    for msg in cal.warnings:
        log.warning(msg)

    row = build_row(now, w, cal)
    if row is None:
        log.error("❌ 氣象來源全部斷線，本次不寫入（不產生假資料）")
        return 1

    import gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    client = gspread.authorize(Credentials.from_service_account_info(json.loads(creds_json_str), scopes=scopes))
    book = client.open(SHEET_NAME)
    sheet = book.sheet1

    if sheet.row_values(1) != HEADERS:
        # 舊表頭是 11 欄（且舊資料為假資料）；更新第 1 列，舊資料列的「資料版本」欄為空即可辨識
        sheet.update(range_name="A1", values=[HEADERS])
    sheet.append_row(row, value_input_option="USER_ENTERED")
    log.info("✅ 成功寫入：%s", dict(zip(HEADERS, row)))

    compare_rows = build_compare_rows(now, w, cal)
    if compare_rows:
        write_compare_rows(book, compare_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
