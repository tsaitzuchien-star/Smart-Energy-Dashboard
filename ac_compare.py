"""空調「人工操作 vs 自動控制」每日比對，寫進試算表分頁「空調人工vs自動」。

Cliff 2026-11-21：要長期觀察人工操控與自動控制的效益；選擇「只做同溫度比對」（不安排人工對照週）。

做法（每個平日一列）：
- 當天用電：監控電腦每 15 分鐘匯到 Google 雲端的 Circuit_YYYY-MM-DD.csv（累積度數），
  磁浮 CHU-2、儲冰 BCU-1、500RT CHU-1 三台主機與全園區總表，取當天第一筆到隔天第一筆的差。
- 外氣溫：Open-Meteo 再分析（archive，與基準同一來源），08–18 時平均；約晚 5 天才有，所以每次往回補 21 天。
- 基準：data/ac_manual_baseline.csv（2025-05-05～2026-11-05 人工操作平日，InTouch 歷史電表），
  同季節（夏月 5/16–10/15）、外氣溫 ±1 °C 的人工日平均；不足 3 天放寬到 ±2 °C，仍不足就留空。
- 控制方式：2026-11-06 起，當天雲端有 mag_auto_YYYY-MM-DD.csv 記錄算「自動」，否則「人工／未記錄」。

只新增試算表沒有的日期，不覆蓋。外氣溫還沒有的日子跳過，下次再補。
"""
import csv
import io
import json
import os
import statistics
import urllib.request
from datetime import date, datetime, timedelta

from build_ems_live import fetch_circuit_files, parse_wide

SHEET_ID = "1NZ0OPky-I-oWXfFTeVR8qpFT1pFBwQvRoSerJYJJMwY"
TAB = "空調人工vs自動"
LAT, LON = 23.93673, 120.69916
DAYS_BACK = int(os.environ.get("AC_COMPARE_DAYS_BACK", "21"))
AUTO_START = date(2026, 11, 6)
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ac_manual_baseline.csv")
TAGS = {
    "mag": "F0_B1P2_EACMP_1_CHU2_KWH",
    "bcu1": "F0_B1P2_ACMP_B_BCU1_KWH",
    "chu1": "F0_B1P2_ACMP_A_CHU1_KWH",
    "main": "F0_B2P2_MCB_KWH",
}
MAX_DAY_KWH = {"mag": 4000, "bcu1": 6000, "chu1": 6000, "main": 15000}
HEADER = ["日期", "星期", "控制方式", "季節", "外氣溫08-18(°C)", "磁浮度", "儲冰度", "500RT度",
          "空調主機合計度", "全園區度", "同溫人工日數", "同溫範圍(°C)", "同溫人工平均度", "差異(%)", "備註"]
WK = "一二三四五六日"


def season(d):
    md = d.month * 100 + d.day
    return "夏月" if 516 <= md <= 1015 else "非夏月"


def day_kwh(rows, d):
    """當天 00:00 起第一筆到隔天 00:00 起第一筆的累積差；兩端都要在 30 分鐘內，否則 None。"""
    t0 = datetime(d.year, d.month, d.day)
    t1 = t0 + timedelta(days=1)
    out = {}
    for key, tag in TAGS.items():
        a = next(((t, v[tag]) for t, v in rows if t >= t0 and tag in v), None)
        b = next(((t, v[tag]) for t, v in rows if t >= t1 and tag in v), None)
        if not a or not b or a[0] - t0 > timedelta(minutes=30) or b[0] - t1 > timedelta(minutes=30):
            out[key] = None
            continue
        diff = b[1] - a[1]
        out[key] = round(diff, 1) if 0 <= diff <= MAX_DAY_KWH[key] else None
    return out


def load_baseline(path=BASELINE):
    with open(path, encoding="utf-8-sig") as f:
        return [{"date": r["日期"], "season": r["季節"], "T": float(r["外氣溫"]), "ac": float(r["空調主機度"])}
                for r in csv.DictReader(f)]


def baseline_match(base, seas, t):
    """同季節、外氣溫 ±1（不足 3 天放寬 ±2）的人工日 → (天數, 範圍, 平均)；不足回傳 (n, 範圍, None)。"""
    for w in (1, 2):
        xs = [b["ac"] for b in base if b["season"] == seas and abs(b["T"] - t) <= w]
        if len(xs) >= 3:
            return len(xs), w, round(statistics.mean(xs), 0)
    return len(xs), 2, None


def daytime_temp(days, getter=None):
    """{date: 08–18 時平均氣溫}；再分析還沒到的日子不會出現。"""
    getter = getter or (lambda u: json.load(urllib.request.urlopen(u, timeout=60)))
    a, b = min(days), max(days)
    j = getter(f"https://archive-api.open-meteo.com/v1/archive?latitude={LAT}&longitude={LON}"
               f"&start_date={a}&end_date={b}&hourly=temperature_2m&timezone=Asia%2FTaipei")
    acc = {}
    for ts, v in zip(j["hourly"]["time"], j["hourly"]["temperature_2m"]):
        t = datetime.fromisoformat(ts)
        if v is not None and 8 <= t.hour <= 18:
            acc.setdefault(t.date(), []).append(v)
    return {d: round(statistics.mean(v), 1) for d, v in acc.items() if len(v) == 11}


def build_row(d, kwh, t, auto, base):
    seas = season(d)
    mode = "自動" if auto else ("人工" if d < AUTO_START else "人工／未記錄")
    parts = [kwh.get(k) for k in ("mag", "bcu1", "chu1")]
    ac = round(sum(parts), 1) if None not in parts else None
    n, w, ref = baseline_match(base, seas, t)
    diff = round((ac - ref) / ref * 100, 1) if ac is not None and ref else None
    note = []
    if ac is None:
        note.append("主機電表資料不足")
    if ref is None:
        note.append(f"同溫人工日不足 3 天（±2 °C 只有 {n} 天）")
    return [d.isoformat(), WK[d.weekday()], mode, seas, t,
            kwh.get("mag"), kwh.get("bcu1"), kwh.get("chu1"), ac, kwh.get("main"),
            n if ref else "", f"±{w}" if ref else "", ref if ref else "", diff if diff is not None else "",
            "；".join(note)]


def _creds():
    from google.oauth2.service_account import Credentials
    return Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_CREDENTIALS"]), scopes=[
        "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.readonly"])


def main():
    import gspread
    from calendar_tw import load_calendar

    today = (datetime.utcnow() + timedelta(hours=8)).date()
    creds = _creds()
    book = gspread.authorize(creds).open_by_key(SHEET_ID)
    try:
        ws = book.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(TAB, rows=1000, cols=len(HEADER))
        ws.update(values=[HEADER], range_name="A1")
    have = set(ws.col_values(1)[1:])

    cal = load_calendar({today.year - 1, today.year})
    days = [today - timedelta(days=i) for i in range(DAYS_BACK, 0, -1)]
    days = [d for d in days if d >= AUTO_START and d.isoformat() not in have
            and d.weekday() < 5 and not cal.is_holiday(d)]
    if not days:
        print("沒有要補的平日")
        return
    temps = daytime_temp(days)
    base = load_baseline()
    new = []
    for d in days:
        if d not in temps:
            print(f"{d} 外氣溫（再分析）還沒有，下次再補")
            continue
        texts = fetch_circuit_files(creds, [d, d + timedelta(days=1)])
        rows = sorted({t: v for text in texts for t, v in parse_wide(text)}.items())
        auto = bool(fetch_circuit_files(creds, [d], prefix="mag_auto_"))
        new.append(build_row(d, day_kwh(rows, d), temps[d], auto, base))
    if new:
        ws.append_rows(new, value_input_option="USER_ENTERED")
    print(f"新增 {len(new)} 列：{[r[0] for r in new]}")


if __name__ == "__main__":
    main()
