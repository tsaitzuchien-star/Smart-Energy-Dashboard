"""南區服務組「南區用電資料.xlsx」中創部分 → bill.json（台電帳單月資料＋填寫檢查）。

來源：中創同仁每月填寫的帳單資料，Cliff 的電腦每天把網路磁碟上的檔案複製到
Google 雲端硬碟「BMS自動匯出」（C:\\BMS_Export\\copy_bill_to_drive.ps1），服務帳號讀取。
數值單位：度數、電費為「千」；用電-尖峰等四欄是各時段最高需量（kW）。
帳單月份大約落後實際用電一個月（例：2026/9 帳單＝8 月用電）。
"""
import io
import json
import os
import sys
from statistics import median

BILL_FILE = "南區用電資料.xlsx"
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


def build(rows):
    recs = parse(rows)
    if len(recs) < 12:
        raise SystemExit(f"中創只有 {len(recs)} 個月，不發布")
    col = lambda k, scale=1000: [round(r[k] * scale) if r[k] is not None else None for r in recs]
    penalty = [round(sum(r[p] or 0 for _, _, p in PERIODS) * 1000) for r in recs]
    return {
        "source": "南區用電資料.xlsx（南區服務組，中創台電帳單；帳單月份約落後用電一個月）",
        "months": [r["month"] for r in recs],
        "kwh": col("kwh"), "fee": col("fee"), "pv": col("pv"), "pv_fee": col("pv_fee"), "ac": col("ac"),
        "demand": {name: [r[dk] for r in recs] for name, dk, _ in PERIODS},
        "contract": CONTRACT,
        "penalty": penalty,
        "issues": check(recs),
    }


def fetch_rows():
    """從雲端硬碟抓 南區用電資料.xlsx → 第一個工作表的所有列。"""
    import openpyxl
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_CREDENTIALS"]),
                                                  scopes=["https://www.googleapis.com/auth/drive.readonly"])
    s = AuthorizedSession(creds)
    r = s.get("https://www.googleapis.com/drive/v3/files",
              params={"q": f"name = '{BILL_FILE}' and trashed = false", "fields": "files(id,modifiedTime)",
                      "orderBy": "modifiedTime desc", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    r.raise_for_status()
    files = r.json().get("files", [])
    if not files:
        raise SystemExit(f"雲端硬碟找不到 {BILL_FILE}")
    c = s.get(f"https://www.googleapis.com/drive/v3/files/{files[0]['id']}", params={"alt": "media"})
    c.raise_for_status()
    wb = openpyxl.load_workbook(io.BytesIO(c.content), data_only=True, read_only=True)
    print(f"{BILL_FILE} 修改時間 {files[0]['modifiedTime']}")
    return list(wb.worksheets[0].iter_rows(values_only=True))


def main():
    data = build(fetch_rows())
    os.makedirs("out", exist_ok=True)
    with open("out/bill.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"bill.json：{data['months'][0]}–{data['months'][-1]}，{len(data['issues'])} 項待確認")
    for x in data["issues"]:
        print(f"  {x['month']} [{x['level']}] {x['msg']}")


if __name__ == "__main__":
    sys.exit(main())
