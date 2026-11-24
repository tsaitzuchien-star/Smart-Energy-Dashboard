"""自來水用量判讀（監控電腦告警與雲端 water.json 共用，同一套規則）。

資料特性（2026-11-24 由 10/25–11/23 讀值判讀）：
- 台水智慧水表每天只給幾個時刻的累計指針（00、03、06、15 時），單位整數度（m³）。
- 指針多半一次跳 21–22 度：園區是受水池／水塔浮球補水，市水表記到的是「補水時間」不是「用水時間」。
  因此：
  * 水塔「上游」（市水表到水塔之間埋管）漏水 → 夜間指針會持續走 → 用「連續夜間用水」抓。
  * 水塔「下游」（大樓管線、馬桶、中水補水閥）漏水 → 只會表現成補水變多 → 用「每 7 天總量」「假日用量」對基線抓。
- 單日用量會因補水落在哪天而在 ~22 與 ~44 之間跳，所以單日不當主要判斷，以 7 天合計為主。

日用量＝隔天 00 時指針 − 當天 00 時指針（台水頁面的「估算量」只到 15 時，會少算）。
"""
from datetime import date, datetime, timedelta
from statistics import median

# ---- 告警門檻（保守：寧可晚一兩天，不要天天誤報；之後可用一整年資料再調）----
NIGHT_FROM, NIGHT_TO = 0, 6        # 夜間時段 00–06 時
NIGHT_MIN = 2                      # 夜間 00–06 扣掉補水後仍 ≥2 度（≈5.6 公升／分以上的持續流）
NIGHT_STREAK = 3                   # 連續 3 晚 → 疑似水塔上游持續漏水
WEEK_WARN, WEEK_WARN_ABS = 1.30, 30    # 近 7 天合計 > 基線 ×1.3 且多 30 度以上 → 注意
WEEK_ALARM, WEEK_ALARM_ABS = 1.60, 60  # > 基線 ×1.6 且多 60 度以上 → 告警
BASE_WEEKS = 8                     # 7 天基線＝前 8 個「7 天」合計的中位數
OFF_WARN_ABS = 30                  # 假日單日 > 假日基線 + 30 度（多補一次以上水塔）→ 注意
OFF_BASE_DAYS = 12
DAY_WARN_MULT, DAY_WARN_MIN = 2.2, 70  # 單日 ≥ 上班日基線 ×2.2 且 ≥70 度 → 注意
WORK_BASE_DAYS = 20
STALE_DAYS = 3                     # 最新指針超過 3 天沒更新 → 資料中斷
REFILL_RATE = 5                    # 某段每小時 ≥5 度（≈83 公升／分）視為水塔補水，不算持續流（受水池一次補約 21–22 度）

RESOLUTION_NOTE = [
    "台水智慧水表每小時一筆指針、整數度（1 度＝1 m³）：夜間 6 小時要累積到 2 度才判得出，約每分鐘 5.6 公升以下的小漏看不出來。",
    "園區是水塔／受水池補水（一次約 21–22 度），水表看到的是補水時間，不是用水時間；夜間有一筆大跳升多半是半夜補水，不是漏水。",
    "看得出：水塔上游埋管持續漏水（夜間指針一直走）、用量整體變多（7 天合計、假日用量高於基線）、單日暴增。",
    "看不出：漏在哪一棟、哪一層；水塔下游的小漏要累積幾天才看得出；這些要靠現場巡檢、分棟水表或水塔液位紀錄。",
    "對外正式數字以台水帳單與總表 02「四、用水」為準；本頁是智慧水表原始讀數。",
]


def _d(s):
    return s if isinstance(s, date) else datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def readings_map(rows):
    """[{date,hour,reading}] → {date: {hour: reading}}"""
    out = {}
    for r in rows:
        try:
            v = float(r["reading"])
        except (TypeError, ValueError):
            continue
        if v != v:   # NaN
            continue
        out.setdefault(_d(r["date"]), {})[int(r["hour"])] = v
    return out


def daily_table(rows, estimates=None, is_holiday=None):
    """逐日：use（日用量）、night（00–06）、night_base（夜間扣掉補水的持續流）、steps、refills、型別。"""
    rm = readings_map(rows)
    est = {}
    for e in estimates or []:
        try:
            v = float(e.get("est"))
            est[_d(e["date"])] = v if v == v else None
        except (TypeError, ValueError):
            pass
    is_holiday = is_holiday or (lambda d: d.weekday() >= 5)
    out = []
    for d in sorted(rm):
        hrs = rm[d]
        nxt = rm.get(d + timedelta(days=1), {})
        pts = sorted(hrs.items())
        if 0 in nxt:
            pts.append((24, nxt[0]))
        steps = []
        for (h1, v1), (h2, v2) in zip(pts, pts[1:]):
            steps.append({"from": h1, "to": h2, "use": round(v2 - v1, 3)})
        use = round(nxt[0] - hrs[0], 3) if (0 in hrs and 0 in nxt) else None
        night_steps = [s for s in steps if s["from"] >= NIGHT_FROM and s["to"] <= NIGHT_TO]
        for st in steps:
            st["refill"] = st["use"] / max(1, st["to"] - st["from"]) >= REFILL_RATE
        # 夜間「持續流」＝夜間扣掉補水那幾段後的用量；連續幾晚都有才像漏水（半夜補一次水塔不算）
        night_base = round(sum(st["use"] for st in night_steps if not st["refill"] and st["use"] > 0), 3) if night_steps else None
        night = round(hrs[NIGHT_TO] - hrs[NIGHT_FROM], 3) if (NIGHT_FROM in hrs and NIGHT_TO in hrs) else None
        refills, prev = 0, False
        for st in steps:                                  # 連續幾段都在補水算同一次
            refills += st["refill"] and not prev
            prev = st["refill"]
        out.append({
            "date": d.isoformat(),
            "off": bool(is_holiday(d)),
            "use": use,
            "est": est.get(d),
            "night": night,
            "night_base": night_base,
            "night_moving": night_base is not None and night_base >= NIGHT_MIN,
            "refills": refills,
            "neg": any(s["use"] < 0 for s in steps),
            "hours": {str(h): v for h, v in sorted(hrs.items())},
            "steps": steps,
        })
    return out


def _rolling7(days):
    """每一天結尾的近 7 天合計（7 天都有用量才算）。"""
    by = {_d(x["date"]): x["use"] for x in days}
    out = {}
    for d in by:
        vals = [by.get(d - timedelta(days=k)) for k in range(7)]
        if all(v is not None for v in vals):
            out[d] = sum(vals)
    return out


def weekly_table(days):
    """週一到週日的週合計（只算 7 天都齊的週）。"""
    by = {_d(x["date"]): x["use"] for x in days}
    weeks = {}
    for d, v in by.items():
        weeks.setdefault(d - timedelta(days=d.weekday()), []).append(v)
    out = []
    for mon in sorted(weeks):
        vals = [by.get(mon + timedelta(days=k)) for k in range(7)]
        if all(v is not None for v in vals):
            out.append({"start": mon.isoformat(), "use": round(sum(vals), 1)})
    return out


def baselines(days, upto=None):
    """以 upto（含）之前的資料算基線：上班日中位數、假日中位數、7 天合計中位數。"""
    upto = _d(upto) if upto else None
    hist = [x for x in days if x["use"] is not None and (upto is None or _d(x["date"]) <= upto)]
    work = [x["use"] for x in hist if not x["off"]][-WORK_BASE_DAYS:]
    off = [x["use"] for x in hist if x["off"]][-OFF_BASE_DAYS:]
    r7 = _rolling7(hist)
    wk = []
    if r7:
        last = max(r7)
        for k in range(1, BASE_WEEKS + 1):
            v = r7.get(last - timedelta(days=7 * k))
            if v is not None:
                wk.append(v)
    return {
        "work": median(work) if len(work) >= 5 else None, "n_work": len(work),
        "off": median(off) if len(off) >= 4 else None, "n_off": len(off),
        "week": median(wk) if len(wk) >= 3 else None, "n_week": len(wk),
    }


def evaluate(days, status=None, today=None):
    """回傳告警清單 [{level: alarm|warn|info, code, title, text, date}]，最新的判斷。"""
    today = _d(today) if today else date.today()
    alerts = []
    status = status or {}
    known = [x for x in days if x["hours"]]
    if not known:
        return [{"level": "info", "code": "nodata", "title": "尚無水表資料", "text": "爬蟲還沒抓到資料。", "date": today.isoformat()}]
    last_day = _d(known[-1]["date"])
    if (today - last_day).days > STALE_DAYS:
        alerts.append({"level": "info", "code": "stale", "title": "水表資料中斷",
                       "text": f"最新指針是 {last_day:%m/%d}，已超過 {STALE_DAYS} 天沒更新（台水網站或爬蟲異常）。",
                       "date": last_day.isoformat()})

    # 1. 連續夜間用水（水塔上游持續漏水）
    streak = []
    for x in reversed([x for x in days if x["night"] is not None]):
        if x["night_moving"]:
            streak.append(x)
        else:
            break
    if len(streak) >= NIGHT_STREAK:
        avg = sum(x["night_base"] for x in streak) / len(streak)
        alerts.append({"level": "alarm", "code": "night", "title": "疑似持續漏水（夜間指針一直走）",
                       "text": f"連續 {len(streak)} 晚 00–06 時扣掉水塔補水後仍有持續用水，平均每晚 {avg:.1f} 度"
                               f"（約 {avg / 6 * 1000 / 60:.1f} 公升／分）。水塔上游埋管、總表後管線優先巡檢。",
                       "date": streak[0]["date"]})

    used = [x for x in days if x["use"] is not None]
    if used:
        last = used[-1]
        ld = _d(last["date"])
        base = baselines(days, upto=ld - timedelta(days=7))     # 基線不含最近 7 天，避免被異常本身拉高
        r7 = _rolling7(used).get(ld)
        if r7 is not None and base["week"]:
            b = base["week"]
            if r7 > b * WEEK_ALARM and r7 - b >= WEEK_ALARM_ABS:
                lvl = "alarm"
            elif r7 > b * WEEK_WARN and r7 - b >= WEEK_WARN_ABS:
                lvl = "warn"
            else:
                lvl = None
            if lvl:
                alerts.append({"level": lvl, "code": "week", "title": "近 7 天用水偏高",
                               "text": f"{ld - timedelta(days=6):%m/%d}–{ld:%m/%d} 合計 {r7:.0f} 度，基線 {b:.0f} 度"
                                       f"（{(r7 / b - 1) * 100:+.0f}%）。若沒有活動、施工、清洗水塔，請巡檢馬桶、中水／雨水補水閥、水塔溢流。",
                               "date": last["date"]})
        if last["off"] and base["off"] is not None and last["use"] > base["off"] + OFF_WARN_ABS:
            alerts.append({"level": "warn", "code": "offday", "title": "假日用水偏高",
                           "text": f"{ld:%m/%d}（假日）用 {last['use']:.0f} 度，假日基線 {base['off']:.0f} 度。",
                           "date": last["date"]})
        if base["work"] and last["use"] >= max(DAY_WARN_MIN, base["work"] * DAY_WARN_MULT):
            alerts.append({"level": "warn", "code": "day", "title": "單日用水暴增",
                           "text": f"{ld:%m/%d} 用 {last['use']:.0f} 度，上班日基線 {base['work']:.0f} 度。",
                           "date": last["date"]})
        if any(x["neg"] for x in used[-7:]):
            alerts.append({"level": "info", "code": "neg", "title": "指針倒退",
                           "text": "近 7 天有指針變小的時段（換表或台水資料修正），該段用量不可信。", "date": last["date"]})

    an = status.get("anomaly_status")
    if an and an != "尚無資料":
        alerts.append({"level": "warn", "code": "taiwater", "title": "台水異常通報", "text": str(an),
                       "date": today.isoformat()})
    return alerts


def monthly_meter(days):
    """智慧水表逐月用量（用水月，日用量加總；缺天數另列）。"""
    out = {}
    for x in days:
        m = x["date"][:7]
        o = out.setdefault(m, {"use": 0.0, "n": 0})
        if x["use"] is not None:
            o["use"] += x["use"]
            o["n"] += 1
    return {m: {"use": round(v["use"], 1), "days": v["n"]} for m, v in sorted(out.items())}


def alert_text(a):
    icon = {"alarm": "🚨", "warn": "⚠️", "info": "ℹ️"}.get(a["level"], "")
    return f"{icon}{a['title']}\n{a['text']}"
