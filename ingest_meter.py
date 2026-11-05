"""把監控電腦每小時匯出的總表電表資料（Google 雲端「BMS自動匯出」資料夾）寫進試算表。

來源：監控電腦排程 bms_export.ps1 從 SQL Server DataLogger.dbo.Table03 匯出，每天一個
Table03_YYYY-MM-DD.csv，約每 15 分鐘一筆累積度數；KmTagx020 是總表（MOF）。
兩筆度數相減 ×4 ＝ 該 15 分鐘平均需量 (kW)，與台電計費方式相同。

這顆電表固定比 BMS 畫面（F0_B2P2_MCB_KW_PV）高約 10 kW（2026-06～10 共 13,413 筆比對，
斜率 1.000、差值標準差 2.7 kW）。試算表「實測需量」的歷史資料與預測模型都是以 BMS 為準，
所以預設扣掉 METER_OFFSET_KW，讓新舊資料同一基準；要改以電表為準時把它設成 0 並重建歷史。

只補試算表沒有的時段，不覆蓋既有資料。
"""
import csv
import io
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

from forecast import _PERIOD_LIMIT, tou_period

SHEET_ID = "1NZ0OPky-I-oWXfFTeVR8qpFT1pFBwQvRoSerJYJJMwY"
METER_COL = "KmTagx020"
METER_OFFSET_KW = float(os.environ.get("METER_OFFSET_KW", "10.0"))
DAYS_BACK = int(os.environ.get("INGEST_DAYS_BACK", "5"))
WK = "一二三四五六日"


def period_of(d, hour, is_holiday):
    """台電時段；平日國定假日當作全日離峰（與既有每日實測摘要一致）。"""
    if is_holiday(d) and d.weekday() < 5:
        return "離峰"
    return tou_period(d, hour)


def parse_readings(text):
    """CSV 文字 → [(datetime, kWh)]，略過空值與 0（通訊中斷時會記 0）。"""
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        v = (r.get(METER_COL) or "").strip()
        t = (r.get("DateTime") or "").strip()
        if not v or not t:
            continue
        try:
            kwh = float(v)
            ts = datetime.strptime(t[:19], "%Y/%m/%d %H:%M:%S")
        except ValueError:
            continue
        if kwh > 0:
            out.append((ts, kwh))
    return sorted(set(out))


def intervals(readings, offset=METER_OFFSET_KW):
    """相鄰讀值 → {區間起始(整 15 分): kW}。只收 14–16 分鐘間隔、40–700 kW 的合理值。"""
    out = {}
    for (t0, e0), (t1, e1) in zip(readings, readings[1:]):
        mins = (t1 - t0).total_seconds() / 60
        if not 14 <= mins <= 16:
            continue
        kw = (e1 - e0) / mins * 60
        if not 40 <= kw <= 700:
            continue
        start = t0.replace(second=0, microsecond=0)
        start -= timedelta(minutes=start.minute % 15)
        if t0.minute % 15 >= 8:          # 例如 00:14:59 視為 00:15 開始
            start += timedelta(minutes=15)
        out[start] = round(kw - offset, 1)
    return out


def hourly_max(iv, last_reading):
    """每小時最大 15 分鐘需量；只輸出整小時已結束的時段。"""
    hours = defaultdict(list)
    for s, kw in iv.items():
        hours[s.replace(minute=0)].append(kw)
    return {h: max(v) for h, v in hours.items()
            if h + timedelta(hours=1) <= last_reading + timedelta(minutes=1)}


def daily_rows(iv, is_holiday, today):
    """已結束的日子 → 每日實測摘要列（欄位與既有工作表相同）。"""
    days = defaultdict(list)
    for s, kw in iv.items():
        days[s.date()].append((s, kw))
    rows = {}
    for d, xs in days.items():
        if d >= today:
            continue
        b, a = max(xs, key=lambda x: x[1])
        p = period_of(d, b.hour, is_holiday)
        mg = min(xs, key=lambda x: _PERIOD_LIMIT[period_of(d, x[0].hour, is_holiday)] - x[1])
        pm = period_of(d, mg[0].hour, is_holiday)
        rows[d] = [d.isoformat(), WK[d.weekday()], a, b.strftime("%H:%M"), p, int(_PERIOD_LIMIT[p]),
                   round(_PERIOD_LIMIT[pm] - mg[1], 1), f"{mg[0].strftime('%H:%M')} {pm}",
                   round(100 * len(xs) / 96)]
    return rows


# ---------------- Google 存取 ----------------
def _creds():
    from google.oauth2.service_account import Credentials
    info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    return Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly"])


def fetch_recent_files(creds, days_back=DAYS_BACK):
    from google.auth.transport.requests import AuthorizedSession
    s = AuthorizedSession(creds)
    since = "Table03_" + (date.today() - timedelta(days=days_back)).isoformat()
    r = s.get("https://www.googleapis.com/drive/v3/files",
              params={"q": "name contains 'Table03_' and trashed = false",
                      "fields": "files(id,name,modifiedTime)", "pageSize": 100, "orderBy": "name desc",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    r.raise_for_status()
    files = {}
    for f in r.json().get("files", []):
        if f["name"].endswith(".csv") and f["name"] >= since:
            files.setdefault(f["name"], f)    # 同名只取一個
    texts = []
    for name in sorted(files):
        d = s.get(f"https://www.googleapis.com/drive/v3/files/{files[name]['id']}", params={"alt": "media"})
        d.raise_for_status()
        texts.append((name, d.content.decode("utf-8-sig", errors="replace")))
    return texts


def main():
    import gspread
    from calendar_tw import load_calendar

    creds = _creds()
    texts = fetch_recent_files(creds)
    if not texts:
        print("找不到 Table03_*.csv（資料夾是否已共用給服務帳號？）")
        return
    readings = sorted({x for _, t in texts for x in parse_readings(t)})
    print(f"讀到 {len(texts)} 個檔案、{len(readings)} 筆讀值：{readings[0][0]} ～ {readings[-1][0]}")
    iv = intervals(readings)
    now = datetime.utcnow() + timedelta(hours=8)
    cal = load_calendar({now.year - 1, now.year})

    book = gspread.authorize(creds).open_by_key(SHEET_ID)

    ws = book.worksheet("實測需量")
    have = {(r[0], r[1]) for r in ws.get_values("A2:B") if len(r) >= 2}
    new = [[h.date().isoformat(), h.strftime("%H:%M"), kw]
           for h, kw in sorted(hourly_max(iv, readings[-1][0]).items())
           if (h.date().isoformat(), h.strftime("%H:%M")) not in have]
    if new:
        ws.append_rows(new, value_input_option="RAW")
    print(f"實測需量：新增 {len(new)} 小時" + (f"（{new[0][0]} {new[0][1]} ～ {new[-1][0]} {new[-1][1]}）" if new else ""))

    wd = book.worksheet("每日實測摘要")
    have_d = {r[0] for r in wd.get_values("A2:A") if r}
    newd = [row for d, row in sorted(daily_rows(iv, cal.is_holiday, now.date()).items())
            if d.isoformat() not in have_d and row[-1] >= 90]
    if newd:
        wd.append_rows(newd, value_input_option="RAW")
    print(f"每日實測摘要：新增 {len(newd)} 天" + (f"（{newd[0][0]} ～ {newd[-1][0]}）" if newd else ""))


if __name__ == "__main__":
    main()
