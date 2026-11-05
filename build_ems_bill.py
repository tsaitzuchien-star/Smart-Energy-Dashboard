"""南區服務組「南區用電資料.xlsx」中創部分 → bill.json（台電帳單月資料＋填寫檢查）。

來源：中創同仁每月填寫的帳單資料，Cliff 的電腦每天把網路磁碟上的檔案複製到
Google 雲端硬碟「BMS自動匯出」（C:\\BMS_Export\\copy_bill_to_drive.ps1），服務帳號讀取。
數值單位：度數、電費為「千」；用電-尖峰等四欄是各時段最高需量（kW）。
帳單月份大約落後實際用電一個月（例：2026/9 帳單＝8 月用電）。

另一份「中創圈區能源資訊統計比較_FY???.xlsx」（AS-0H300\05水電設施\能源統計，同一支腳本複製）
的「電力」分頁通常早一個月填好：南區用電資料還沒有的月份用它補上，重疊的月份互相對帳。
"""
import re
import io
import json
import os
import sys
from statistics import median

BILL_FILE = "南區用電資料.xlsx"
FY_FILE = "能源資訊統計比較_FY"
PV_SHEET_ID = "14emzrciRx4VlmAgnwEgxJXioOPpN0zJeHeLkY0am87A"   # 自建太陽能網站的 T-REC 紀錄（四組電表累計度數）
PV_TAB = "中創園區_太陽能發電紀錄"
PV_METERS = 4
PV_MAX_KWH_15 = 40.0      # 單組每 15 分鐘最多 40 度；超過或負值＝換表、歸零，不計   # 檔名包含這段（實際檔名是「中創圈區…」）
FY_BLOCKS = {"最大需量": ("max_kw", 1), "超約罰款": ("penalty", 1000), "流動電度": ("kwh", 1000),
             "電費": ("fee", 1000), "太陽能": ("pv", 1)}
SITE = "中創"
CHECK_FROM = "2024/01"   # 之前欄位格式不同（2021 只有一欄需量）、契約也可能不同，只檢查這之後的月份
CONTRACT = {"尖峰": 452.0, "半尖峰": 516.0, "週六半尖峰": 616.0, "離峰": 616.0}
COLS = {  # 表頭 → 欄位
    "台電電度": "kwh", "台電電費": "fee", "太陽能用電": "pv", "太陽能電費": "pv_fee",
    "用電-尖峰": "d_peak", "用電-半尖峰": "d_semi", "用電-周六半尖峰": "d_sat", "用電-離峰": "d_off",
    "罰款-尖峰": "p_peak", "罰款-半尖峰": "p_semi", "罰款-周六半尖峰": "p_sat", "罰款-離峰": "p_off",
    "空調用電": "ac",
}
PERIODS = [("尖峰", "d_peak", "p_peak"), ("半尖峰", "d_semi", "p_semi"),
           ("週六半尖峰", "d_sat", "p_sat"), ("離峰", "d_off", "p_off")]
REQUIRED = ["kwh", "fee", "pv", "pv_fee", "d_semi", "d_sat", "d_off"]
NAMES = {"kwh": "台電電度", "fee": "台電電費", "pv": "太陽能用電", "pv_fee": "太陽能電費",
         "d_semi": "半尖峰需量", "d_sat": "週六半尖峰需量", "d_off": "離峰需量", "d_peak": "尖峰需量", "ac": "空調用電"}


def num(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse(rows):
    """rows：第一列是表頭的 list of tuples（openpyxl values_only, data_only）。→ 依月份排序的 dict 列表。"""
    head = [str(h).strip() if h is not None else "" for h in rows[0]]
    ix = {h: i for i, h in enumerate(head)}
    out = []
    for r in rows[1:]:
        if len(r) <= ix["院區"] or r[ix["院區"]] != SITE or not hasattr(r[ix["日期"]], "year"):
            continue
        rec = {"month": f"{r[ix['日期']].year}/{r[ix['日期']].month:02d}"}
        for h, k in COLS.items():
            rec[k] = num(r[ix[h]]) if h in ix and ix[h] < len(r) else None
        out.append(rec)
    out.sort(key=lambda x: x["month"])
    while out and out[-1]["kwh"] is None:      # 還沒填的月份
        out.pop()
    return out


def check(recs):
    """同仁填寫值的檢查 → [{month, level, msg}]。level：error 必須改、warn 請確認。"""
    issues = []

    def add(m, level, msg):
        issues.append({"month": m, "level": level, "msg": msg})

    for i, r in enumerate(recs):
        m = r["month"]
        if m < CHECK_FROM:
            continue
        for k in REQUIRED:
            if r[k] is None:
                add(m, "warn", f"{NAMES[k]}空白")
            elif r[k] == 0 and k.startswith("d_"):
                add(m, "warn", f"{NAMES[k]}填 0")
        # 單價跳動：和前後各 6 個月的中位數比
        if r["kwh"] and r["fee"]:
            near = [x["fee"] / x["kwh"] for x in recs[max(0, i - 6):i + 7] if x is not r and x["kwh"] and x["fee"]]
            if near:
                med = median(near)
                p = r["fee"] / r["kwh"]
                if p < med * 0.7 or p > med * 1.4:
                    add(m, "error", f"每度 {p:.2f} 元，前後月份約 {med:.2f} 元，電費或度數疑似填錯")
        # 太陽能整列和上個月一樣
        if i and r["pv"] is not None and r["pv"] == recs[i - 1]["pv"] and r["pv_fee"] == recs[i - 1]["pv_fee"]:
            add(m, "error", "太陽能度數與電費和上個月完全相同，疑似複製上一列")
        # 超約：需量超過契約，罰款卻沒填
        for name, dk, pk in PERIODS:
            d, p = r[dk], r[pk]
            if d is not None and d > CONTRACT[name] and not p:
                add(m, "error", f"{name}需量 {d:.0f} kW 超過契約 {CONTRACT[name]:.0f} kW，但罰款欄{'空白' if p is None else '是 0'}，請對帳單")
            if p and (d is None or d <= CONTRACT[name]):
                add(m, "warn", f"{name}有罰款 {p * 1000:,.0f} 元，但需量 {d if d is not None else '空白'} 沒有超過契約")
        # 度數跳動：和去年同月差超過 30%
        prev = next((x for x in recs if x["month"] == f"{int(m[:4]) - 1}{m[4:]}"), None)
        if prev and prev["kwh"] and r["kwh"] and abs(r["kwh"] / prev["kwh"] - 1) > 0.3:
            add(m, "warn", f"台電電度 {r['kwh'] * 1000:,.0f} 度，和去年同月 {prev['kwh'] * 1000:,.0f} 度差超過 30%")
    return issues


def parse_fy(rows):
    """能源統計表「電力」分頁 → {"2026/11": {kwh, fee, pv, max_kw, penalty}}（度、元、kW）。
    區塊：第 5 欄是名稱（最大需量、超約罰款、流動電度、電費、太陽能PV），第 6 欄 FY115，第 7–18 欄 1–12 月。"""
    out, block = {}, None
    for r in rows:
        r = list(r) + [None] * 18
        if isinstance(r[4], str) and r[4].strip():
            block = next((v for k, v in FY_BLOCKS.items() if r[4].strip().startswith(k)), None)
        fy = re.fullmatch(r"FY(\d{3})", str(r[5]).strip()) if r[5] is not None else None
        if not (block and fy):
            continue
        key, scale = block
        for m in range(12):
            v = num(r[6 + m])
            if v is not None:
                out.setdefault(f"{int(fy.group(1)) + 1911}/{m + 1:02d}", {})[key] = v * scale
    return out


def cross_check(recs, fy):
    """兩份表重疊月份互相對帳 → issues。"""
    issues = []
    for r in recs:
        f = fy.get(r["month"])
        if r["month"] < CHECK_FROM or not f:
            continue
        mine = {"kwh": r["kwh"] and r["kwh"] * 1000, "fee": r["fee"] and r["fee"] * 1000, "pv": r["pv"] and r["pv"] * 1000,
                "max_kw": max([r[d] for _, d, _ in PERIODS if r[d] is not None], default=None),
                "penalty": sum(r[p] or 0 for _, _, p in PERIODS) * 1000}
        for k, name, tol in [("kwh", "台電電度", 1), ("fee", "台電電費", 1),
                             ("max_kw", "最高需量", 0.5), ("penalty", "超約罰款", 1)]:
            a, b = mine[k], f.get(k)
            if a is not None and b is not None and abs(a - b) > tol:
                issues.append({"month": r["month"], "level": "warn",
                               "msg": f"{name}：南區用電資料 {a:,.0f}，能源統計表 {b:,.0f}，兩份不一致"})
    return issues


def pv_site_monthly(rows):
    """太陽能網站（T-REC 試算表：系統名稱、紀錄時間、…、累計度數）→ {"2026/10": 度}，只收資料完整的整月。
    每組電表相鄰兩筆累計度數相減（中間缺資料也照比例上限接受），歸零或跳表的那段不計。"""
    import calendar
    from datetime import datetime, timedelta
    series = {}
    for r in rows:
        if len(r) < 6 or not str(r[1]).strip():
            continue
        try:
            t = datetime.fromisoformat(str(r[1]).strip().replace("/", "-"))
            e = float(str(r[5]).replace(",", ""))
        except ValueError:
            continue
        series.setdefault(r[0], {})[t] = e
    if len(series) < PV_METERS:
        return {}
    kwh, days = {}, {}
    last_t = max(max(v) for v in series.values())
    for name, ser in series.items():
        ts = sorted(ser)
        for a, b in zip(ts, ts[1:]):
            de, steps = ser[b] - ser[a], (b - a) / timedelta(minutes=15)
            m = f"{b.year}/{b.month:02d}"
            days.setdefault((name, m), set()).add(b.day)
            if 0 <= de <= PV_MAX_KWH_15 * max(steps, 1):
                kwh[m] = kwh.get(m, 0.0) + de
    out = {}
    for m, v in kwh.items():
        y, mo = int(m[:4]), int(m[5:])
        ndays = calendar.monthrange(y, mo)[1]
        month_over = (last_t.year, last_t.month) > (y, mo)
        if month_over and all(len(days.get((n, m), ())) >= ndays * 0.9 for n in series):
            out[m] = round(v)
    return out


def _prev_month(m):
    y, mo = int(m[:4]), int(m[5:])
    return f"{y - 1}/12" if mo == 1 else f"{y}/{mo - 1:02d}"


def build(rows, fy_rows=None, pv_rows=None):
    recs = parse(rows)
    if len(recs) < 12:
        raise SystemExit(f"中創只有 {len(recs)} 個月，不發布")
    col = lambda k, scale=1000: [round(r[k] * scale) if r[k] is not None else None for r in recs]
    penalty = [round(sum(r[p] or 0 for _, _, p in PERIODS) * 1000) for r in recs]
    data = {
        "source": "南區用電資料.xlsx（南區服務組，中創台電帳單；帳單月份約落後用電一個月）",
        "months": [r["month"] for r in recs],
        "kwh": col("kwh"), "fee": col("fee"), "pv": col("pv"), "pv_fee": col("pv_fee"), "ac": col("ac"),
        "demand": {name: [r[dk] for r in recs] for name, dk, _ in PERIODS},
        "max_kw": [max([r[d] for _, d, _ in PERIODS if r[d] is not None], default=None) for r in recs],
        "contract": CONTRACT,
        "penalty": penalty,
        "fy_months": [],
        "issues": check(recs),
    }
    if fy_rows:
        fy = parse_fy(fy_rows)
        data["issues"] += cross_check(recs, fy)
        data["issues"].sort(key=lambda x: x["month"])
        for m in sorted(k for k in fy if k > data["months"][-1] and fy[k].get("kwh")):
            f = fy[m]
            data["months"].append(m)
            data["fy_months"].append(m)
            for k in ("kwh", "fee", "pv"):
                data[k].append(round(f[k]) if f.get(k) is not None else None)
            data["pv_fee"].append(None)
            data["ac"].append(None)
            for name in data["demand"]:
                data["demand"][name].append(None)
            data["max_kw"].append(f.get("max_kw"))
            data["penalty"].append(round(f.get("penalty") or 0))
        if data["fy_months"]:
            data["source"] += f"；{'、'.join(data['fy_months'])} 先取自能源統計表（只有總用電、電費、最高需量）"
    # 太陽能以自建太陽能網站為準：帳單月份 M 的太陽能＝網站上 M−1 整月的度數（2026/4 起同仁也是照抄這個數）
    site = pv_site_monthly(pv_rows) if pv_rows else {}
    data["pv_site"] = []
    for i, m in enumerate(data["months"]):
        v = site.get(_prev_month(m))
        if v is not None:
            data["pv"][i] = v
            data["pv_site"].append(m)
    if data["pv_site"]:
        data["source"] += f"；太陽能度數以太陽能網站為準（{data['pv_site'][0]} 起）"
    return data


def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_CREDENTIALS"]),
                                                  scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return AuthorizedSession(creds)


def _download(s, q, label):
    """雲端硬碟搜尋 → 最新修改的那份 xlsx（跳過「複本」）→ openpyxl workbook。"""
    import openpyxl
    r = s.get("https://www.googleapis.com/drive/v3/files",
              params={"q": f"{q} and trashed = false", "fields": "files(id,name,modifiedTime)",
                      "orderBy": "modifiedTime desc", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    r.raise_for_status()
    files = [f for f in r.json().get("files", []) if f["name"].endswith(".xlsx") and not f["name"].startswith("複本")]
    if not files:
        return None
    c = s.get(f"https://www.googleapis.com/drive/v3/files/{files[0]['id']}", params={"alt": "media"})
    c.raise_for_status()
    print(f"{files[0]['name']} 修改時間 {files[0]['modifiedTime']}")
    return openpyxl.load_workbook(io.BytesIO(c.content), data_only=True, read_only=True)


def fetch_rows():
    """南區用電資料.xlsx 第一個工作表；能源統計表「電力」分頁（抓不到就 None，不影響帳單）。"""
    s = _session()
    wb = _download(s, f"name = '{BILL_FILE}'", BILL_FILE)
    if wb is None:
        raise SystemExit(f"雲端硬碟找不到 {BILL_FILE}")
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    fy_rows = None
    try:
        fwb = _download(s, f"name contains '{FY_FILE}'", FY_FILE)
        if fwb is not None and "電力" in fwb.sheetnames:
            fy_rows = list(fwb["電力"].iter_rows(values_only=True))
        else:
            print("找不到能源統計表，略過")
    except Exception as e:  # 能源統計表只是補充，出錯不擋帳單
        print(f"能源統計表讀取失敗，略過：{e}")
    pv_rows = None
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_CREDENTIALS"]),
                                                      scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        pv_rows = gspread.authorize(creds).open_by_key(PV_SHEET_ID).worksheet(PV_TAB).get_values("A2:F")
        print(f"太陽能網站 {len(pv_rows)} 筆")
    except Exception as e:  # 太陽能網站讀不到時沿用帳單上填的數字
        print(f"太陽能網站讀取失敗，沿用帳單數字：{e}")
    return rows, fy_rows, pv_rows


def main():
    data = build(*fetch_rows())
    os.makedirs("out", exist_ok=True)
    with open("out/bill.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"bill.json：{data['months'][0]}–{data['months'][-1]}，{len(data['issues'])} 項待確認")
    for x in data["issues"]:
        print(f"  {x['month']} [{x['level']}] {x['msg']}")


if __name__ == "__main__":
    sys.exit(main())
