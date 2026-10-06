"""氣象抓取模組（不依賴 Streamlit，app.py 與 auto_log.py 共用）。

資料來源：Open-Meteo ECMWF（主）＋ Visual Crossing（備援／高溫校正，需 VC_API_KEY）。
邏輯由原 app.py 的 get_smart_weather() 原樣搬出，僅改為可傳入金鑰與時間。
"""
import logging
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

TW_TZ = timezone(timedelta(hours=8))
LAT, LON = "23.936537", "120.697917"
TARGET_HOURS = ["08:00", "10:00", "12:00", "14:00", "16:00", "18:00"]


def wmo_to_text(wmo):
    if wmo == 0: return "晴朗"
    elif wmo in [1, 2]: return "多雲"
    elif wmo == 3: return "陰天"
    elif 50 <= wmo <= 69: return "降雨"
    elif 80 <= wmo <= 82: return "陣雨"
    elif wmo >= 95: return "雷陣雨"
    return "未知"

def translate_wx(wx_en):
    wx_en = wx_en.lower()
    if 'clear' in wx_en: return "晴朗"
    if 'partially cloudy' in wx_en: return "多雲"
    if 'cloudy' in wx_en or 'overcast' in wx_en: return "陰天"
    if 'rain' in wx_en: return "降雨"
    return wx_en.capitalize()


def apply_open_meteo(res, r, now, target_hours=TARGET_HOURS):
    """把 Open-Meteo 回應（含 current 與 hourly）填進氣象 dict；回測腳本也用這個，確保解析方式與線上一致。"""
    today_prefix = now.strftime("%Y-%m-%d")
    tmr_prefix = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    res["source"] = "ECMWF"
    res["status_code"] = 1
    res["wx"] = wmo_to_text(r['current']['weather_code'])
    res["cloud"] = r['current']['cloud_cover']
    res["cloud_low"] = r['current']['cloud_cover_low']
    res["cloud_mid"] = r['current']['cloud_cover_mid']
    res["cloud_high"] = r['current']['cloud_cover_high']
    res["rad"] = r['current']['shortwave_radiation']
    res["temp"] = r['current']['temperature_2m']
    times_list = r['hourly']['time']
    for i, t in enumerate(times_list):
        if t.startswith(today_prefix): res["all_temps_today"][t.split("T")[1]] = r['hourly']['temperature_2m'][i]
        elif t.startswith(tmr_prefix): res["all_temps_tmr"][t.split("T")[1]] = r['hourly']['temperature_2m'][i]
    for hour in target_hours:
        t_td = f"{today_prefix}T{hour}"
        if t_td in times_list:
            idx = times_list.index(t_td)
            res["today_hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "rad": r['hourly']['shortwave_radiation'][idx], "c_low": r['hourly']['cloud_cover_low'][idx], "c_mid": r['hourly']['cloud_cover_mid'][idx], "c_high": r['hourly']['cloud_cover_high'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
        t_tm = f"{tmr_prefix}T{hour}"
        if t_tm in times_list:
            idx = times_list.index(t_tm)
            res["hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "rad": r['hourly']['shortwave_radiation'][idx], "c_low": r['hourly']['cloud_cover_low'][idx], "c_mid": r['hourly']['cloud_cover_mid'][idx], "c_high": r['hourly']['cloud_cover_high'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
    try: res["tmr_temp"] = max([r['hourly']['temperature_2m'][times_list.index(f"{tmr_prefix}T{h}:00")] for h in range(12, 16)])
    except: res["tmr_temp"] = res["hourly"].get("12:00", {}).get("temp", 28.0)
    try: res["tmr_rad"] = int(sum([r['hourly']['shortwave_radiation'][times_list.index(f"{tmr_prefix}T{h:02d}:00")] for h in range(8, 17, 2)]) / len(range(8, 17, 2)))
    except: res["tmr_rad"] = res["rad"]
    return res


def fetch_smart_weather(vc_key=None, now=None, session=None):
    """回傳氣象 dict。status_code: 1=ECMWF, 2=VC 備援, 0=全部斷線(盲估)。"""
    now = now or datetime.now(TW_TZ)
    fetch_time = now.strftime('%Y-%m-%d %H:%M:%S')
    res = {
        "fetch_time": fetch_time, "status_code": 0, "source": "盲估",
        "wx": "未知", "cloud": 0, "rad": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_rad": 400, 
        "cloud_low": 0, "cloud_mid": 0, "cloud_high": 0, "today_hourly": {}, "hourly": {},
        "all_temps_today": {}, "all_temps_tmr": {}, "temp_is_calibrated": False
    }
    today_prefix = now.strftime("%Y-%m-%d")
    tmr_prefix = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    lat, lon = LAT, LON
    target_hours = TARGET_HOURS

    if session is None:
        session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.5)
        session.mount("https://", HTTPAdapter(max_retries=retry))

    ecmwf_parsed = None
    vc_parsed = None

    try:
        om_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,weather_code,shortwave_radiation&hourly=temperature_2m,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,weather_code,shortwave_radiation&timezone=Asia%2FTaipei&models=ecmwf_ifs"
        r_om = session.get(om_url, timeout=5)
        if r_om.status_code == 200:
            apply_open_meteo(res, r_om.json(), now, target_hours)
            ecmwf_parsed = True
    except Exception as e: log.warning("ECMWF 抓取失敗: %s", e)

    if vc_key:
        try:
            vc_url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{lat},{lon}?unitGroup=metric&key={vc_key}&contentType=json&elements=datetime,temp,cloudcover,solarradiation,conditions,tempmax"
            r_vc = session.get(vc_url, timeout=8)
            if r_vc.status_code == 200:
                r = r_vc.json()
                vc_parsed = { "current_temp": r['currentConditions'].get('temp', 25.0), "tmr_temp": r['days'][1].get('tempmax', 28.0), "today_hourly": {}, "hourly": {} }
                today_hours, tmr_hours = r['days'][0]['hours'], r['days'][1]['hours']
                for hr_data in today_hours: res["all_temps_today"][hr_data['datetime'][:5]] = hr_data.get('temp', 25.0)
                for hr_data in tmr_hours: res["all_temps_tmr"][hr_data['datetime'][:5]] = hr_data.get('temp', 25.0)
                for h in target_hours:
                    vc_time = h + ":00"
                    for hr_data in today_hours:
                        if hr_data['datetime'] == vc_time: vc_parsed["today_hourly"][h] = hr_data.get('temp', 25.0)
                    for hr_data in tmr_hours:
                        if hr_data['datetime'] == vc_time: vc_parsed["hourly"][h] = hr_data.get('temp', 25.0)
                if not ecmwf_parsed:
                    res["source"] = "VC"
                    res["status_code"] = 2
                    curr = r['currentConditions']
                    res["wx"] = translate_wx(curr.get('conditions', '未知'))
                    res["cloud"] = curr.get('cloudcover', 0)
                    res["cloud_low"] = curr.get('cloudcover', 0) 
                    res["rad"] = curr.get('solarradiation', 0)
                    res["temp"] = vc_parsed["current_temp"]
                    res["tmr_temp"] = vc_parsed["tmr_temp"]
                    for h in target_hours:
                        vc_time = h + ":00"
                        for hr_data in today_hours:
                            if hr_data['datetime'] == vc_time: res["today_hourly"][h] = {"temp": hr_data.get('temp', 25.0), "rad": hr_data.get('solarradiation', 0), "c_low": hr_data.get('cloudcover', 0), "c_mid": 0, "c_high": 0, "wx": translate_wx(hr_data.get('conditions', ''))}
                        for hr_data in tmr_hours:
                            if hr_data['datetime'] == vc_time: res["hourly"][h] = {"temp": hr_data.get('temp', 25.0), "rad": hr_data.get('solarradiation', 0), "c_low": hr_data.get('cloudcover', 0), "c_mid": 0, "c_high": 0, "wx": translate_wx(hr_data.get('conditions', ''))}
                    tmr_rads = [res["hourly"][h]["rad"] for h in res["hourly"] if "rad" in res["hourly"][h]]
                    if tmr_rads: res["tmr_rad"] = sum(tmr_rads) / len(tmr_rads)
        except Exception as e: log.warning("VC 抓取失敗: %s", e)

    if ecmwf_parsed and vc_parsed:
        if vc_parsed["current_temp"] > res["temp"]:
            res["temp"] = vc_parsed["current_temp"]
            res["temp_is_calibrated"] = True
        if vc_parsed["tmr_temp"] > res["tmr_temp"]: res["tmr_temp"] = vc_parsed["tmr_temp"]
        for h in target_hours:
            if h in res["today_hourly"] and h in vc_parsed["today_hourly"]:
                if vc_parsed["today_hourly"][h] > res["today_hourly"][h]["temp"]: res["today_hourly"][h]["temp"] = vc_parsed["today_hourly"][h]
            if h in res["hourly"] and h in vc_parsed["hourly"]:
                if vc_parsed["hourly"][h] > res["hourly"][h]["temp"]: res["hourly"][h]["temp"] = vc_parsed["hourly"][h]

    return res
