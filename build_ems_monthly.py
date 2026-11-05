"""中創服務部「園區空間每月用電表」→ monthly.json（ctic-ems-dashboard 的歷史月用電）。

試算表需以「檢視者」共用給服務帳號。每月全額分攤（台電帳單＋太陽能自用）到各空間，
這裡彙總成園區每月用電、電費、空調分攤、假日空調、空壓機、公共／進駐，以及各房間每月度數。
房間代號對照網站 index.html 的 roomData（只公開度數，不公開各房間電費）。
"""
import json
import os
import re
import sys
from collections import defaultdict

BILLING_SHEET_ID = "1QmBGtE2ym9rZC46PPVvtGC6C82-mBw7l3L85pFUaJyY"
BILLING_TAB = "園區全空間每月用電"
SITE_HTML = "https://raw.githubusercontent.com/tsaitzuchien-star/ctic-ems-dashboard/main/index.html"
PARK_COLS = [("用電總量", "kwh"), ("電費", "fee"), ("空調系統分攤", "ac"), ("假日空調申請", "holiday_ac"), ("空壓機用電", "air")]
SOURCE = "園區空間每月用電表（中創服務部，全額分攤，含太陽能自用）"


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return 0.0


def month_key(m):
    y, mo = m.split("/")
    return int(y), int(mo)


def room_id(row, ids):
    """空間編號或房間名稱 → 網站的 room_id；對不上回傳 None。"""
    code = (row.get("空間編號") or "").strip()
    if code in ids:
        return code
    for text in (code, row.get("房間") or ""):
        m = re.match(r"([A-Z]+\d+)", text)
        if m and m.group(1) in ids:
            return m.group(1)
    return None


def build(rows, ids):
    """rows：表頭為鍵的 dict 列表。"""
    rows = [r for r in rows if re.fullmatch(r"\d{4}/\d{1,2}", (r.get("用電年月") or "").strip())]
    months = sorted({r["用電年月"].strip() for r in rows}, key=month_key)
    idx = {m: i for i, m in enumerate(months)}
    park = defaultdict(lambda: [0.0] * len(months))
    rooms = defaultdict(lambda: [0.0] * len(months))
    for r in rows:
        i = idx[r["用電年月"].strip()]
        for col, key in PARK_COLS:
            park[key][i] += num(r.get(col, ""))
        kwh = num(r.get("用電總量", ""))
        park["public" if r.get("進駐分類") == "公共" else "tenant"][i] += kwh
        rid = room_id(r, ids)
        if rid:
            rooms[rid][i] += kwh
    return {
        "source": SOURCE, "months": months,
        "park": {k: [round(v) for v in vs] for k, vs in park.items()},
        "rooms": {k: [round(v, 1) for v in vs] for k, vs in sorted(rooms.items())},
    }


def site_room_ids():
    import requests
    html = requests.get(SITE_HTML, timeout=30).text
    data = re.search(r"const roomData = (\[.*?\]);\n", html).group(1)
    return {r["room_id"] for r in json.loads(data)}


def fetch_rows():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_CREDENTIALS"]), scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly"])
    values = gspread.authorize(creds).open_by_key(BILLING_SHEET_ID).worksheet(BILLING_TAB).get_all_values()
    head = values[0]
    return [dict(zip(head, v)) for v in values[1:]]


def main():
    rows = fetch_rows()
    out = build(rows, site_room_ids())
    total = sum(out["park"]["kwh"])
    mapped = sum(sum(v) for v in out["rooms"].values())
    print(f"{out['months'][0]}–{out['months'][-1]}，{len(out['months'])} 個月，{len(out['rooms'])} 間對上，"
          f"涵蓋 {mapped / total:.1%} 度數")
    if len(out["months"]) < 12 or mapped / total < 0.8:
        sys.exit("資料異常，不發布")
    os.makedirs("out", exist_ok=True)
    with open("out/monthly.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
