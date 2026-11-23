from datetime import datetime

import build_ems_live as b

CSV = """DateTime,F0_B2P2_MCB_KW_PV,F0_B2P2_MCB_KWH,F1_A1P1_L1B_I1_KWH,F1_A1P1_ESP_11_1_I1_KWH,F0_A2P1_ELB1A_B1_KWH_01,F0_B1P2_ACMP_A_CHU1_KWH,F0_B2P2_ACB2_1_KWH
2026/11/05 14:00:00,330,1000,10,20,5,100,50
2026/11/05 14:15:00,335,1100,12,25,6,130,100
2026/11/05 14:30:00,340,1190,,30,7,130,140
"""


def test_parse_and_intervals():
    rows = b.parse_wide(CSV)
    assert len(rows) == 3 and rows[1][1]["F0_B2P2_MCB_KWH"] == 1100
    iv = b.intervals(rows)
    assert [t.strftime("%H:%M") for t, _ in iv] == ["14:00", "14:15"]
    assert iv[0][1]["F0_B2P2_MCB_KWH"] == 400
    assert "F1_A1P1_L1B_I1_KWH" not in iv[1][1]      # 空值不算


def test_aggregate_categories():
    rows = b.parse_wide(CSV)
    a = b.aggregate(b.intervals(rows)[0][1])
    assert a["total"] == 400
    assert a["floors"] == {"1F": 28.0, "B1": 4.0}
    assert a["cats"] == {"照明": 12.0, "空調箱": 20.0}
    assert a["plant"] == {"冰水主機 CHU-1": 120.0}
    assert a["feeders"] == {"空調主機房": 200.0, "其他用電": 200.0}


def test_category():
    assert b.category("EL1B") == "照明" and b.category("ER2C_1") == "插座"
    assert b.category("ESP_11_1") == "空調箱" and b.category("P1B") == "動力"
    assert b.category("LPV3B") == "其他"


def test_counter_reset_ignored():
    assert b.kw_between(100, 90, 15) is None
    assert b.kw_between(100, 100, 15) == 0.0


def test_build():
    rows = b.parse_wide(CSV)
    d = b.build(rows, {"14:00": 350.0}, datetime(2026, 11, 5, 14, 35))
    assert d["now_kw"] == 340 and d["last15_kw"] == 360
    assert d["today_peak_kw"] == 400 and d["today_peak_at"] == "14:00"
    assert d["period"] == "半尖峰" and d["limit_kw"] == 516
    assert d["times"] == ["14:00", "14:15"] and d["total"] == [400, 360]
    assert d["today_kwh"] == 190


def test_circuit_key():
    assert b.circuit_key("F1_A1P1_EL1B_B1_KWH_10") == "EL1B_10"
    assert b.circuit_key("F1_G_P1_SP_21_3_B1_KWH_07") == "SP_21_3_7"
    assert b.circuit_key("F3_B1P1_EP3D_1_B_B1_KWH_02") == "EP3D_1_B_2"
    assert b.circuit_key("F1_A1P1_EL1B_I1_KWH") is None
    assert b.circuit_key("F0_B2P2_PB1B_KWH_1") == "PB1B_1"
    assert b.circuit_key("F0_B2P2_EPB1C_KWH_1") == "EPB1C_1"
    assert b.circuit_key("F0_B2P2_ATS_1_3_KWH") == "#ATS_1_3"
    assert b.circuit_key("F2_A2P2_ER2A_KWH") == "#ER2A"
    assert b.circuit_key("F0_B1P2_EACMP_1_KWH") == "#EACMP_1"


def test_circuit_stats():
    text = """DateTime,F0_A2P1_ELB1A_B1_KWH_01,F1_A1P1_R1B_B1_KWH_20
2026/11/05 00:00:00,100,50
2026/11/05 14:00:00,110,55
2026/11/05 14:15:00,111,54
"""
    rows = b.parse_wide(text)
    st = b.circuit_stats(rows, {"F0_A2P1_ELB1A_B1_KWH_01": 90, "F1_A1P1_R1B_B1_KWH_20": 40})
    assert st["ELB1A_1"] == [4.0, 11.0, 21.0, None, None]   # 夜間/上班時段資料不足 → None
    assert st["R1B_20"] == [None, 4.0, 14.0, None, None]    # 最後一段倒退 → 即時 kW 為 None


def test_circuit_stats_night_and_work():
    # 11/4（三）00:00–06:00 用 6 kWh → 夜間 1 kW；09:00–17:00 用 40 kWh → 上班 5 kW
    text = """DateTime,F1_A1P1_R1B_B1_KWH_20
2026/11/04 00:00:00,100
2026/11/04 06:00:00,106
2026/11/04 09:00:00,110
2026/11/04 17:00:00,150
2026/11/04 17:15:00,151
"""
    st = b.circuit_stats(b.parse_wide(text))
    assert st["R1B_20"][3:] == [1.0, 5.0]



def test_pv_summary_uses_cumulative_energy():
    from datetime import datetime
    rows = []
    meters = {"BIPV-1": 0.0, "BIPV-2": 100.0, "斜坡PV": 200.0, "鋼構PV": 300.0}
    for i, t in enumerate(["2026-10-31 23:45:00", "2026-11-05 10:00:00", "2026-11-05 10:15:00", "2026-11-05 10:30:00"]):
        for name, base in meters.items():
            rows.append([name, t, "230", "1", "5", str(base + i * 10)])   # 每支每段 +10 度
    pv = b.pv_summary(rows, datetime(2026, 11, 5, 10, 40))
    assert pv["series"] == {"10:00": 160.0, "10:15": 160.0}        # 4 支 × 10 度 × 4
    assert pv["now_kw"] == 160.0 and pv["updated"] == "2026-11-05 10:30"
    assert pv["today_kwh"] == 80.0                                  # 今天兩段 × 4 支 × 10
    assert pv["month_kwh"] == 80.0                                  # 10/31 不算本月


def test_ice_runs_span_midnight_and_mag_today():
    from datetime import timedelta
    t0 = datetime(2026, 7, 6, 23, 0)
    iv = []
    for i in range(36):                      # 23:00 → 08:00
        t = t0 + timedelta(minutes=15 * i)
        on = datetime(2026, 7, 7, 3, 15) <= t < datetime(2026, 7, 7, 7, 0)
        iv.append((t, {b.BCU_TAG: 240.0 if on else 0.4, b.MAG_TAG: 120.0 if t.hour >= 7 else 0.0}))
    runs = b.ice_runs(iv)
    assert len(runs) == 1
    r = runs[0]
    assert r["start"] == "07/07 03:15" and r["end"] == "07/07 07:00"
    assert r["hours"] == 3.75 and r["kwh"] == 900.0 and r["avg_kw"] == 240.0
    m = b.mag_today([x for x in iv if x[0].date() == datetime(2026, 7, 7).date()])
    assert m == {"kwh": 120.0, "max_kw": 120.0, "run_hours": 1.0}
    assert b.mag_today([]) is None


def test_ac_summary():
    head = "time,viewer_ok,ice01_pct,ice02_pct,mag_c1_pct,mag_c2_pct,mag_c1_kw,mag_c2_kw,mag_limit_pct"
    text = "\n".join([
        head,
        "2026-11-05 13:00:00,1,0,3.2,50,60,30,32,90",
        "2026-11-05 13:01:00,0,,,,,,,",
        "2026-11-05 13:14:00,1,0,3.4,70,80,40,42,90",
        "2026-11-05 13:15:00,1,1,3,0,0,0,0,90",
        "bad,line",
    ])
    rows = b.parse_ac([text])
    assert len(rows) == 3
    s = b.ac_summary(rows, datetime(2026, 11, 5, 13, 20))
    assert s["age_min"] == 5
    assert s["ice_pct"] == 2.0 and s["mag_pct"] == 0 and s["mag_kw"] == 0
    assert s["latest"]["mag_limit_pct"] == 90
    assert s["series"]["times"] == ["13:00", "13:15"]
    assert s["series"]["mag_pct"] == [65.0, 0.0]
    assert s["series"]["mag_kw"] == [72.0, 0.0]
    assert b.ac_summary([], datetime(2026, 11, 5)) is None


def test_taipower_summary():
    from datetime import datetime
    import build_ems_live as b
    text = ("﻿時間,時段,需量_kW,契約容量_kW,使用率_%\n"
            "2026-11-23 00:00,離峰,140,616,22.7\n"        # 區間 23:45–00:00 屬前一天
            "2026-11-23 09:15,半尖峰,307,516,59.5\n"
            "2026-11-23 13:15,離峰,285,616,46.3\n")
    d = b.taipower_summary(text, {"ok": True, "updated": "2026-11-23 13:40:01"}, datetime(2026, 11, 23, 13, 45))
    assert d["times"] == ["09:00", "13:00"]
    assert d["peak_kw"] == 307 and d["peak_end"] == "09:15"
    assert d["last_end"] == "13:15" and d["last_limit"] == 616 and d["age_min"] == 30
    assert d["over_count"] == 0 and d["scraper_ok"] is True
    assert b.taipower_summary("時間,時段,需量_kW,契約容量_kW,使用率_%\n", None, datetime(2026, 11, 23)) is None


def test_build_taipower_json():
    h = "時間,時段,需量_kW,契約容量_kW,使用率_%\n"
    full = h + "2025-08-28 11:30,半尖峰,530,516,102.7\n2025-08-28 16:15,尖峰,400,452,88.5\n"
    recent = h + ("2026-11-22 00:00,離峰,300,616,48.7\n2026-11-23 00:15,離峰,310,616,50.3\n"
                  "2026-11-23 07:45,半尖峰,420,516,81.4\n2026-11-23 08:00,半尖峰,470,516,91.1\n")
    d = b.build_taipower_json(full, recent, {"ok": True, "updated": "x"}, datetime(2026, 11, 23, 8, 10), days=3)
    assert set(d["days"]) == {"2026-11-21", "2026-11-23"}           # 11/22 00:00 結束那筆屬於 11/21；2025 不在逐日範圍
    assert d["days"]["2026-11-21"]["kw"][95] == 300
    day = d["days"]["2026-11-23"]
    assert day["kw"][0] == 310 and day["kw"][30] == 420 and day["kw"][31] == 470 and day["kw"][1] is None
    assert day["lim"] == [[0, 616], [30, 516]]
    aug = [m for m in d["months"] if m["month"] == "2025-08" and m["period"] == "半尖峰"][0]
    assert aug["max"] == 530 and aug["at"] == "08-28 11:30" and aug["nover"] == 1 and aug["pct"] == 102.7
    nov = [m for m in d["months"] if m["month"] == "2026-11" and m["period"] == "半尖峰"][0]
    assert nov["n90"] == 1 and nov["nover"] == 0
    assert d["over"] == [{"t": "2025-08-28 11:30", "kw": 530, "limit": 516, "period": "半尖峰"}]
    y2025 = d["years"][0]
    assert y2025["year"] == 2025 and y2025["max"] == 530 and y2025["kwh"] == round((530 + 400) * 0.25)
    assert d["last_end"] == "2026-11-23 08:00" and d["age_min"] == 10 and d["scraper_ok"] is True
    assert b.build_taipower_json(None, h, None, datetime(2026, 11, 23)) is None


def test_forecast_check():
    from datetime import date
    rows = []
    for h in (9, 10, 11):
        for m in (0, 15, 30, 45):
            rows.append((datetime(2026, 11, 20, h, m), 400 + h + m / 15, 516, "半尖峰"))
    rows.append((datetime(2026, 11, 21, 9, 0), 300, 616, "離峰"))           # 11/21 不齊 → 不列
    fc = {(date(2026, 11, 20), 9): 390.0, (date(2026, 11, 20), 10): 395.0, (date(2026, 11, 20), 11): 392.0,
          (date(2026, 11, 21), 9): 300.0}
    r = b.forecast_check(fc, rows, lambda d: False, offset=14.0, today=date(2026, 11, 23))
    assert len(r["days"]) == 1
    d = r["days"][0]
    assert d["pred"] == 409.0 and d["pred_hour"] == "10:00"                 # 395 + 14
    assert d["actual"] == 414 and d["actual_end"] == "12:00"                # 11:45 起那筆 400+11+3
    assert d["err"] == 5.0 and d["actual_h"] == [412, 413, 414] and d["pred_h"] == [404.0, 409.0, 406.0]
    assert r["work_summary"]["mae"] == 5.0 and r["work_summary"]["within5"] == 1


def test_parse_compare_rows_last_wins():
    rows = [["日期", "時間", "星期", "台電時段", "契約上限(kW)", "預測需量(kW)"],
            ["2026-11-20", "09:00", "五", "半尖峰", "516", "390"],
            ["2026-11-20", "09:00", "五", "半尖峰", "516", "395.5"], ["bad"]]
    from datetime import date
    assert b.parse_compare_rows(rows) == {(date(2026, 11, 20), 9): 395.5}


def test_forecast_check_keeps_hourly_detail_for_last_workday():
    from datetime import date
    rows, fc = [], {}
    for day in (20, 21):                                    # 11/20 五 上班、11/21 六 假日
        for m in (0, 15, 30, 45):
            rows.append((datetime(2026, 11, day, 9, m), 300.0, 516, "半尖峰"))
        fc[(date(2026, 11, day), 9)] = 290.0
    r = b.forecast_check(fc, rows, lambda d: d.weekday() >= 5, offset=14.0, today=date(2026, 11, 23))
    assert [("hours" in o) for o in r["days"]] == [True, False]
    assert r["work_summary"]["days"] == 1
