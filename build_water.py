"""用水戰情室 water.json：監控電腦 water_live.py 每天把台水智慧水表指針放到 Google 雲端「BMS自動匯出\\自來水」，
這裡（ems_live 工作流程）整理成 water.json 放到 ems-live 分支給網站 water.html 讀。

同期比較用台水帳單月（同仁每月填的「中創園區能源資訊統計比較」用水頁，帳單月＝用水月＋1）；
智慧水表 2026-01 才申裝，逐日資料要到 2027 年才有去年同期可比。
"""
import io
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "water"))
import water_analysis as wa  # noqa: E402

READINGS = "water_readings.csv"
ESTIMATES = "water_estimates.csv"
STATUS = "water_status.json"
STATS_NAME = "能源資訊統計比較"      # 同仁的能源統計表（檔名含這幾個字，取最新一份）
KEEP_DAYS = 400


def _csv_rows(text):
    import csv
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def parse_bill_sheet(ws_rows):
    """能源統計表「用水」頁 → {'FY114': [12 個月], 'FY115': [...]}（自來水，帳單月；回收水表格之前的那幾列）。"""
    out = {}
    for r in ws_rows:
        if not r:
            continue
        head = str(r[0] or "")
        if "回收水" in head:
            break
        if head.startswith("FY") and head.endswith("水度"):
            vals = []
            for v in list(r[1:13]) + [None] * (12 - len(r[1:13])):
                vals.append(float(v) if isinstance(v, (int, float)) else None)
            out[head[:5]] = vals
    return out


def _latest(days):
    """最後一個水表指針的日期時刻（台水智慧水表約每天凌晨一次上傳前一天的逐時資料）。"""
    for x in reversed(days):
        if x["hours"]:
            h = max(int(k) for k in x["hours"])
            return {"date": x["date"], "hour": h, "reading": x["hours"][str(h)]}
    return None


def build_water_json(readings_text, estimates_text, status, now, is_holiday=None, bills=None, bills_src=None):
    rows = _csv_rows(readings_text)
    if not rows:
        return None
    days = wa.daily_table(rows, _csv_rows(estimates_text), is_holiday)
    alerts = wa.evaluate(days, status, today=now.date())
    used = [x for x in days if x["use"] is not None]
    last = used[-1] if used else None
    base = wa.baselines(days, upto=(wa._d(last["date"]) - timedelta(days=7)) if last else None)
    r7 = wa._rolling7(used)
    cut = (now.date() - timedelta(days=KEEP_DAYS)).isoformat()
    slim = [{"d": x["date"], "off": x["off"], "use": x["use"], "est": x["est"], "night": x["night"],
             "nm": x["night_moving"], "rf": x["refills"], "h": x["hours"],
             "r7": r7.get(wa._d(x["date"])),
             "up": x["use_partial"], "ut": x["partial_to"]} for x in days if x["date"] >= cut]
    hours = sorted({int(h) for x in days for h in x["hours"]})
    st = {k: status.get(k) for k in ("meter_update_time", "current_reading", "current_period_usage", "next_period_usage",
                                     "current_fee", "last_fee", "next_fee", "outage_notice", "outage_list", "anomaly_status",
                                     "scraped_at", "scrape_ok", "scrape_error", "updated_at")} if status else {}
    return {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "water_no": "B1801146550",
        "data_until": last["date"] if last else None,
        "latest_reading": _latest(days),
        "first_day": days[0]["date"],
        "hours": hours,
        "status": st,
        "alerts": alerts,
        "baseline": base,
        "last7": r7.get(wa._d(last["date"])) if last else None,
        "days": slim,
        "weeks": wa.weekly_table(days)[-60:],
        "months": wa.monthly_meter(days),
        "bills": bills or {},
        "bills_src": bills_src,
        "rules": {
            "night": f"連續 {wa.NIGHT_STREAK} 晚 00–06 時扣掉水塔補水（每小時 ≥{wa.REFILL_RATE} 度的時段）後仍 ≥{wa.NIGHT_MIN} 度 → 疑似持續漏水",
            "week": f"近 7 天合計 > 基線 ×{wa.WEEK_WARN}（且多 {wa.WEEK_WARN_ABS} 度）注意；×{wa.WEEK_ALARM}（且多 {wa.WEEK_ALARM_ABS} 度）告警。基線＝最近 7 天之前 {wa.BASE_WEEKS} 個 7 天合計的中位數",
            "offday": f"假日單日 > 假日基線 + {wa.OFF_WARN_ABS} 度 → 注意",
            "day": f"單日 ≥ 上班日基線 ×{wa.DAY_WARN_MULT} 且 ≥{wa.DAY_WARN_MIN} 度 → 注意",
        },
        "resolution": wa.RESOLUTION_NOTE,
    }


def _drive_find(session, q):
    r = session.get("https://www.googleapis.com/drive/v3/files",
                    params={"q": q, "fields": "files(id,name,modifiedTime)", "orderBy": "modifiedTime desc",
                            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    r.raise_for_status()
    return r.json().get("files", [])


def _drive_bytes(session, fid):
    c = session.get(f"https://www.googleapis.com/drive/v3/files/{fid}", params={"alt": "media"})
    c.raise_for_status()
    return c.content


def fetch_bills(session):
    try:
        files = [f for f in _drive_find(session, f"name contains '{STATS_NAME}' and trashed = false")
                 if f["name"].endswith(".xlsx")]
        if not files:
            return None, None
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(_drive_bytes(session, files[0]["id"])), read_only=True, data_only=True)
        if "用水" not in wb.sheetnames:
            return None, None
        bills = parse_bill_sheet(list(wb["用水"].iter_rows(values_only=True)))
        return bills, {"name": files[0]["name"], "modified": files[0].get("modifiedTime", "")[:10]}
    except Exception as e:   # noqa: BLE001 — 帳單比較是輔助
        print(f"讀用水帳單失敗：{e}")
        return None, None


def fetch_water(creds, now, is_holiday=None):
    """讀不到（爬蟲還沒裝）回傳 None，不影響其他頁面。"""
    try:
        from google.auth.transport.requests import AuthorizedSession
        s = AuthorizedSession(creds)

        def text(name):
            f = _drive_find(s, f"name = '{name}' and trashed = false")
            return _drive_bytes(s, f[0]["id"]).decode("utf-8-sig", errors="replace") if f else None

        rd = text(READINGS)
        if not rd:
            return None
        st = text(STATUS)
        bills, src = fetch_bills(s)
        return build_water_json(rd, text(ESTIMATES), json.loads(st) if st else {}, now, is_holiday, bills, src)
    except Exception as e:   # noqa: BLE001
        print(f"讀自來水資料失敗：{e}")
        return None
