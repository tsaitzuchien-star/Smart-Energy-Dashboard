"""一次性資料抓取：6 月起逐時氣象（近實況、前一天預報、ERA5 再分析），供模型校正用。

容器連不到 Open-Meteo，所以在 GitHub Actions 執行；輸出原始 JSON，不做任何處理。
用法：python tools/fetch_weather_history.py 2026-06-01 2026-10-05 out_dir
"""
import json
import os
import sys
import time

import requests

LAT, LON = "23.936537", "120.697917"
BASE_VARS = ["temperature_2m", "relative_humidity_2m", "dew_point_2m", "apparent_temperature",
             "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
             "shortwave_radiation", "direct_radiation", "diffuse_radiation", "weather_code", "precipitation"]
PREV_VARS = ["temperature_2m", "relative_humidity_2m", "cloud_cover", "cloud_cover_low", "cloud_cover_mid",
             "shortwave_radiation", "precipitation"]


def get(url, name, out):
    """失敗不中斷，狀態寫進 log.txt，方便在無法看 Actions 日誌時排查。"""
    for i in range(3):
        try:
            r = requests.get(url, timeout=180)
            msg = f"{name} {r.status_code} {len(r.content)}"
            if r.status_code == 200:
                with open(os.path.join(out, name + ".json"), "w") as f:
                    f.write(r.text)
                _log(out, msg)
                return
            _log(out, msg + " " + r.text[:300])
        except Exception as e:
            _log(out, f"{name} EXC {type(e).__name__}: {e}"[:400])
        time.sleep(5 * (i + 1))


def _log(out, msg):
    print(msg)
    with open(os.path.join(out, "log.txt"), "a") as f:
        f.write(msg + "\n")


def main(start, end, out):
    os.makedirs(out, exist_ok=True)
    common = f"latitude={LAT}&longitude={LON}&start_date={start}&end_date={end}&timezone=Asia%2FTaipei"
    for model in ["ecmwf_ifs", "best_match"]:
        get(f"https://historical-forecast-api.open-meteo.com/v1/forecast?{common}&hourly={','.join(BASE_VARS)}&models={model}",
            f"histfc_{model}", out)
    prev = ",".join([v for v in PREV_VARS] + [f"{v}_previous_day1" for v in PREV_VARS] + [f"{v}_previous_day2" for v in PREV_VARS])
    for model in ["ecmwf_ifs025", "best_match", "ecmwf_ifs"]:
        get(f"https://previous-runs-api.open-meteo.com/v1/forecast?{common}&hourly={prev}&models={model}",
            f"prevruns_{model}", out)
    get(f"https://archive-api.open-meteo.com/v1/archive?{common}&hourly={','.join(BASE_VARS)}", "era5", out)


if __name__ == "__main__":
    main(*sys.argv[1:4])
