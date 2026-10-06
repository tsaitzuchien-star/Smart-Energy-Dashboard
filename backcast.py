"""回測：用歷史氣象重算過去每天的「明日預測」，與實測需量比對用。

每個預測日 D，假設在 D 前一天 18:00 執行（與 auto_log.py 相同時間、相同預設參數），
氣象改用 Open-Meteo 歷史預報（historical-forecast API，各次預報的前幾小時拼接而成，接近實況）。
因此誤差主要反映負載模型本身，不含氣象預報誤差。

用法：python backcast.py 2026-06-08 2026-09-30 [輸出 CSV 路徑]
"""
import csv
import logging
import sys
from datetime import date, datetime, timedelta

import requests

from calendar_tw import load_calendar
from forecast import ForecastInputs, compute_forecast, off_days_before
from weather import LAT, LON, TW_TZ, apply_open_meteo

log = logging.getLogger("backcast")

HOURLY_VARS = "temperature_2m,relative_humidity_2m,precipitation,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,weather_code,shortwave_radiation"
MODELS = ["ecmwf_ifs", "ecmwf_ifs025", "best_match"]   # 依序嘗試，記錄實際使用的模型
HEADERS = ["預測日期", "時間", "台電時段", "契約上限(kW)", "預測需量(kW)", "當日預測最高(kW)", "當日最危險時段", "氣象模型"]


def fetch_history(start, end, session=None):
    """抓 start 前一天到 end 的逐時歷史預報；回傳 (Open-Meteo hourly dict, 模型名)。"""
    session = session or requests.Session()
    for model in MODELS:
        url = ("https://historical-forecast-api.open-meteo.com/v1/forecast"
               f"?latitude={LAT}&longitude={LON}&start_date={start - timedelta(days=1)}&end_date={end}"
               f"&hourly={HOURLY_VARS}&timezone=Asia%2FTaipei&models={model}")
        r = session.get(url, timeout=60)
        if r.status_code == 200:
            hourly = r.json()["hourly"]
            if any(v is not None for v in hourly["temperature_2m"]):
                return hourly, model
        log.warning("模型 %s 無資料（HTTP %s）", model, r.status_code)
    raise RuntimeError("Open-Meteo 歷史預報抓不到資料")


def weather_at(hourly, now):
    """把歷史序列包成 fetch_smart_weather() 的格式；current 取 now 那一小時。"""
    key = now.strftime("%Y-%m-%dT%H:00")
    i = hourly["time"].index(key)
    current = {k: hourly[k][i] for k in hourly if k != "time"}
    res = {
        "fetch_time": now.strftime("%Y-%m-%d %H:%M:%S"), "status_code": 0, "source": "盲估",
        "wx": "未知", "cloud": 0, "rad": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_rad": 400,
        "cloud_low": 0, "cloud_mid": 0, "cloud_high": 0, "today_hourly": {}, "hourly": {},
        "all_temps_today": {}, "all_temps_tmr": {}, "temp_is_calibrated": False,
    }
    return apply_open_meteo(res, {"current": current, "hourly": hourly}, now)


def backcast_rows(start, end, hourly, model, cal, inp=None):
    inp = inp or ForecastInputs()
    rows, d = [], start
    while d <= end:
        now = datetime(d.year, d.month, d.day, 18, 0, tzinfo=TW_TZ) - timedelta(days=1)
        fc = compute_forecast(weather_at(hourly, now), inp, now, cal.is_holiday(now.date()), cal.is_holiday(d),
                              prev_off_days=off_days_before(d, cal.is_holiday))
        for h, c in sorted(fc["calc_tmr"].items()):
            rows.append([d.isoformat(), h, c["period"], c["current_limit"], round(c["h_net"], 1),
                         round(fc["max_net_grid_demand"], 1), fc["worst_hour"], model])
        d += timedelta(days=1)
    return rows


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    start, end = date.fromisoformat(argv[1]), date.fromisoformat(argv[2])
    out = argv[3] if len(argv) > 3 else "backcast.csv"
    hourly, model = fetch_history(start, end)
    cal = load_calendar({start.year, end.year, (start - timedelta(days=1)).year})
    rows = backcast_rows(start, end, hourly, model, cal)
    with open(out, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([HEADERS] + rows)
    log.info("模型 %s，%d 天，%d 列 → %s", model, (end - start).days + 1, len(rows), out)


if __name__ == "__main__":
    main(sys.argv)
