"""研究用：太陽能網站（T-REC 試算表）每組電表每月度數（月初第一筆到下月初第一筆的累計度數差）。"""
import json, os
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
ws = gspread.authorize(Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])).open_by_key(
    "14emzrciRx4VlmAgnwEgxJXioOPpN0zJeHeLkY0am87A").worksheet("中創園區_太陽能發電紀錄")
rows = ws.get_values("A2:F")
first = {}   # (meter, 'YYYY/MM') -> (t, eac) earliest
last = {}
for r in rows:
    if len(r) < 6 or not r[1] or not r[5]:
        continue
    try:
        t = datetime.fromisoformat(r[1].strip().replace("/", "-")); e = float(r[5].replace(",", ""))
    except ValueError:
        continue
    k = (r[0], f"{t.year}/{t.month:02d}")
    if k not in first or t < first[k][0]: first[k] = (t, e)
    if k not in last or t > last[k][0]: last[k] = (t, e)
months = sorted({m for _, m in first})
meters = sorted({n for n, _ in first})
out = {}
for i, m in enumerate(months):
    tot, ok = 0.0, True
    for n in meters:
        a = first.get((n, m)); b = first.get((n, months[i + 1])) if i + 1 < len(months) else last.get((n, m))
        if not a or not b or b[1] < a[1]: ok = False; continue
        tot += b[1] - a[1]
    out[m] = {"kwh": round(tot), "all_meters": ok}
    print(m, round(tot), "" if ok else "（有電表缺或歸零）")
json.dump(out, open("pv_monthly.json", "w"), ensure_ascii=False)
