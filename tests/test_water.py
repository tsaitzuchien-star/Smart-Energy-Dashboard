import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools", "water"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import build_water  # noqa: E402
import water_analysis as wa  # noqa: E402
import water_common as wc  # noqa: E402

PAGE = """用水指針查詢
顯示時段 查詢
2026/11/22
00:00~24:00 估算量 10
00時 7,896
03時 7896
06時 7896
15時 7906
2026/11/23
00:00~24:00 估算量 1
00時 7916 03時 7917 06時 7917 15時 7917
2026/11/24
00:00~24:00 估算量 0
"""


def test_parse_pointer_text():
    rd, es = wc.parse_pointer_text(PAGE)
    assert {(r["date"], r["hour"], r["reading"]) for r in rd} >= {("2026-11-22", 0, 7896.0), ("2026-11-22", 15, 7906.0),
                                                                  ("2026-11-23", 3, 7917.0)}
    assert len(rd) == 8                          # 11/24 沒有指針 → 不收
    assert {e["date"]: e["est"] for e in es} == {"2026-11-22": 10.0, "2026-11-23": 1.0}


def _rows(series, hours=(0, 3, 6, 15)):
    """series: {date: {hour: reading}}"""
    return [{"date": d.isoformat(), "hour": h, "reading": v} for d, hv in series.items() for h, v in hv.items()]


def _steady(start, n, per_day=22, night=0):
    """每天 00 時起：夜間用 night 度（平均分到 00–03、03–06），白天 15 時前用 per_day−night。"""
    out, r = {}, 1000.0
    for k in range(n):
        d = start + timedelta(days=k)
        h = night / 2
        out[d] = {0: r, 3: r + h, 6: r + night, 15: r + per_day}
        r += per_day
    return out


def test_daily_use_uses_next_midnight():
    s = _steady(date(2026, 11, 2), 3)
    days = wa.daily_table(_rows(s), [{"date": "2026-11-02", "est": 5}])
    assert days[0]["use"] == 22 and days[0]["est"] == 5
    assert days[-1]["use"] is None               # 沒有隔天 00 時


def test_old_csv_import(tmp_path):
    p = tmp_path / "old.csv"
    p.write_text("date,daily_usage,reading_00,reading_03,reading_06,reading_15,night_usage\n"
                 "2026-10-27,21.0,7294.0,7294.0,7294.0,7315.0,0.0\n2026-10-28,18.0,7337.0,7337.0,7337.0,7355.0,0.0\n",
                 encoding="utf-8-sig")
    rd, es = wc.import_old_csv(str(p))
    days = wa.daily_table(rd, es)
    assert days[0]["use"] == 43 and days[0]["est"] == 21    # 估算量只到 15 時，實際 43


def test_no_alert_on_normal_and_single_night_refill():
    s = _steady(date(2026, 9, 1), 70)
    d = date(2026, 11, 5)
    s[d] = {0: s[d][0], 3: s[d][0] + 21, 6: s[d][0] + 21, 15: s[d][0] + 22}   # 半夜補一次水塔
    days = wa.daily_table(_rows(s))
    al = wa.evaluate(days, {}, today=date(2026, 11, 10))
    assert [a for a in al if a["level"] != "info"] == []


def test_night_leak_alarm():
    s = _steady(date(2026, 9, 1), 60)
    last = max(s)
    r = s[last][0]
    for k in range(1, 5):                        # 最後 4 天夜間持續走 4 度
        d = last + timedelta(days=k)
        s[d] = {0: r, 3: r + 2, 6: r + 4, 15: r + 26}
        r += 26
    days = wa.daily_table(_rows(s))
    al = wa.evaluate(days, {}, today=max(s) + timedelta(days=1))
    assert any(a["code"] == "night" and a["level"] == "alarm" for a in al)


def test_week_alarm_when_usage_jumps():
    s = _steady(date(2026, 8, 1), 80, per_day=22)
    last = max(s)
    r = s[last][0] + 22
    for k in range(1, 9):                        # 近 8 天每天 44 度（翻倍）
        d = last + timedelta(days=k)
        s[d] = {0: r, 3: r, 6: r, 15: r + 44}
        r += 44
    s[max(s) + timedelta(days=1)] = {0: r}
    days = wa.daily_table(_rows(s))
    al = wa.evaluate(days, {}, today=max(s))
    w = [a for a in al if a["code"] == "week"]
    assert w and w[0]["level"] == "alarm"


def test_stale_and_taiwater_flags():
    s = _steady(date(2026, 10, 1), 20)
    days = wa.daily_table(_rows(s))
    al = wa.evaluate(days, {"anomaly_status": "連續用水"}, today=max(s) + timedelta(days=10))
    codes = {a["code"] for a in al}
    assert "stale" in codes and "taiwater" in codes


def test_parse_bill_sheet():
    rows = [("水號", "4R"), ("FY114水度", 433, 308, 352, 425, 488, 700, 738, 627, 876, 787, 733, 748, 7215),
            ("FY115水度", 455, 437, 439, 543, 633, 789, 1024, 926, 978, 837, 1297, None, 8358),
            ("回收水(雨水+中水)量統計表",), ("FY114水度", 1, 2)]
    b = build_water.parse_bill_sheet(rows)
    assert b["FY114"][0] == 433 and b["FY115"][10] == 1297 and b["FY115"][11] is None
    assert sum(b["FY115"][:11]) == 8358          # 對總表 02「四、用水」1–11 帳單月 8,358 度


def test_build_water_json():
    s = _steady(date(2026, 10, 1), 40)
    text = "date,hour,reading\n" + "\n".join(f"{r['date']},{r['hour']},{r['reading']}" for r in _rows(s))
    j = build_water.build_water_json(text, "date,est\n2026-10-01,9\n", {"current_reading": 1}, datetime(2026, 11, 10, 9, 0))
    assert j["data_until"] == "2026-11-08" and j["hours"] == [0, 3, 6, 15]
    assert j["days"][0]["est"] == 9.0 and j["last7"] == 154
    assert j["baseline"]["week"] == 154


def test_results_part_and_zero_cleanup():
    text = "停水公告 2026/11/24 ...\n資料回寫\n2026/07/05\n估算量\n3585\n00時\n0\n03時\n3564\n2026/07/06\n00時\n3585\n03時\n3585\n"
    rd, _ = wc.parse_pointer_text(wc.results_part(text))
    rd = wc.clean_readings(rd)
    assert ("2026-07-05", 0) not in {(r["date"], r["hour"]) for r in rd}
    assert {r["date"] for r in rd} == {"2026-07-05", "2026-07-06"}


def test_hourly_refill_split_is_not_leak():
    """逐時資料：半夜補水拆成兩小時（10＋12）仍算補水，不算持續流；每小時 1 度的持續流連 3 晚要告警。"""
    s, r = {}, 1000.0
    for k in range(40):
        d = date(2026, 10, 1) + timedelta(days=k)
        hv = {h: r for h in range(24)}
        if k == 30:
            hv.update({h: r + (10 if h == 3 else 22) for h in range(3, 24)} | {h: r + 10 for h in (3,)})
        hv = {h: v for h, v in hv.items()}
        s[d] = hv
        r += 22 if k == 30 else 0
        for h in range(12, 24):
            s[d][h] = s[d][h] + 22
        r += 22
    days = wa.daily_table(_rows(s, hours=range(24)))
    assert not any(x["night_moving"] for x in days)
    last = max(s)
    for k in range(1, 5):                       # 再加 4 晚：00–06 每小時 1 度
        d = last + timedelta(days=k)
        s[d] = {h: r + min(h, 6) + (22 if h >= 12 else 0) for h in range(24)}
        r += 6 + 22
    days = wa.daily_table(_rows(s, hours=range(24)))
    al = wa.evaluate(days, {}, today=max(s) + timedelta(days=1))
    assert any(a["code"] == "night" for a in al)
