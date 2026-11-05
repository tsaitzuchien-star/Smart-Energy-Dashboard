import datetime as dt

import build_ems_bill as b

HEAD = ("日期", "院區", "台電電度", "台電電費", "太陽能用電", "太陽能電費", "用電-尖峰", "用電-半尖峰", "用電-周六半尖峰",
        "用電-離峰", "罰款-尖峰", "罰款-半尖峰", "罰款-周六半尖峰", "罰款-離峰", "轉供度數", "轉供-尖峰", "轉供-半尖峰",
        "轉供-周六半尖峰", "轉供-離峰", "空調用電", "部門")


def row(y, m, kwh=180.0, fee=900.0, pv=20.0, pv_fee=100.0, d=(0, 480, 220, 480), p=(None,) * 4, site="中創"):
    return (dt.datetime(y, m, 1), site, kwh, fee, pv, pv_fee, *d, *p, None, None, None, None, None, 30.0, "H300")


def sheet(extra=()):
    rows = [HEAD]
    for y in (2024, 2025):
        for m in range(1, 13):
            rows.append(row(y, m, pv=20.0 + m + y % 10))
    rows += list(extra)
    rows.append(row(2026, 1, kwh=None, fee=None, pv=None, pv_fee=None, d=(None,) * 4))   # 還沒填
    rows.append(row(2025, 5, site="六甲"))
    return rows


def test_parse_keeps_site_and_drops_unfilled_months():
    d = b.build(sheet())
    assert d["months"][0] == "2024/01" and d["months"][-1] == "2025/12" and len(d["months"]) == 24
    assert d["kwh"][0] == 180000 and d["fee"][0] == 900000
    assert d["demand"]["半尖峰"][0] == 480


def test_checks_flag_typos_copies_and_missing_penalty():
    rows = sheet()
    rows[5] = row(2024, 5, fee=300.0, pv=rows[5][4])                                    # 單價太低
    rows[8] = row(2024, 8, pv=rows[7][4], pv_fee=rows[7][5])             # 太陽能和上月相同
    rows[10] = row(2024, 10, pv=rows[10][4], d=(454, 480, 220, 480))                     # 尖峰超約沒填罰款
    rows[11] = row(2024, 11, pv=rows[11][4], d=(0, 528, 220, 480), p=(None, 4.005, None, None))   # 有罰款，沒問題
    msgs = {(x["month"], x["level"]): x["msg"] for x in b.build(rows)["issues"]}
    assert "疑似填錯" in msgs[("2024/05", "error")]
    assert "複製上一列" in msgs[("2024/08", "error")]
    assert "超過契約 452" in msgs[("2024/10", "error")]
    assert not any(m == "2024/11" for m, _ in msgs)


def fy_sheet(kwh12, fy="FY114", max_kw=None, pen=None, pv=None):
    pad = lambda v: [None] * (12 - len(v)) if len(v) < 12 else []
    rows = [(None, "電號"), (None, None, None, None, None, "使用度數(kWh)", "一月")]
    rows.append((None,) * 5 + (fy, *kwh12, *pad(kwh12)))                       # 度數總表（第 6 欄沒有區塊名，略過）
    rows.append((None,) * 4 + ("最大需量\n(仟瓦)", fy, *(max_kw or [480] * len(kwh12))))
    rows.append((None,) * 4 + ("超約罰款金額\n(仟元)", fy, *(pen or [0] * len(kwh12))))
    rows.append((None,) * 4 + ("流動電度\n(仟度)", fy, *[k / 1000 for k in kwh12]))
    rows.append((None,) * 4 + ("電費\n(仟元)", fy, *[900.0] * len(kwh12)))
    rows.append((None,) * 4 + ("太陽能PV\n(度)", fy, *(pv or [20000] * len(kwh12))))
    return rows


def test_fy_sheet_adds_newer_month_and_cross_checks():
    # FY114 = 2025；南區用電資料到 2025/12，這裡改成只到 2025/11 → 2025/12 由能源統計表補
    rows = [r for r in sheet() if not (r is not b and isinstance(r[0], dt.datetime) and r[0].year == 2025 and r[0].month == 12)]
    kwh = [180000] * 12
    kwh[2] = 170000                                   # 2025/03 不一致
    d = b.build(rows, fy_sheet(kwh, max_kw=[480] * 11 + [500], pv=[(25 + m) * 1000 for m in range(1, 13)]))
    assert d["months"][-1] == "2025/12" and d["fy_months"] == ["2025/12"]
    assert d["kwh"][-1] == 180000 and d["max_kw"][-1] == 500 and d["demand"]["半尖峰"][-1] is None
    assert len(d["months"]) == len(d["kwh"]) == len(d["penalty"]) == len(d["demand"]["離峰"]) == len(d["max_kw"])
    bad = [x for x in d["issues"] if "兩份不一致" in x["msg"]]
    assert [x["month"] for x in bad] == ["2025/03"] and "台電電度" in bad[0]["msg"]


def test_fy_sheet_optional():
    assert b.build(sheet(), None)["fy_months"] == []
