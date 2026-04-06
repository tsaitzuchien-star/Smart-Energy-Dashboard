import streamlit as st
import requests
import urllib3
import os
from datetime import datetime, timedelta, timezone

# 匯入 Google Sheets 所需套件
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GS_AVAILABLE = True
except ImportError:
    GS_AVAILABLE = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
TW_TZ = timezone(timedelta(hours=8))

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="中創園區空調聯防戰情室 V2.52", page_icon="❄️", layout="wide")

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
    .status-banner-ok { background-color: #d4edda; color: #155724; padding: 12px 20px; border-radius: 8px; font-size: 18px; font-weight: bold; margin-bottom: 20px; border-left: 6px solid #28a745; }
    .status-banner-fail { background-color: #fff3cd; color: #856404; padding: 12px 20px; border-radius: 8px; font-size: 18px; font-weight: bold; margin-bottom: 20px; border-left: 6px solid #ffc107; }
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
    primary_brain = st.radio("氣象大腦來源", ["🇪🇺 歐洲 ECMWF 輻射預測 (最準確)", "🇹🇼 台灣氣象署 (南投縣)"])
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

# --- 3. 氣象抓取 ---
today_str = now_dt.strftime("%Y-%m-%d")
tmr_dt = now_dt + timedelta(days=1)
tmr_str = tmr_dt.strftime("%Y-%m-%d")

TAIWAN_HOLIDAYS_2026 = ["2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-27", "2026-04-03", "2026-04-04", "2026-04-06", "2026-05-01", "2026-06-19", "2026-09-25", "2026-09-28", "2026-10-09", "2026-10-26", "2026-12-25"]
today_is_holiday = now_dt.weekday() >= 5 or today_str in TAIWAN_HOLIDAYS_2026
tmr_is_holiday = tmr_dt.weekday() >= 5 or tmr_str in TAIWAN_HOLIDAYS_2026

@st.cache_data(ttl=300) 
def get_dual_weather():
    fetch_time = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
    res_dict = {"fetch_time": fetch_time, "cwa": {"status": "🔴", "wx": "未知", "cloud": 0, "rad": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_cloud": 30, "tmr_rad": 400}, "owm": {"status": "🔴", "wx": "未知", "cloud": 0, "rad": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_cloud": 30, "tmr_rad": 400, "cloud_low": 0, "cloud_mid": 0, "cloud_high": 0, "today_hourly": {}, "hourly": {}}}
    today_prefix = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    tmr_prefix = (datetime.now(TW_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        lat, lon = "23.936537", "120.697917"
        om_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,weather_code,shortwave_radiation&hourly=temperature_2m,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,weather_code,shortwave_radiation&timezone=Asia%2FTaipei&models=ecmwf_ifs"
        r = requests.get(om_url, timeout=5).json()
        res_dict["owm"]["status"] = "🟢"
        res_dict["owm"]["wx"] = wmo_to_text(r['current']['weather_code'])
        res_dict["owm"]["cloud"] = r['current']['cloud_cover']
        res_dict["owm"]["cloud_low"] = r['current']['cloud_cover_low']
        res_dict["owm"]["cloud_mid"] = r['current']['cloud_cover_mid']
        res_dict["owm"]["cloud_high"] = r['current']['cloud_cover_high']
        res_dict["owm"]["rad"] = r['current']['shortwave_radiation']
        res_dict["owm"]["temp"] = r['current']['temperature_2m']
        
        target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
        times_list = r['hourly']['time']
        for hour in target_hours:
            t_str_today = f"{today_prefix}T{hour}"
            if t_str_today in times_list:
                idx = times_list.index(t_str_today)
                res_dict["owm"]["today_hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "rad": r['hourly']['shortwave_radiation'][idx], "c_low": r['hourly']['cloud_cover_low'][idx], "c_mid": r['hourly']['cloud_cover_mid'][idx], "c_high": r['hourly']['cloud_cover_high'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
        for hour in target_hours:
            t_str_tmr = f"{tmr_prefix}T{hour}"
            if t_str_tmr in times_list:
                idx = times_list.index(t_str_tmr)
                res_dict["owm"]["hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "rad": r['hourly']['shortwave_radiation'][idx], "c_low": r['hourly']['cloud_cover_low'][idx], "c_mid": r['hourly']['cloud_cover_mid'][idx], "c_high": r['hourly']['cloud_cover_high'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
        try:
            tmr_temps = [r['hourly']['temperature_2m'][times_list.index(f"{tmr_prefix}T{h}:00")] for h in range(12, 16)]
            res_dict["owm"]["tmr_temp"] = max(tmr_temps)
        except:
            res_dict["owm"]["tmr_temp"] = res_dict["owm"]["hourly"].get("12:00", {}).get("temp", 28.0)
        try:
            tmr_rads = [r['hourly']['shortwave_radiation'][times_list.index(f"{tmr_prefix}T{h:02d}:00")] for h in range(8, 17, 2)]
            res_dict["owm"]["tmr_rad"] = int(sum(tmr_rads) / len(tmr_rads))
        except:
            res_dict["owm"]["tmr_rad"] = res_dict["owm"]["rad"]
    except: pass
    return res_dict

w = get_dual_weather()
sel = w["owm"] if "歐洲" in primary_brain and w["owm"]["status"] == "🟢" else w["cwa"]
cloud, temp, tmr_temp = sel.get("cloud",0), sel.get("temp",25), sel.get("tmr_temp",25)
current_rad = sel.get("rad", 0)
tmr_rad = sel.get("tmr_rad", 400)

api_is_online = "🟢" in w["owm"]["status"] and bool(w["owm"].get("hourly"))

with st.sidebar:
    st.markdown("---")
    st.header("☁️ 即時天空剖析 (ECMWF)")
    if api_is_online:
        st.progress(w["owm"]["cloud_low"] / 100.0, text=f"🌫️ 低雲層 (發電殺手): {w['owm']['cloud_low']}%")
        st.progress(w["owm"]["cloud_mid"] / 100.0, text=f"☁️ 中雲層 (微弱影響): {w['owm']['cloud_mid']}%")
        st.progress(w["owm"]["cloud_high"] / 100.0, text=f"🌤️ 高雲層 (陽光穿透): {w['owm']['cloud_high']}%")
    else:
        st.error("⚠️ API 連線中斷，無法顯示雲層。")
    st.markdown(f"<div style='color: #666; font-size: 14px; margin-top: 10px;'>⏱️ 氣象大腦同步：<br><b>{w['fetch_time']}</b></div>", unsafe_allow_html=True)

# --- 4. 決策大腦運算 ---
# [4.1 今日決策]
today_ice_rest = chiller_compensation if 1 <= current_month <= 5 else 0.0
today_base_load = base_load_historical + today_ice_rest
today_actual_load = 70.0 * (occupancy_rate / 100.0)
today_shaved_kw = MAG_CHILLER_RT * (1.0 - MAG_CAP_LIMIT) * MAG_EFF
today_max_net = 0.0
today_worst_hour = "未知"

if api_is_online:
    target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
    max_rad_today_real = max([w["owm"]["today_hourly"][h]["rad"] for h in target_hours if h in w["owm"]["today_hourly"]] + [1])
    for h in target_hours:
        if h in w["owm"]["today_hourly"]:
            h_temp = w["owm"]["today_hourly"][h]['temp']
            h_rad = w["owm"]["today_hourly"][h]['rad']
            if today_is_holiday: h_load = 160.0
            else: h_load = today_base_load + today_actual_load + max(0, (h_temp - 25.0) * 5.5) - today_shaved_kw
            if solar_mode == "🤖 API 短波輻射精準推算": h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0)
            else: h_solar = min(manual_solar, manual_solar * (h_rad / max_rad_today_real if max_rad_today_real > 0 else 0))
            h_net = h_load - h_solar
            if h_net > today_max_net:
                today_max_net = h_net
                today_worst_hour = h
else:
    if today_is_holiday: h_load = 160.0
    else: h_load = today_base_load + today_actual_load + max(0, (28.0 - 25.0) * 5.5) - today_shaved_kw
    today_max_net = h_load - (SOLAR_MAX_KW * 0.4) 
    today_worst_hour = "斷線盲估"

# [4.2 明日決策]
if tmr_is_holiday:
    tmr_true_base_load = 160.0
    tmr_actual_load_growth = 0.0
    tmr_temp_penalty = 0.0
    tmr_shaved_kw = 0.0
else:
    tmr_ice_rest = chiller_compensation if 1 <= current_month <= 5 else 0.0
    tmr_true_base_load = base_load_historical + tmr_ice_rest
    tmr_actual_load_growth = 70.0 * (occupancy_rate / 100.0)
    tmr_temp_penalty = max(0, (tmr_temp - 25.0) * 5.5)
    tmr_shaved_kw = MAG_CHILLER_RT * (1.0 - MAG_CAP_LIMIT) * MAG_EFF

final_predicted_demand = tmr_true_base_load + tmr_actual_load_growth + tmr_temp_penalty - tmr_shaved_kw

if solar_mode == "🤖 API 短波輻射精準推算":
    solar_eff = min(1.0, tmr_rad / 1000.0)
    est_solar = SOLAR_MAX_KW * solar_eff
    solar_ui_label = f"↑ 實測大數據轉換"
else:
    est_solar = manual_solar
    solar_ui_label = f"↑ ✋ 廠務手動巔峰校正"

max_net_grid_demand = 0.0
worst_hour = "未知"
worst_hour_load = 0.0
worst_hour_solar = 0.0

if api_is_online:
    target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
    max_rad_tmr = max([w["owm"]["hourly"][h]["rad"] for h in target_hours if h in w["owm"]["hourly"]] + [1])
    for h in target_hours:
        if h in w["owm"]["hourly"]:
            h_temp = w["owm"]["hourly"][h]['temp']
            h_rad = w["owm"]["hourly"][h]['rad']
            if tmr_is_holiday: h_load = 160.0
            else: h_load = tmr_true_base_load + tmr_actual_load_growth + max(0, (h_temp - 25.0) * 5.5) - tmr_shaved_kw
            if solar_mode == "🤖 API 短波輻射精準推算": h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0)
            else: h_solar = min(manual_solar, manual_solar * (h_rad / max_rad_tmr if max_rad_tmr > 0 else 0))
            h_net = h_load - h_solar
            if h_net > max_net_grid_demand:
                max_net_grid_demand = h_net
                worst_hour = h
                worst_hour_load = h_load
                worst_hour_solar = h_solar
else:
    max_net_grid_demand = final_predicted_demand - (SOLAR_MAX_KW * 0.4)
    worst_hour = "斷線盲估"
    worst_hour_load = final_predicted_demand
    worst_hour_solar = SOLAR_MAX_KW * 0.4

demand_gap = max_net_grid_demand - (CONTRACT_LIMIT - 15.0)
needed_ice_rthr_for_grid = (demand_gap / MAG_EFF) * 6.0 if demand_gap > 0 else 0
extra_ice_rthr_for_cooling = MAG_CHILLER_RT * (1.0 - MAG_CAP_LIMIT) * 4.0 if not tmr_is_holiday else 0.0

if tmr_is_holiday:
    suggested_ice_hrs = 0.0
    start_time_str, end_time_str, melt_start, melt_end = "關閉排程", "關閉排程", "關閉排程", "關閉排程"
    time_color = "#dc3545" 
else:
    suggested_ice_hrs = max(1.5, min(9.0, ((needed_ice_rthr_for_grid + extra_ice_rthr_for_cooling) * 1.2) / ICE_CHILLER_CAP_RT))
    end_minutes = 7 * 60 
    start_minutes = int(end_minutes - (suggested_ice_hrs * 60))
    if start_minutes < 0: start_minutes += 24 * 60
    start_time_str = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}"
    end_time_str = "07:00"
    melt_start, melt_end = "10:00", "16:00"
    time_color = "#D2691E"

# --- 5. 渲染 UI ---
st.title("❄️ 中創園區空調聯防：H300行動戰情室 V2.52")

if api_is_online:
    st.markdown("<div class='status-banner-ok'>📡 系統狀態：🟢 ECMWF 衛星連線正常 (資料即時同步中)</div>", unsafe_allow_html=True)
else:
    st.markdown("<div class='status-banner-fail'>📡 系統狀態：🔴 API 壅塞斷線 (已自動切換至保守盲估模式，保障防禦底線)</div>", unsafe_allow_html=True)

if tmr_is_holiday:
    action_msg = f"🎉 假日停機警報：明日 ({tmr_str}) 為休息日/補假！請【暫停今晚儲冰】，並手動解除排程。"
elif suggested_ice_hrs <= 2:
    action_msg = f"🟢 預估明日最高需量 {max_net_grid_demand:.1f} kW，電力餘裕充足，執行例行儲冰即可。"
elif suggested_ice_hrs <= 5:
    action_msg = f"🟡 預估明日最高需量 {max_net_grid_demand:.1f} kW 逼近警戒！需補充 70% 封印缺口，請加強儲冰。"
else:
    action_msg = f"🔴 警告：明日危險時段需量暴增至 {max_net_grid_demand:.1f} kW！嚴防午後超約，務必長時間儲冰！"

st.markdown("### 🔔 健維哥-空調核心指令 (今晚任務)")
c_action, c_metrics = st.columns([1.2, 1])
with c_action:
    border_color = "#17a2b8" if tmr_is_holiday else ("#28a745" if suggested_ice_hrs <= 2 else "#ffc107" if suggested_ice_hrs <= 5 else "#dc3545")
    st.markdown(f"""<div class="ice-card" style="border: 4px solid {border_color};"><div style="font-size: 28px; color: #666; font-weight: bold; margin-bottom: 15px;">建議今晚儲冰時間</div><div><span class="ice-value">{suggested_ice_hrs:.1f}</span><span class="ice-unit">小時</span></div></div>""", unsafe_allow_html=True)

with c_metrics:
    st.markdown(f"""<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px 15px; min-height: 320px; align-content: center;"><div><div style="font-size: 15px; color: #555;">目前園區氣溫</div><div style="font-size: 38px; font-weight: 700; color: #2c3e50;">{temp} <span style="font-size: 16px;">°C</span></div></div><div><div style="font-size: 15px; color: #555;">明日預測最高溫</div><div style="font-size: 38px; font-weight: 700; color: #2c3e50;">{tmr_temp} <span style="font-size: 16px;">°C</span></div></div><div><div style="font-size: 15px; color: #555;">目前短波輻射強度</div><div style="font-size: 38px; font-weight: 700; color: #d35400;">{current_rad} <span style="font-size: 16px;">W/m²</span></div></div><div><div style="font-size: 15px; color: #555;">明日平均太陽能</div><div style="font-size: 38px; font-weight: 700; color: #2c3e50;">{est_solar:.1f} <span style="font-size: 16px;">kW</span></div></div><div style="background: #f0f8ff; padding: 10px 15px; border-radius: 8px; border-left: 4px solid #17a2b8;"><div style="font-size: 14px; color: #555; font-weight: bold;">今日最危險 ({today_worst_hour})</div><div style="font-size: 38px; font-weight: 900; color: #17a2b8;">{today_max_net:.1f} <span style="font-size: 16px;">kW</span></div></div><div style="background: #ffeaea; padding: 10px 15px; border-radius: 8px; border-left: 4px solid #dc3545;"><div style="font-size: 14px; color: #555; font-weight: bold;">明日最危險 ({worst_hour})</div><div style="font-size: 38px; font-weight: 900; color: #dc3545;">{max_net_grid_demand:.1f} <span style="font-size: 16px;">kW</span></div></div></div>""", unsafe_allow_html=True)

action_bg = "#17a2b8" if tmr_is_holiday else "#1E3A8A"
st.markdown(f'<div class="action-call" style="background-color: {action_bg};">{action_msg}</div>', unsafe_allow_html=True)

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
            if h in w["owm"]["today_hourly"]:
                h_data = w["owm"]["today_hourly"][h]
                h_temp, h_rad = h_data['temp'], h_data['rad']
                c_low, c_mid, c_high = h_data.get('c_low',0), h_data.get('c_mid',0), h_data.get('c_high',0)
                if today_is_holiday: h_load = 160.0
                else: h_load = today_base_load + today_actual_load + max(0, (h_temp - 25.0) * 5.5) - today_shaved_kw
                if solar_mode == "🤖 API 短波輻射精準推算": h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0)
                else: h_solar = min(manual_solar, manual_solar * (h_rad / max_rad_today_real if max_rad_today_real > 0 else 0))
                h_net = h_load - h_solar
                card_color = "#dc3545" if h_net > CONTRACT_LIMIT - 15 else ("#ffc107" if h_net > CONTRACT_LIMIT - 50 else "#28a745")
                st.write(f"🌤️ {h_data['wx']}")
                st.write(f"🌡️ {h_temp} °C | ☀️ {h_rad} W/m²")
                st.markdown(f"""<div class="hourly-card-today" style="border-left-color: {card_color};"><div class="cloud-badge"><div>☁️ 雲分布 (低/中/高)</div><div style="font-weight:bold;">{c_low}% / {c_mid}% / {c_high}%</div></div><div style="font-size:13px; color:#555;">🏭 總負載: {h_load:.1f}</div><div style="font-size:13px; color:#28a745;">🌞 太陽能: -{h_solar:.1f}</div><div style="height:1px; background-color:#b8daff; margin:2px 0;"></div><div style="font-size:16px; font-weight:bold; color:{card_color};">⚡ 需量: {h_net:.0f} kW</div></div>""", unsafe_allow_html=True)
            else: st.write("資料擷取中...")
else:
    st.warning("📡 由於 API 暫時無法連線，系統已暫停繪製今日逐時雷達圖。請參考上方 6 宮格的盲估安全值，或稍後再試。")

st.markdown("---")
st.subheader(f"🎯 明日關鍵時段預報追蹤 ({tmr_str} 儲冰防禦準備)")
if api_is_online:
    h_cols = st.columns(5)
    for i, h in enumerate(target_hours):
        with h_cols[i]:
            st.markdown(f"<div style='text-align:center; font-size:18px; font-weight:bold; color:#1E3A8A;'>⏰ {h}</div>", unsafe_allow_html=True)
            if h in w["owm"]["hourly"]:
                h_data = w["owm"]["hourly"][h]
                h_temp, h_rad = h_data['temp'], h_data['rad']
                c_low, c_mid, c_high = h_data.get('c_low',0), h_data.get('c_mid',0), h_data.get('c_high',0)
                if tmr_is_holiday: h_load = 160.0
                else: h_load = tmr_true_base_load + tmr_actual_load_growth + max(0, (h_temp - 25.0) * 5.5) - tmr_shaved_kw
                if solar_mode == "🤖 API 短波輻射精準推算": h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0)
                else: h_solar = min(manual_solar, manual_solar * (h_rad / max_rad_tmr if max_rad_tmr > 0 else 0))
                h_net = h_load - h_solar
                card_color = "#dc3545" if h_net > CONTRACT_LIMIT - 15 else ("#ffc107" if h_net > CONTRACT_LIMIT - 50 else "#28a745")
                st.write(f"🌤️ {h_data['wx']}")
                st.write(f"🌡️ {h_temp} °C | ☀️ {h_rad} W/m²")
                st.markdown(f"""<div class="hourly-card" style="border-left: 4px solid {card_color};"><div class="cloud-badge"><div>☁️ 雲分布 (低/中/高)</div><div style="font-weight:bold;">{c_low}% / {c_mid}% / {c_high}%</div></div><div style="font-size:13px; color:#555;">🏭 總負載: {h_load:.1f}</div><div style="font-size:13px; color:#28a745;">🌞 太陽能: -{h_solar:.1f}</div><div style="height:1px; background-color:#ddd; margin:2px 0;"></div><div style="font-size:16px; font-weight:bold; color:{card_color};">⚡ 需量: {h_net:.0f} kW</div></div>""", unsafe_allow_html=True)
            else: st.write("資料擷取中...")
else:
    st.warning("📡 由於 API 暫時無法連線，系統已暫停繪製明日逐時雷達圖。請參考上方 6 宮格的盲估安全值，或稍後再試。")

st.markdown("---")
st.subheader("📊 明日防禦決策基準：聚焦最嚴苛時段")
c1, c2, c3, c4 = st.columns(4)
if tmr_is_holiday:
    c1.metric("非上班日基礎負載", f"{tmr_true_base_load:.1f} kW", "實測假日基本待機用電", delta_color="off")
    c2.metric("📈 動態與高溫加載", f"+0.0 kW", "假日無辦公空調需求")
else:
    c1.metric("歷史基礎與動態加載", f"{tmr_true_base_load + tmr_actual_load_growth:.1f} kW", f"依進駐率 {occupancy_rate}% 計算", delta_color="off")
    c2.metric("🌡️ 高溫熱負荷加載", f"+{tmr_temp_penalty:.1f} kW", f"預測高溫 {tmr_temp}°C")
c3.metric("🛡️ 磁浮 70% 封印降載", f"-{tmr_shaved_kw:.1f} kW", "硬體限制省下需量", delta_color="normal")
c4.metric("🔥 園區絕對最高負載", f"{final_predicted_demand:.1f} kW", "冷氣全開的物理極限", delta_color="off")

c5, c6, c7, c8 = st.columns(4)
c5.metric(f"🔥 {worst_hour} 預估負載", f"{worst_hour_load:.1f} kW", "該時段之建築耗能", delta_color="off")
c6.metric(f"📉 {worst_hour} 太陽能殘值", f"-{worst_hour_solar:.1f} kW", "太陽偏西或雲層遮蔽後之發電量", delta_color="normal")
c7.metric("⚡ 真實最高台電需量", f"{max_net_grid_demand:.1f} kW", "作為儲備防禦的最高標準", delta_color="inverse")
c8.metric("🛑 契約警戒線", f"{CONTRACT_LIMIT} kW", f"{season_tag}模式")

# ==========================================
# Google Sheets 大數據倉儲寫入系統 (附帶智慧標題)
# ==========================================
st.markdown("---")
st.subheader("💾 戰情室大數據資料庫 (Google Sheets)")

if not GS_AVAILABLE:
    st.warning("⚠️ 尚未安裝資料庫套件。請在終端機執行 `pip install gspread google-auth`。")
elif not os.path.exists("credentials.json"):
    st.info("ℹ️ 尚未偵測到 `credentials.json` 金鑰。請將 Google Service Account 金鑰放入同一個資料夾中，並將試算表共用給金鑰內的 email 以啟動寫入功能。")
else:
    if st.button("📤 將今日戰情與明日預測寫入資料庫", type="primary", use_container_width=True):
        try:
            with st.spinner("正在連線至 Google Sheets 寫入資料..."):
                scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
                creds = Credentials.from_service_account_file('credentials.json', scopes=scopes)
                client = gspread.authorize(creds)
                
                sheet = client.open('中創園區空調戰情大數據').sheet1
                
                # 【V2.52 核心修復】自動檢查並補上欄位標題
                expected_headers = [
                    "紀錄時間", "今日進駐率(%)", "今日氣溫(°C)", "今日輻射(W/m²)", 
                    "今日最危險時段", "今日最高需量(kW)", "明日預估高溫(°C)", 
                    "明日太陽能峰值(kW)", "明日最危險時段", "明日預估最高需量(kW)", "建議今晚儲冰(小時)"
                ]
                
                first_row = sheet.row_values(1)
                if not first_row:
                    sheet.append_row(expected_headers)
                
                # 寫入本次的預測數據
                data_row = [
                    w['fetch_time'],                      
                    occupancy_rate,                       
                    temp,                                 
                    current_rad,                          
                    today_worst_hour,                     
                    round(today_max_net, 1),              
                    tmr_temp,                             
                    round(est_solar, 1),                  
                    worst_hour,                           
                    round(max_net_grid_demand, 1),        
                    round(suggested_ice_hrs, 1)           
                ]
                sheet.append_row(data_row)
                st.success(f"✅ 寫入成功！已將 {w['fetch_time']} 的戰情快照與標題備份至雲端。")
        except Exception as e:
            st.error(f"❌ 寫入失敗。錯誤細節：{e}")

st.markdown(f"系統運行中 | 氣象大腦同步時間：{w['fetch_time']} | 設備參數：BCU-1(儲冰主機) & IB-1(2500RT-HR)")
