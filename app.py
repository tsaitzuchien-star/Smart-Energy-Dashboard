import streamlit as st
import requests
import urllib3
from datetime import datetime, timedelta, timezone

# 關閉不安全的請求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
TW_TZ = timezone(timedelta(hours=8))

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="中創園區空調聯防戰情室 V3.1 (雙核心備援)", page_icon="❄️", layout="wide")

st.markdown("""
    <style>
    .ice-card { background-color: white; border-radius: 15px; text-align: center; box-shadow: 2px 2px 10px rgba(0,0,0,0.05); display: flex; flex-direction: column; justify-content: center; min-height: 320px; }
    .ice-value { font-size: 115px; font-weight: 900; color: #1f77b4; line-height: 1.0; }
    .ice-unit { font-size: 32px; color: #555; font-weight: bold; margin-left: 8px; }
    .action-call { background-color: #1E3A8A; color: white; padding: 15px; border-radius: 10px; font-size: 24px; font-weight: bold; text-align: center; margin-top: 15px; }
    .schedule-box { padding: 20px; border-radius: 10px; border: 2px dashed #4682B4; background-color: #F0F8FF; font-size: 20px;}
    .schedule-time { font-size: 32px; font-weight: bold; }
    .hourly-card { background-color: #f8f9fa; padding: 12px; border-radius: 8px; margin-top: 10px; box-shadow: 1px 1px 5px rgba(0,0,0,0.05); display: flex; flex-direction: column; gap: 4px; }
    .hourly-card-today { background-color: #f0f8ff; padding: 12px; border-radius: 8px; margin-top: 10px; box-shadow: 1px 1px 5px rgba(0,0,0,0.05); display: flex; flex-direction: column; gap: 4px; }
    .cloud-badge { font-size:11px; background:#e2e8f0; color:#495057; padding:4px 6px; border-radius:6px; text-align:center; line-height:1.4; }
    .status-banner-ecmwf { background-color: #d4edda; color: #155724; padding: 12px 20px; border-radius: 8px; font-size: 18px; font-weight: bold; margin-bottom: 20px; border-left: 6px solid #28a745; }
    .status-banner-vc { background-color: #fff3cd; color: #856404; padding: 12px 20px; border-radius: 8px; font-size: 18px; font-weight: bold; margin-bottom: 20px; border-left: 6px solid #ffc107; }
    .status-banner-fail { background-color: #f8d7da; color: #721c24; padding: 12px 20px; border-radius: 8px; font-size: 18px; font-weight: bold; margin-bottom: 20px; border-left: 6px solid #dc3545; }
    </style>
    """, unsafe_allow_html=True)

# --- 2. 參數與原廠硬體規格 ---
ICE_CHILLER_KW = 241.0       
ICE_CHILLER_CAP_RT = 242.5   
ICE_BANK_MAX_RTHR = 2500.0   

MAG_CHILLER_RT = 200.0       
MAG_CAP_LIMIT = 0.70         
MAG_EFF = 0.7                
SOLAR_MAX_KW = 135.0         

now_dt = datetime.now(TW_TZ)
current_month = now_dt.month
CONTRACT_LIMIT, season_tag = (452.0, "夏月") if 6 <= current_month <= 9 else (516.0, "非夏月")

historical_max_demand = {1: 274, 2: 262, 3: 286, 4: 366, 5: 362, 6: 365, 7: 530, 8: 504, 9: 428, 10: 460, 11: 500, 12: 394}
base_load_historical = historical_max_demand.get(current_month, 400)

with st.sidebar:
    st.header("⚙️ 系統與營運參數")
    st.info("📡 氣象大腦：雙核心自動備援 (ECMWF 主力 + VC 備援)")
    st.markdown("---")
    st.header("🌞 太陽能預測校正")
    solar_mode = st.radio("太陽能預估模式", ["🤖 API 短波輻射精準推算", "✋ 廠務手動強制設定"])
    if solar_mode == "✋ 廠務手動強制設定":
        manual_solar = st.slider("手動設定巔峰太陽能 (kW)", min_value=0.0, max_value=SOLAR_MAX_KW, value=80.0, step=1.0)
    else: manual_solar = 80.0
    st.markdown("---")
    st.header("🏢 動態負載微調")
    occupancy_rate = st.slider("今日園區預估進駐率 (%)", min_value=0, max_value=100, value=70, step=5)
    chiller_compensation = st.number_input("預估磁浮主機平均耗電 (kW)", min_value=0.0, max_value=140.0, value=50.0, step=5.0)
    st.markdown("---")
    if st.button("🔄 強制同步最新氣象", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

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

# --- 3. 智慧氣象抓取 (自動備援機制) ---
today_str = now_dt.strftime("%Y-%m-%d")
tmr_dt = now_dt + timedelta(days=1)
tmr_str = tmr_dt.strftime("%Y-%m-%d")

TAIWAN_HOLIDAYS_2026 = ["2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-27", "2026-04-03", "2026-04-04", "2026-04-06", "2026-05-01", "2026-06-19", "2026-09-25", "2026-09-28", "2026-10-09", "2026-10-26", "2026-12-25"]
today_is_holiday = now_dt.weekday() >= 5 or today_str in TAIWAN_HOLIDAYS_2026
tmr_is_holiday = tmr_dt.weekday() >= 5 or tmr_str in TAIWAN_HOLIDAYS_2026

@st.cache_data(ttl=300) 
def get_smart_weather():
    fetch_time = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
    res = {
        "fetch_time": fetch_time, "status_code": 0, "source": "盲估",
        "wx": "未知", "cloud": 0, "rad": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_rad": 400, 
        "cloud_low": 0, "cloud_mid": 0, "cloud_high": 0, "today_hourly": {}, "hourly": {}
    }
    today_prefix = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    tmr_prefix = (datetime.now(TW_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    lat, lon = "23.936537", "120.697917"
    target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
    
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=0.5) # 快速重試 2 次
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)

    # ＝＝＝ [計畫 A]：嘗試抓取老闆指定的 ECMWF ＝＝＝
    try:
        om_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,weather_code,shortwave_radiation&hourly=temperature_2m,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,weather_code,shortwave_radiation&timezone=Asia%2FTaipei&models=ecmwf_ifs"
        r_om = session.get(om_url, timeout=5) # 只等 5 秒，不行就立刻放棄換備援
        
        if r_om.status_code == 200:
            r = r_om.json()
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
            for hour in target_hours:
                t_td = f"{today_prefix}T{hour}"
                if t_td in times_list:
                    idx = times_list.index(t_td)
                    res["today_hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "rad": r['hourly']['shortwave_radiation'][idx], "c_low": r['hourly']['cloud_cover_low'][idx], "c_mid": r['hourly']['cloud_cover_mid'][idx], "c_high": r['hourly']['cloud_cover_high'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
                t_tm = f"{tmr_prefix}T{hour}"
                if t_tm in times_list:
                    idx = times_list.index(t_tm)
                    res["hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "rad": r['hourly']['shortwave_radiation'][idx], "c_low": r['hourly']['cloud_cover_low'][idx], "c_mid": r['hourly']['cloud_cover_mid'][idx], "c_high": r['hourly']['cloud_cover_high'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
            
            try:
                res["tmr_temp"] = max([r['hourly']['temperature_2m'][times_list.index(f"{tmr_prefix}T{h}:00")] for h in range(12, 16)])
            except: res["tmr_temp"] = res["hourly"].get("12:00", {}).get("temp", 28.0)
            
            try:
                tmr_rads = [r['hourly']['shortwave_radiation'][times_list.index(f"{tmr_prefix}T{h:02d}:00")] for h in range(8, 17, 2)]
                res["tmr_rad"] = int(sum(tmr_rads) / len(tmr_rads))
            except: res["tmr_rad"] = res["rad"]
            return res
    except Exception as e:
        print(f"ECMWF 失敗，啟動備援: {e}")

    # ＝＝＝ [計畫 B]：ECMWF 失敗，無縫啟用 VC 企業備援 ＝＝＝
    if "VC_API_KEY" in st.secrets:
        try:
            vc_key = st.secrets["VC_API_KEY"]
            vc_url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{lat},{lon}?unitGroup=metric&key={vc_key}&contentType=json&elements=datetime,temp,cloudcover,solarradiation,conditions,tempmax"
            r_vc = session.get(vc_url, timeout=8)
            
            if r_vc.status_code == 200:
                r = r_vc.json()
                res["source"] = "VC"
                res["status_code"] = 2
                curr = r['currentConditions']
                res["wx"] = translate_wx(curr.get('conditions', '未知'))
                res["cloud"] = curr.get('cloudcover', 0)
                res["cloud_low"] = curr.get('cloudcover', 0) # VC用總雲量代替
                res["rad"] = curr.get('solarradiation', 0)
                res["temp"] = curr.get('temp', 25.0)
                res["tmr_temp"] = r['days'][1].get('tempmax', 28.0)
                
                today_hours, tmr_hours = r['days'][0]['hours'], r['days'][1]['hours']
                for h in target_hours:
                    vc_time = h + ":00"
                    for hr_data in today_hours:
                        if hr_data['datetime'] == vc_time:
                            res["today_hourly"][h] = {"temp": hr_data.get('temp', 25.0), "rad": hr_data.get('solarradiation', 0), "c_low": hr_data.get('cloudcover', 0), "c_mid": 0, "c_high": 0, "wx": translate_wx(hr_data.get('conditions', ''))}
                            break
                    for hr_data in tmr_hours:
                        if hr_data['datetime'] == vc_time:
                            res["hourly"][h] = {"temp": hr_data.get('temp', 25.0), "rad": hr_data.get('solarradiation', 0), "c_low": hr_data.get('cloudcover', 0), "c_mid": 0, "c_high": 0, "wx": translate_wx(hr_data.get('conditions', ''))}
                            break
                
                tmr_rads = [res["hourly"][h]["rad"] for h in res["hourly"] if "rad" in res["hourly"][h]]
                if tmr_rads: res["tmr_rad"] = sum(tmr_rads) / len(tmr_rads)
                return res
        except Exception as e:
            print(f"VC 備援也失敗: {e}")

    return res

w = get_smart_weather()
cloud, temp, tmr_temp = w.get("cloud",0), w.get("temp",25), w.get("tmr_temp",25)
current_rad = w.get("rad", 0)
tmr_rad = w.get("tmr_rad", 400)

api_is_online = w["status_code"] > 0

with st.sidebar:
    st.markdown("---")
    st.header("☁️ 即時雲量剖析")
    if w["status_code"] == 1:
        st.progress(w["cloud_low"] / 100.0, text=f"🌫️ 低雲層 (發電殺手): {w['cloud_low']}%")
        st.progress(w["cloud_mid"] / 100.0, text=f"☁️ 中雲層: {w['cloud_mid']}%")
        st.progress(w["cloud_high"] / 100.0, text=f"🌤️ 高雲層: {w['cloud_high']}%")
    elif w["status_code"] == 2:
        st.progress(w["cloud"] / 100.0, text=f"☁️ 總天空遮蔽率: {w['cloud']}%")
    else:
        st.error("⚠️ 雙氣象源皆斷線")
    st.markdown(f"<div style='color: #666; font-size: 14px; margin-top: 10px;'>⏱️ 氣象大腦同步：<br><b>{w['fetch_time']}</b></div>", unsafe_allow_html=True)

# --- 4. 決策大腦運算 ---
today_ice_rest = chiller_compensation if 1 <= current_month <= 5 else 0.0
today_base_load = base_load_historical + today_ice_rest
today_actual_load = 70.0 * (occupancy_rate / 100.0)
today_shaved_kw = MAG_CHILLER_RT * (1.0 - MAG_CAP_LIMIT) * MAG_EFF
today_max_net = 0.0
today_worst_hour = "未知"

target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
if api_is_online:
    max_rad_today_real = max([w["today_hourly"][h]["rad"] for h in target_hours if h in w["today_hourly"]] + [1])
    for h in target_hours:
        if h in w["today_hourly"]:
            h_temp, h_rad = w["today_hourly"][h]['temp'], w["today_hourly"][h]['rad']
            h_load = 160.0 if today_is_holiday else today_base_load + today_actual_load + max(0, (h_temp - 25.0) * 5.5) - today_shaved_kw
            h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0) if solar_mode == "🤖 API 短波輻射精準推算" else min(manual_solar, manual_solar * (h_rad / max_rad_today_real if max_rad_today_real > 0 else 0))
            h_net = h_load - h_solar
            if h_net > today_max_net:
                today_max_net, today_worst_hour = h_net, h
else:
    h_load = 160.0 if today_is_holiday else today_base_load + today_actual_load + max(0, (28.0 - 25.0) * 5.5) - today_shaved_kw
    today_max_net, today_worst_hour = h_load - (SOLAR_MAX_KW * 0.4), "斷線盲估"

if tmr_is_holiday: tmr_true_base_load, tmr_actual_load_growth, tmr_temp_penalty, tmr_shaved_kw = 160.0, 0.0, 0.0, 0.0
else:
    tmr_ice_rest = chiller_compensation if 1 <= current_month <= 5 else 0.0
    tmr_true_base_load = base_load_historical + tmr_ice_rest
    tmr_actual_load_growth = 70.0 * (occupancy_rate / 100.0)
    tmr_temp_penalty = max(0, (tmr_temp - 25.0) * 5.5)
    tmr_shaved_kw = MAG_CHILLER_RT * (1.0 - MAG_CAP_LIMIT) * MAG_EFF

final_predicted_demand = tmr_true_base_load + tmr_actual_load_growth + tmr_temp_penalty - tmr_shaved_kw
est_solar = SOLAR_MAX_KW * min(1.0, w.get("tmr_rad", 400) / 1000.0) if solar_mode == "🤖 API 短波輻射精準推算" else manual_solar

max_net_grid_demand, worst_hour, worst_hour_load, worst_hour_solar = 0.0, "未知", 0.0, 0.0

if api_is_online:
    max_rad_tmr = max([w["hourly"][h]["rad"] for h in target_hours if h in w["hourly"]] + [1])
    for h in target_hours:
        if h in w["hourly"]:
            h_temp, h_rad = w["hourly"][h]['temp'], w["hourly"][h]['rad']
            h_load = 160.0 if tmr_is_holiday else tmr_true_base_load + tmr_actual_load_growth + max(0, (h_temp - 25.0) * 5.5) - tmr_shaved_kw
            h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0) if solar_mode == "🤖 API 短波輻射精準推算" else min(manual_solar, manual_solar * (h_rad / max_rad_tmr if max_rad_tmr > 0 else 0))
            h_net = h_load - h_solar
            if h_net > max_net_grid_demand:
                max_net_grid_demand, worst_hour, worst_hour_load, worst_hour_solar = h_net, h, h_load, h_solar
else:
    max_net_grid_demand, worst_hour = final_predicted_demand - (SOLAR_MAX_KW * 0.4), "斷線盲估"
    worst_hour_load, worst_hour_solar = final_predicted_demand, SOLAR_MAX_KW * 0.4

demand_gap = max_net_grid_demand - (CONTRACT_LIMIT - 15.0)
needed_ice_rthr_for_grid = (demand_gap / MAG_EFF) * 6.0 if demand_gap > 0 else 0
extra_ice_rthr_for_cooling = MAG_CHILLER_RT * (1.0 - MAG_CAP_LIMIT) * 4.0 if not tmr_is_holiday else 0.0

if tmr_is_holiday:
    suggested_ice_hrs = 0.0
    start_time_str, end_time_str, melt_start, melt_end, time_color = "關閉排程", "關閉排程", "關閉排程", "關閉排程", "#dc3545" 
else:
    suggested_ice_hrs = max(1.5, min(9.0, ((needed_ice_rthr_for_grid + extra_ice_rthr_for_cooling) * 1.2) / ICE_CHILLER_CAP_RT))
    end_minutes = 7 * 60 
    start_minutes = int(end_minutes - (suggested_ice_hrs * 60))
    if start_minutes < 0: start_minutes += 24 * 60
    start_time_str, end_time_str, melt_start, melt_end, time_color = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}", "07:00", "10:00", "16:00", "#D2691E"

# --- 5. 渲染 UI ---
st.title("❄️ 中創園區空調聯防：H300行動戰情室 V3.1")

if w["status_code"] == 1:
    st.markdown("<div class='status-banner-ecmwf'>📡 系統狀態：🟢 ECMWF 歐洲衛星連線正常 (主力運作中)</div>", unsafe_allow_html=True)
elif w["status_code"] == 2:
    st.markdown("<div class='status-banner-vc'>📡 系統狀態：🟡 ECMWF 遭遇壅塞，已無縫啟動 VC 企業備援 (數據保障中)</div>", unsafe_allow_html=True)
else:
    st.markdown("<div class='status-banner-fail'>📡 系統狀態：🔴 雙氣象源皆斷線 (已切換至保守盲估模式)</div>", unsafe_allow_html=True)

if tmr_is_holiday: action_msg = f"🎉 假日停機警報：明日 ({tmr_str}) 為休息日/補假！請【暫停今晚儲冰】，並手動解除排程。"
elif suggested_ice_hrs <= 2: action_msg = f"🟢 預估明日最高需量 {max_net_grid_demand:.1f} kW，電力餘裕充足，執行例行儲冰即可。"
elif suggested_ice_hrs <= 5: action_msg = f"🟡 預估明日最高需量 {max_net_grid_demand:.1f} kW 逼近警戒！需補充 70% 封印缺口，請加強儲冰。"
else: action_msg = f"🔴 警告：明日危險時段需量暴增至 {max_net_grid_demand:.1f} kW！嚴防午後超約，務必長時間儲冰！"

st.markdown("### 🔔 健維哥-空調核心指令 (今晚任務)")
c_action, c_metrics = st.columns([1.2, 1])
with c_action:
    border_color = "#17a2b8" if tmr_is_holiday else ("#28a745" if suggested_ice_hrs <= 2 else "#ffc107" if suggested_ice_hrs <= 5 else "#dc3545")
    st.markdown(f"""<div class="ice-card" style="border: 4px solid {border_color};"><div style="font-size: 28px; color: #666; font-weight: bold; margin-bottom: 15px;">建議今晚儲冰時間</div><div><span class="ice-value">{suggested_ice_hrs:.1f}</span><span class="ice-unit">小時</span></div></div>""", unsafe_allow_html=True)

with c_metrics:
    st.markdown(f"""<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px 15px; min-height: 320px; align-content: center;"><div><div style="font-size: 15px; color: #555;">目前園區氣溫</div><div style="font-size: 38px; font-weight: 700; color: #2c3e50;">{temp} <span style="font-size: 16px;">°C</span></div></div><div><div style="font-size: 15px; color: #555;">明日預測最高溫</div><div style="font-size: 38px; font-weight: 700; color: #2c3e50;">{tmr_temp} <span style="font-size: 16px;">°C</span></div></div><div><div style="font-size: 15px; color: #555;">目前短波輻射強度</div><div style="font-size: 38px; font-weight: 700; color: #d35400;">{current_rad} <span style="font-size: 16px;">W/m²</span></div></div><div><div style="font-size: 15px; color: #555;">明日平均太陽能</div><div style="font-size: 38px; font-weight: 700; color: #2c3e50;">{est_solar:.1f} <span style="font-size: 16px;">kW</span></div></div><div style="background: #f0f8ff; padding: 10px 15px; border-radius: 8px; border-left: 4px solid #17a2b8;"><div style="font-size: 14px; color: #555; font-weight: bold;">今日最危險 ({today_worst_hour})</div><div style="font-size: 38px; font-weight: 900; color: #17a2b8;">{today_max_net:.1f} <span style="font-size: 16px;">kW</span></div></div><div style="background: #ffeaea; padding: 10px 15px; border-radius: 8px; border-left: 4px solid #dc3545;"><div style="font-size: 14px; color: #555; font-weight: bold;">明日最危險 ({worst_hour})</div><div style="font-size: 38px; font-weight: 900; color: #dc3545;">{max_net_grid_demand:.1f} <span style="font-size: 16px;">kW</span></div></div></div>""", unsafe_allow_html=True)

st.markdown(f'<div class="action-call" style="background-color: {"#17a2b8" if tmr_is_holiday else "#1E3A8A"};">{action_msg}</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
st.subheader("📝 中央監控系統 (儲融冰) 排程設定建議")
sc1, sc2 = st.columns(2)
with sc1:
    memo_1 = "*明日為假日，無需儲冰備戰。" if tmr_is_holiday else "*已包含填補磁浮 70% 封印所需之額外冰量。"
    st.markdown(f"""<div class="schedule-box"><b>❄️ 夜間儲冰排程</b><br><br>啟動：<span class="schedule-time" style="color:{time_color};">{start_time_str}</span><br>停止：<span class="schedule-time" style="color:{time_color};">{end_time_str}</span><br><br><span style="font-size:16px; color:#666;">{memo_1}</span></div>""", unsafe_allow_html=True)
with sc2:
    memo_2 = "*明日為假日，務必手動關閉空調自動排程！" if tmr_is_holiday else "*依 IB-1 設計 13°C 進水條件執行。"
    st.markdown(f"""<div class="schedule-box"><b>💧 日間融冰排程</b><br><br>啟動：<span class="schedule-time" style="color:{time_color};">{melt_start}</span><br>停止：<span class="schedule-time" style="color:{time_color};">{melt_end}</span><br><br><span style="font-size:16px; color:#666;">{memo_2}</span></div>""", unsafe_allow_html=True)

st.markdown("---")
st.subheader(f"⚡ 今日關鍵時段即時追蹤 ({today_str} 現場比對專用)")

if api_is_online:
    h_cols_today = st.columns(5)
    for i, h in enumerate(target_hours):
        with h_cols_today[i]:
            st.markdown(f"<div style='text-align:center; font-size:18px; font-weight:bold; color:#17a2b8;'>⏰ {h}</div>", unsafe_allow_html=True)
            if h in w["today_hourly"]:
                h_data = w["today_hourly"][h]
                h_temp, h_rad = h_data['temp'], h_data['rad']
                c_low = h_data.get('c_low',0)
                if today_is_holiday: h_load = 160.0
                else: h_load = today_base_load + today_actual_load + max(0, (h_temp - 25.0) * 5.5) - today_shaved_kw
                if solar_mode == "🤖 API 短波輻射精準推算": h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0)
                else: h_solar = min(manual_solar, manual_solar * (h_rad / max_rad_today_real if max_rad_today_real > 0 else 0))
                h_net = h_load - h_solar
                card_color = "#dc3545" if h_net > CONTRACT_LIMIT - 15 else ("#ffc107" if h_net > CONTRACT_LIMIT - 50 else "#28a745")
                st.write(f"🌤️ {h_data['wx']}")
                st.write(f"🌡️ {h_temp} °C | ☀️ {h_rad} W/m²")
                st.markdown(f"""<div class="hourly-card-today" style="border-left-color: {card_color};"><div class="cloud-badge"><div>☁️ 雲分布</div><div style="font-weight:bold;">{c_low}%</div></div><div style="font-size:13px; color:#555;">🏭 總負載: {h_load:.1f}</div><div style="font-size:13px; color:#28a745;">🌞 太陽能: -{h_solar:.1f}</div><div style="height:1px; background-color:#b8daff; margin:2px 0;"></div><div style="font-size:16px; font-weight:bold; color:{card_color};">⚡ 需量: {h_net:.0f} kW</div></div>""", unsafe_allow_html=True)
            else: st.write("資料擷取中...")
else: st.warning("📡 由於 API 暫時無法連線，系統已暫停繪製今日逐時雷達圖。")

st.markdown("---")
st.subheader(f"🎯 明日關鍵時段預報追蹤 ({tmr_str} 儲冰防禦準備)")
if api_is_online:
    h_cols = st.columns(5)
    for i, h in enumerate(target_hours):
        with h_cols[i]:
            st.markdown(f"<div style='text-align:center; font-size:18px; font-weight:bold; color:#1E3A8A;'>⏰ {h}</div>", unsafe_allow_html=True)
            if h in w["hourly"]:
                h_data = w["hourly"][h]
                h_temp, h_rad = h_data['temp'], h_data['rad']
                c_low = h_data.get('c_low',0)
                if tmr_is_holiday: h_load = 160.0
                else: h_load = tmr_true_base_load + tmr_actual_load_growth + max(0, (h_temp - 25.0) * 5.5) - tmr_shaved_kw
                if solar_mode == "🤖 API 短波輻射精準推算": h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0)
                else: h_solar = min(manual_solar, manual_solar * (h_rad / max_rad_tmr if max_rad_tmr > 0 else 0))
                h_net = h_load - h_solar
                card_color = "#dc3545" if h_net > CONTRACT_LIMIT - 15 else ("#ffc107" if h_net > CONTRACT_LIMIT - 50 else "#28a745")
                st.write(f"🌤️ {h_data['wx']}")
                st.write(f"🌡️ {h_temp} °C | ☀️ {h_rad} W/m²")
                st.markdown(f"""<div class="hourly-card" style="border-left: 4px solid {card_color};"><div class="cloud-badge"><div>☁️ 雲分布</div><div style="font-weight:bold;">{c_low}%</div></div><div style="font-size:13px; color:#555;">🏭 總負載: {h_load:.1f}</div><div style="font-size:13px; color:#28a745;">🌞 太陽能: -{h_solar:.1f}</div><div style="height:1px; background-color:#ddd; margin:2px 0;"></div><div style="font-size:16px; font-weight:bold; color:{card_color};">⚡ 需量: {h_net:.0f} kW</div></div>""", unsafe_allow_html=True)
            else: st.write("資料擷取中...")
else: st.warning("📡 由於 API 暫時無法連線，系統已暫停繪製明日逐時雷達圖。")

st.markdown("---")
st.subheader("📊 明日防禦決策基準：聚焦最嚴苛時段")
c1, c2, c3, c4 = st.columns(4)
if tmr_is_holiday:
    c1.metric("非上班日基礎負載", f"{tmr_true_base_load:.1f} kW", delta_color="off")
    c2.metric("📈 動態與高溫加載", f"+0.0 kW")
else:
    c1.metric("歷史基礎與動態加載", f"{tmr_true_base_load + tmr_actual_load_growth:.1f} kW", delta_color="off")
    c2.metric("🌡️ 高溫熱負荷加載", f"+{tmr_temp_penalty:.1f} kW")
c3.metric("🛡️ 磁浮 70% 封印降載", f"-{tmr_shaved_kw:.1f} kW", delta_color="normal")
c4.metric("🔥 園區絕對最高負載", f"{final_predicted_demand:.1f} kW", delta_color="off")

c5, c6, c7, c8 = st.columns(4)
c5.metric(f"🔥 {worst_hour} 預估負載", f"{worst_hour_load:.1f} kW", delta_color="off")
c6.metric(f"📉 {worst_hour} 太陽能殘值", f"-{worst_hour_solar:.1f} kW", delta_color="normal")
c7.metric("⚡ 真實最高台電需量", f"{max_net_grid_demand:.1f} kW", delta_color="inverse")
c8.metric("🛑 契約警戒線", f"{CONTRACT_LIMIT} kW")

st.markdown("---")
st.markdown(f"<div style='text-align: center; color: #666;'>系統運行中 | 氣象更新時間：{w['fetch_time']} | 設備參數：BCU-1 & IB-1</div>", unsafe_allow_html=True)
