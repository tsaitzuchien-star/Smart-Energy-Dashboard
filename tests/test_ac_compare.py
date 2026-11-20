from datetime import date, datetime, timedelta

import ac_compare as m


def _rows(d, per_day):
    """d 00:00 到隔天 00:15，每 15 分鐘一筆，各標籤每天累積 per_day 度。"""
    out = []
    t0 = datetime(d.year, d.month, d.day)
    for i in range(98):
        t = t0 + timedelta(minutes=15 * i, seconds=20)
        out.append((t, {tag: 1000 + per_day[k] * i / 96 for k, tag in m.TAGS.items()}))
    return out


def test_day_kwh_and_row():
    d = date(2026, 11, 20)
    kwh = m.day_kwh(_rows(d, {"mag": 600, "bcu1": 30, "chu1": 1, "main": 5000}), d)
    assert kwh == {"mag": 600.0, "bcu1": 30.0, "chu1": 1.0, "main": 5000.0}
    base = [{"date": "x", "season": "非夏月", "T": t, "ac": ac} for t, ac in
            [(22.0, 200), (22.4, 300), (21.6, 400), (25.0, 900), (22.1, 1000)]]
    base[-1]["season"] = "夏月"
    row = m.build_row(d, kwh, 22.2, True, base)
    assert row[2] == "自動" and row[3] == "非夏月"
    assert row[8] == 631.0 and row[10] == 3 and row[11] == "±1" and row[12] == 300
    assert row[13] == 110.3


def test_missing_and_widen():
    d = date(2026, 7, 1)
    assert m.season(d) == "夏月" and m.season(date(2026, 10, 16)) == "非夏月"
    kwh = m.day_kwh(_rows(d, {"mag": 1, "bcu1": 1, "chu1": 1, "main": 1})[:50], d)
    assert kwh["mag"] is None
    base = [{"season": "夏月", "T": t, "ac": 100} for t in (29.0, 31.5, 28.4)]
    assert m.baseline_match(base, "夏月", 30.0) == (3, 2, 100)
    assert m.baseline_match(base[:2], "夏月", 30.0)[2] is None
    row = m.build_row(d, kwh, 30.0, False, base[:2])
    assert row[8] is None and row[13] == "" and "不足" in row[14]


def test_daytime_temp():
    days = [date(2026, 11, 10)]
    times = [f"2026-11-10T{h:02d}:00" for h in range(24)]
    j = {"hourly": {"time": times, "temperature_2m": [20.0 if 8 <= h <= 18 else 10.0 for h in range(24)]}}
    assert m.daytime_temp(days, getter=lambda u: j) == {date(2026, 11, 10): 20.0}


def test_special_day_has_no_diff():
    d = date(2026, 11, 16)
    base = [{"season": "非夏月", "T": 25.0, "ac": 500}] * 3
    row = m.build_row(d, {"mag": 600, "bcu1": 1500, "chu1": 1, "main": 7000}, 25.0, True, base)
    assert row[13] == "" and "製冰測試" in row[14]


def test_hot_rooms():
    txt = "time,mode,hot_share\n2026-11-20T07:30:00,x,0.9\n2026-11-20T09:00:00,x,0.1\n2026-11-20T15:00:00,x,0.3\n2026-11-20T17:00:00,x,\n"
    assert m.hot_rooms(txt) == (20.0, 30.0)
    assert m.hot_rooms("") == ("", "")
