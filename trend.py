"""實測趨勢：把試算表的實測需量與預測紀錄整理成每日／逐時表，給儀表板畫趨勢圖。

輸入都是 gspread `get_all_values()` 的結果（含標題列的字串二維陣列），不碰網路，方便測試。
"""
from datetime import date, datetime, timedelta

import pandas as pd

from forecast import contract_limit_at, tou_period

FORECAST_HOURS = range(8, 19)   # 預測涵蓋 08:00–18:00，實測日間最高用同一段比較
WEEKDAYS = "一二三四五六日"


def _date(s):
    s = str(s).strip().split(" ")[0].replace("/", "-")
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return date(y, m, d)
    except ValueError:
        return None


def _hour(s):
    try:
        return int(str(s).strip().split(":")[0])
    except ValueError:
        return None


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def parse_actual(rows):
    """「實測需量」工作表 → {(日期, 時): kW}；空白（監控停機）不列入。"""
    out = {}
    for r in rows[1:]:
        if len(r) < 3:
            continue
        d, h, v = _date(r[0]), _hour(r[1]), _num(r[2])
        if d and h is not None and v is not None:
            out[(d, h)] = v
    return out


def parse_forecast_log(rows):
    """主工作表 → {預測日期: 明日預估最高需量}；只取 v2／v3 實算列，同一天多筆取最後一筆。"""
    if not rows:
        return {}
    head = rows[0]
    try:
        i_t, i_v, i_ver = head.index("紀錄時間"), head.index("明日預估最高需量(kW)"), head.index("資料版本")
    except ValueError:
        return {}
    out = {}
    for r in rows[1:]:
        if len(r) <= max(i_t, i_v, i_ver) or not str(r[i_ver]).startswith(("v2", "v3")):
            continue
        d, v = _date(r[i_t]), _num(r[i_v])
        if d and v is not None:
            out[d + timedelta(days=1)] = v
    return out


def parse_compare(rows):
    """「預測與實測比對」工作表 → {(日期, 時): 預測 kW}。"""
    if not rows:
        return {}
    head = rows[0]
    try:
        i_d, i_h, i_f = head.index("日期"), head.index("時間"), head.index("預測需量(kW)")
    except ValueError:
        return {}
    out = {}
    for r in rows[1:]:
        if len(r) <= max(i_d, i_h, i_f):
            continue
        d, h, v = _date(r[i_d]), _hour(r[i_h]), _num(r[i_f])
        if d and h is not None and v is not None:
            out[(d, h)] = v
    return out


def daily_from_hourly(fc_hourly):
    """逐時預測 → {日期: 當日預測最高}；用來補上主工作表沒有的日子（例如歷史氣象回測）。"""
    out = {}
    for (d, _), v in fc_hourly.items():
        out[d] = max(out.get(d, v), v)
    return out


def daily_table(actual, fc_daily):
    """每日一列：實測日間最高（08–18）、全日距契約最近、預測最高與誤差。"""
    days = sorted({d for d, _ in actual} | set(fc_daily))
    rows = []
    for d in days:
        hrs = {h: actual[(d, h)] for h in range(24) if (d, h) in actual}
        day_vals = [hrs[h] for h in FORECAST_HOURS if h in hrs]
        act = max(day_vals) if day_vals else None
        margin, m_hour = None, None
        for h, v in hrs.items():
            m = contract_limit_at(d, h) - v
            if margin is None or m < margin:
                margin, m_hour = m, h
        fc = fc_daily.get(d)
        rows.append({
            "日期": pd.Timestamp(d), "星期": WEEKDAYS[d.weekday()],
            "實測日間最高(kW)": act, "預測最高(kW)": fc,
            "誤差(kW)": round(act - fc, 1) if act is not None and fc is not None else None,
            "距契約最近(kW)": round(margin, 1) if margin is not None else None,
            "距契約最近時段": f"{m_hour:02d}:00 {tou_period(d, m_hour)}" if m_hour is not None else "",
        })
    return pd.DataFrame(rows)


def hourly_table(d, actual, fc_hourly):
    """單日 24 小時：實測、預測（有才填）、該時段契約上限。"""
    return pd.DataFrame([{
        "時間": datetime(d.year, d.month, d.day, h), "時段": tou_period(d, h),
        "實測(kW)": actual.get((d, h)), "預測(kW)": fc_hourly.get((d, h)), "契約上限(kW)": contract_limit_at(d, h),
    } for h in range(24)])
