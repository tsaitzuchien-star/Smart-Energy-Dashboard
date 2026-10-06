"""每週自動校正：用試算表「實測需量」＋歷史氣象預報重新擬合 demand_model，寫入 model/demand_model.json。

流程
1. 讀「實測需量」工作表（每小時最大 15 分鐘需量；空白＝監控停機，跳過）。
2. 向 Open-Meteo previous-runs API 取同期「前一天發布的預報」（與每天 18:00 實際拿得到的資訊一致）。
3. 逐日滾動驗證：每一天只用它之前的資料訓練再預測，得到不偷看答案的誤差與「最壞情況」分位數。
4. 用全部資料擬合最終係數，連同驗證結果寫入 JSON。

GitHub Actions 每週執行（.github/workflows/calibrate.yml），需要 GOOGLE_CREDENTIALS。
離線重跑：python calibrate.py --actual-csv 實測.csv --wx-json 氣象.json
"""
import argparse
import csv
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta

import demand_model as dm

log = logging.getLogger("calibrate")

ACTUAL_TAB = "實測需量"
SHEET_ID = "1NZ0OPky-I-oWXfFTeVR8qpFT1pFBwQvRoSerJYJJMwY"
WX_MODEL = "ecmwf_ifs"     # 與 weather.py 線上預報同一個模型
_OM_VARS = {"T": "temperature_2m", "RH": "relative_humidity_2m", "CC": "cloud_cover",
            "R": "shortwave_radiation", "P": "precipitation"}
MIN_TRAIN_DAYS = 14        # 滾動驗證前至少要有的訓練天數
EVAL_DAYS = 60             # 分位數與誤差只看最近這麼多天，跟上季節變化
MAX_ACCEPT_MAE = 60.0      # 滾動驗證誤差超過這個值就不更新（資料異常保護）


# ---------------- 資料整理 ----------------

def _date(s):
    s = str(s).strip().split(" ")[0].replace("/", "-")
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


def parse_actual(rows):
    """「實測需量」表（含標題列）→ {(date, hour): kW}；空白跳過。"""
    out = {}
    for r in rows[1:]:
        if len(r) < 3 or str(r[2]).strip() == "":
            continue
        try:
            out[(_date(r[0]), int(str(r[1]).split(":")[0]))] = float(str(r[2]).replace(",", ""))
        except ValueError:
            continue
    return out


def parse_openmeteo(j, suffix="_previous_day1"):
    """Open-Meteo hourly 回應 → {(date, hour): {T, RH, CC, R, P}}。"""
    h = j["hourly"]
    out = {}
    for i, t in enumerate(h["time"]):
        dt = datetime.fromisoformat(t)
        wx = {}
        for f, var in _OM_VARS.items():
            v = h.get(var + suffix, [None] * len(h["time"]))[i]
            if v is not None:
                wx[f] = float(v)
        if len(wx) == len(_OM_VARS):
            out[(dt.date(), dt.hour)] = wx
    return out


def mag_off(d, h, is_holiday):
    """該小時磁浮是否全關：夏月平日尖峰、空調時段內（16、17 點）。"""
    from forecast import tou_period
    return not is_holiday and h < 18 and tou_period(d, h) == "尖峰"


def build_rows(actual, wx, is_holiday):
    rows = []
    for (d, h), y in actual.items():
        if h in dm.HOURS and (d, h) in wx:
            hol = is_holiday(d)
            rows.append({"date": d, "hour": h, "daytype": dm.day_type(hol), "y": y,
                         "mag_off": mag_off(d, h, hol), **wx[(d, h)]})
    return rows


# ---------------- 驗證與擬合 ----------------

def rolling_eval(rows, halflife=21.0, alpha=3.0):
    """逐日：只用前幾天訓練、預測當天。回傳每列加上 pred 的 list。"""
    days = sorted({r["date"] for r in rows})
    out = []
    for i, d in enumerate(days):
        train = [r for r in rows if r["date"] < d]
        if len({r["date"] for r in train}) < MIN_TRAIN_DAYS:
            continue
        m = dm.fit(train, d - timedelta(days=1), halflife, alpha)
        for r in rows:
            if r["date"] == d:
                p = dm.predict_hour(m, r["daytype"], r["hour"], r, r["mag_off"])
                if p is not None:
                    out.append({**r, "pred": p})
    return out


def summarize(evals):
    """誤差統計與各時段「實測最高 − 預測最高」分位數。"""
    metrics, resid_q = {}, {}
    for dt in ("work", "off"):
        sub = [e for e in evals if e["daytype"] == dt]
        if not sub:
            continue
        err = [e["y"] - e["pred"] for e in sub]
        metrics[dt] = {"hours": len(sub), "days": len({e["date"] for e in sub}),
                       "mae": round(sum(abs(x) for x in err) / len(err), 1),
                       "bias": round(sum(err) / len(err), 1)}
        resid_q[dt] = {}
        for blk, hrs in dm.BLOCKS.items():
            by_day = {}
            for e in sub:
                if e["hour"] in hrs:
                    a, p = by_day.get(e["date"], (-1e9, -1e9))
                    by_day[e["date"]] = (max(a, e["y"]), max(p, e["pred"]))
            res = [a - p for a, p in by_day.values()]
            if len(res) >= 10:
                resid_q[dt][blk] = {"q90": round(dm.quantile(res, 0.9), 1), "q95": round(dm.quantile(res, 0.95), 1),
                                    "peak_mae": round(sum(abs(x) for x in res) / len(res), 1), "days": len(res)}
    return metrics, resid_q


def calibrate(rows, halflife=21.0, alpha=3.0):
    last = max(r["date"] for r in rows)
    evals = rolling_eval(rows, halflife, alpha)
    recent = [e for e in evals if e["date"] > last - timedelta(days=EVAL_DAYS)]
    metrics, resid_q = summarize(recent)
    model = dm.fit(rows, last, halflife, alpha)
    model.update({"trained_through": last.isoformat(), "first_date": min(r["date"] for r in rows).isoformat(),
                  "weather": f"Open-Meteo {WX_MODEL} 前一天預報", "metrics": metrics, "resid_q": resid_q,
                  "summer_only": all(r["date"].month in (6, 7, 8, 9) for r in rows),
                  "eval_window_days": EVAL_DAYS, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
    return model


# ---------------- 外部 I/O ----------------

def fetch_weather(start, end):
    import requests
    from weather import LAT, LON
    vars_ = ",".join(v + "_previous_day1" for v in _OM_VARS.values())
    url = (f"https://previous-runs-api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
           f"&start_date={start}&end_date={end}&hourly={vars_}&timezone=Asia%2FTaipei&models={WX_MODEL}")
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    return r.json()


def read_actual_sheet():
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    book = gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes)).open_by_key(SHEET_ID)
    return book.worksheet(ACTUAL_TAB).get_all_values()


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--actual-csv", help="離線：實測需量 CSV（日期,時間,需量）")
    ap.add_argument("--wx-json", help="離線：Open-Meteo previous-runs 回應 JSON")
    ap.add_argument("--out", default=dm.DEFAULT_PATH)
    a = ap.parse_args(argv)

    if a.actual_csv:
        with open(a.actual_csv, encoding="utf-8-sig") as f:
            actual = parse_actual(list(csv.reader(f)))
    else:
        actual = parse_actual(read_actual_sheet())
    if not actual:
        log.error("沒有實測資料")
        return 1
    start, end = min(d for d, _ in actual), max(d for d, _ in actual)
    if a.wx_json:
        with open(a.wx_json, encoding="utf-8") as f:
            wx = parse_openmeteo(json.load(f))
    else:
        wx = parse_openmeteo(fetch_weather(start, end))

    from calendar_tw import load_calendar
    cal = load_calendar({start.year, end.year})
    rows = build_rows(actual, wx, cal.is_holiday)
    log.info("實測 %d 小時，可用 %d 列（%s → %s）", len(actual), len(rows), start, end)
    model = calibrate(rows)
    work = model["metrics"].get("work", {})
    if model["n_days"].get("work", 0) < MIN_TRAIN_DAYS or not work or work["mae"] > MAX_ACCEPT_MAE:
        log.warning("新模型不合格（上班日 %s 天、誤差 %s kW），保留原模型", model["n_days"].get("work"), work.get("mae"))
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=1)
    log.info("模型更新至 %s：%s", model["trained_through"], json.dumps(model["metrics"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
