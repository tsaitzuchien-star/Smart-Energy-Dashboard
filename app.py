import streamlit as st
import requests
import urllib3
from datetime import datetime, timedelta, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
TW_TZ = timezone(timedelta(hours=8))

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="中創園區空調聯防戰情室 V2.41", page_icon="❄️", layout="wide")

st.markdown("""
    <style>
    .ice-card { background-color: white; padding: 40px 20px; border-radius: 15px; text-align: center; box-shadow: 2px 2px 10px rgba(0,0,0,0.05); height: 100%; display: flex; flex-direction: column; justify-content: center; }
    .ice-value { font-size: 85px; font-weight: 900; color: #1f77b4; line-height: 1.1; }
    .ice-unit { font-size: 28px; color: #555; font-weight: bold; }
    .action-call { background-color: #1E3A8A; color: white; padding: 15px; border-radius: 10px; font-size: 24px; font-weight: bold; text-align: center; margin-top: 15px; }
    .schedule-box { padding: 20px; border-radius: 10px; border: 2px dashed #4682B4; background-color: #F0F8FF; font-size: 20px;}
    .schedule-time { font-size: 32px; font-weight: bold; }
    .hourly-card { background-color: #f8f9fa; padding: 12px; border-radius: 8px; margin-top: 10px; box-shadow: 1px 1px 5px rgba(0,0,0,0.05); }
    .hourly-card-today { background-color: #f0f8ff; padding: 12px; border-radius: 8px; margin-top: 10px; box-shadow: 1px 1px 5px rgba(0,0,0,0.05); border-left: 4px solid #17a2b8; }
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
    solar_mode = st.radio("太陽能預估模式", ["🤖 API 短波輻射精準推算", "✋ 廠務手動強制設定"], help="歐洲 ECMWF 模式會抓取短波輻射(W/m²)來無視薄雲干擾。")
    if solar_mode == "✋ 廠務手動強制設定":
        manual_solar = st.slider("手動設定巔峰太陽能 (kW)", min_value=0.0, max_value=SOLAR_MAX_KW, value=80.0, step=1.0)
    
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

# --- 3. 氣象抓取與假日判定 ---
today_str = now_dt.strftime("%Y-%m-%d")
tmr_dt = now_dt + timedelta(days=1)
tmr_str = tmr_dt.strftime("%Y-%m-%d")

TAIWAN_HOLIDAYS_2026 = ["2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-27", "2026-04-03", "2026-04-04", "2026-04-06", "2026-05-01", "2026-06-19", "2026-09-25", "2026-09-28", "2026-10-09", "2026-10-26", "2026-12-25"]
today_is_holiday = now_dt.weekday() >= 5 or today_str in TAIWAN_HOLIDAYS_2026
tmr_is_holiday = tmr_dt.weekday() >= 5 or tmr_str in TAIWAN_HOLIDAYS_2026

@st.cache_data(ttl=300) 
def get_dual_weather():
    fetch_time = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
    res_dict = {"fetch_time": fetch_time, "cwa": {"status": "🔴", "wx": "未知", "cloud": 0, "rad": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_cloud": 30, "tmr_rad": 400}, "owm": {"status": "🔴", "wx": "未知", "cloud": 0, "rad": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_cloud": 30, "tmr_rad": 400, "today_hourly": {}, "hourly": {}}}
    
    today_prefix = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    tmr_prefix = (datetime.now(TW_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        lat, lon = "23.936537", "120.697917"
        om_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,weather_code,shortwave_radiation&hourly=temperature_2m,cloud_cover,weather_code,shortwave_radiation&timezone=Asia%2FTaipei&models=ecmwf_ifs"
        r = requests.get(om_url, timeout=5).json()
        res_dict["owm"]["status"] = "🟢"
        res_dict["owm"]["wx"] = wmo_to_text(r['current']['weather_code'])
        res_dict["owm"]["cloud"] = r['current']['cloud_cover']
        res_dict["owm"]["rad"] = r['current']['shortwave_radiation']
        res_dict["owm"]["temp"] = r['current']['temperature_2m']
        
        target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
        times_list = r['hourly']['time']
        
        for hour in target_hours:
            t_str_today = f"{today_prefix}T{hour}"
            if t_str_today in times_list:
                idx = times_list.index(t_str_today)
                res_dict["owm"]["today_hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "cloud": r['hourly']['cloud_cover'][idx], "rad": r['hourly']['shortwave_radiation'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
                
        for hour in target_hours:
            t_str_tmr = f"{tmr_prefix}T{hour}"
            if t_str_tmr in times_list:
                idx = times_list.index(t_str_tmr)
                res_dict["owm"]["hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "cloud": r['hourly']['cloud_cover'][idx], "rad": r['hourly']['shortwave_radiation'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
        
        try:
            tmr_temps = [r['hourly']['temperature_2m'][times_list.index(f"{tmr_prefix}T{h}:00")] for h in range(12, 16)]
            res_dict["owm"]["tmr_temp"] = max(tmr_temps)
        except:
            res_dict["owm"]["tmr_temp"] = res_dict["owm"]["hourly"].get("12:00", {}).get("temp", 28.0)
            
        try:
            tmr_clouds = [r['hourly']['cloud_cover'][times_list.index(f"{tmr_prefix}T{h:02d}:00")] for h in range(8, 17, 2)]
            res_dict["owm"]["tmr_cloud"] = int(sum(tmr_clouds) / len(tmr_clouds))
            tmr_rads = [r['hourly']['shortwave_radiation'][times_list.index(f"{tmr_prefix}T{h:02d}:00")] for h in range(8, 17, 2)]
            res_dict["owm"]["tmr_rad"] = int(sum(tmr_rads) / len(tmr_rads))
        except:
            res_dict["owm"]["tmr_cloud"] = res_dict["owm"]["cloud"]
            res_dict["owm"]["tmr_rad"] = res_dict["owm"]["rad"]
    except: pass
    return res_dict

w = get_dual_weather()
sel = w["owm"] if "歐洲" in primary_brain and w["owm"]["status"] == "🟢" else w["cwa"]
cloud, temp, tmr_temp = sel.get("cloud",0), sel.get("temp",25), sel.get("tmr_temp",25)
current_rad = sel.get("rad", 0)

with st.sidebar:
    st.markdown(f"<div style='color: #666; font-size: 14px; margin-top: 10px;'>⏱️ 氣象大腦最後同步：<br><b>{w['fetch_time']}</b></div>", unsafe_allow_html=True)

# --- 4. 【V2.41 重構】找出真實「最嚴苛時段」的台電需量 ---
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

# 這是全日最顛峰的絕對負載 (僅供步驟一展示用)
final_predicted_demand = tmr_true_base_load + tmr_actual_load_growth + tmr_temp_penalty - tmr_shaved_kw

# [核心破解] 逐時段交戰模擬，尋找真正的「最高需量點」
max_net_grid_demand = 0.0
worst_hour = "未知"
worst_hour_load = 0.0
worst_hour_solar = 0.0

if "🟢" in w["owm"]["status"] and w["owm"]["hourly"]:
    target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
    max_rad_tmr = max([w["owm"]["hourly"][h]["rad"] for h in target_hours if h in w["owm"]["hourly"]] + [1])
    
    for h in target_hours:
        if h in w["owm"]["hourly"]:
            h_temp = w["owm"]["hourly"][h]['temp']
            h_rad = w["owm"]["hourly"][h]['rad']
            
            if tmr_is_holiday:
                h_load = 160.0
            else:
                h_load = tmr_true_base_load + tmr_actual_load_growth + max(0, (h_temp - 25.0) * 5.5) - tmr_shaved_kw
            
            if solar_mode == "🤖 API 短波輻射精準推算":
                h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0)
            else:
                weight = h_rad / max_rad_tmr if max_rad_tmr > 0 else 0
                h_solar = min(manual_solar, manual_solar * weight)
            
            h_net = h_load - h_solar
            
            # 抓出最慘烈的那一小時！
            if h_net > max_net_grid_demand:
                max_net_grid_demand = h_net
                worst_hour = h
                worst_hour_load = h_load
                worst_hour_solar = h_solar
else:
    # 預防 API 失敗的保守備案
    max_net_grid_demand = final_predicted_demand - (SOLAR_MAX_KW * 0.5)
    worst_hour = "14:00 (預設)"

# 以「最慘需量」作為儲備防禦的唯一標準
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
st.title("❄️ 中創園區空調聯防：H300行動戰情室 V2.41")

if tmr_is_holiday:
    action_msg = f"🎉 假日停機警報：明日 ({tmr_str}) 為休息日/補假！請【暫停今晚儲冰】，並手動解除排程。"
elif suggested_ice_hrs <= 2:
    action_msg = f"🟢 明日最危險時段需量 {max_net_grid_demand:.1f} kW，電力餘裕充足，執行例行儲冰即可。"
elif suggested_ice_hrs <= 5:
    action_msg = f"🟡 明日最危險時段需量 {max_net_grid_demand:.1f} kW 逼近警戒！需補充 70% 封印缺口，請加強儲冰。"
else:
    action_msg = f"🔴 警告：明日危險時段需量暴增至 {max_net_grid_demand:.1f} kW！嚴防午後超約，務必長時間儲冰！"

st.markdown("### 🔔 健維哥-空調核心指令 (今晚任務)")
c_action, c_metrics = st.columns([1.2, 1])
with c_action:
    border_color = "#17a2b8" if tmr_is_holiday else ("#28a745" if suggested_ice_hrs <= 2 else "#ffc107" if suggested_ice_hrs <= 5 else "#dc3545")
    st.markdown(f"""
        <div class="ice-card" style="border: 4px solid {border_color};">
            <div style="font-size: 24px; color: #666; font-weight: bold; margin-bottom: 10px;">建議今晚儲冰時間</div>
            <div><span class="ice-value">{suggested_ice_hrs:.1f}</span><span class="ice-unit">小時</span></div>
        </div>
        """, unsafe_allow_html=True)

with c_metrics:
    st.markdown(f"""
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px 15px; height: 100%; align-content: center;">
            <div><div style="font-size: 15px; color: #555;">目前園區氣溫</div><div style="font-size: 45px; font-weight: 700; color: #2c3e50;">{temp} <span style="font-size: 20px;">°C</span></div></div>
            <div><div style="font-size: 15px; color: #555;">目前短波輻射強度</div><div style="font-size: 45px; font-weight: 700; color: #d35400;">{current_rad} <span style="font-size: 20px;">W/m²</span></div></div>
            <div><div style="font-size: 15px; color: #555;">明日預測最高溫</div><div style="font-size: 45px; font-weight: 700; color: #2c3e50;">{tmr_temp} <span style="font-size: 20px;">°C</span></div></div>
            <div><div style="font-size: 15px; color: #555;">最危險時段 ({worst_hour})</div><div style="font-size: 45px; font-weight: 700; color: #dc3545;">{max_net_grid_demand:.1f} <span style="font-size: 20px;">kW</span></div><div style="font-size: 13px; color:#dc3545;">↑ 真實最大防禦挑戰</div></div>
        </div>
    """, unsafe_allow_html=True)

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

today_ice_rest = chiller_compensation if 1 <= current_month <= 5 else 0.0
today_base_load = base_load_historical + today_ice_rest
today_actual_load = 70.0 * (occupancy_rate / 100.0)
today_shaved_kw = MAG_CHILLER_RT * (1.0 - MAG_CAP_LIMIT) * MAG_EFF

if "🟢" in w["owm"]["status"] and w["owm"]["today_hourly"]:
    h_cols_today = st.columns(5)
    target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
    max_rad_today_real = max([w["owm"]["today_hourly"][h]["rad"] for h in target_hours if h in w["owm"]["today_hourly"]] + [1])

    for i, h in enumerate(target_hours):
        with h_cols_today[i]:
            st.markdown(f"<div style='text-align:center; font-size:18px; font-weight:bold; color:#17a2b8;'>⏰ {h}</div>", unsafe_allow_html=True)
            if h in w["owm"]["today_hourly"]:
                h_data = w["owm"]["today_hourly"][h]
                h_temp, h_rad = h_data['temp'], h_data['rad']
                
                if today_is_holiday:
                    h_load = 160.0
                else:
                    h_temp_penalty = max(0, (h_temp - 25.0) * 5.5)
                    h_load = today_base_load + today_actual_load + h_temp_penalty - today_shaved_kw
                
                if solar_mode == "🤖 API 短波輻射精準推算":
                    h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0)
                else:
                    weight = h_rad / max_rad_today_real if max_rad_today_real > 0 else 0
                    h_solar = min(manual_solar, manual_solar * weight)
                
                h_net = h_load - h_solar
                card_color = "#dc3545" if h_net > CONTRACT_LIMIT - 15 else ("#ffc107" if h_net > CONTRACT_LIMIT - 50 else "#28a745")
                
                st.write(f"🌤️ {h_data['wx']}")
                st.write(f"🌡️ {h_temp} °C | ☀️ {h_rad} W/m²")
                st.markdown(f"""
                    <div class="hourly-card-today" style="border-left-color: {card_color};">
                        <div style="font-size:13px; color:#555;">🏭 總負載: {h_load:.1f}</div>
                        <div style="font-size:13px; color:#28a745;">🌞 太陽能: -{h_solar:.1f}</div>
                        <hr style="margin:6px 0; border: 0.5px solid #b8daff;">
                        <div style="font-size:16px; font-weight:bold; color:{card_color};">⚡ 需量: {h_net:.0f} kW</div>
                    </div>
                """, unsafe_allow_html=True)
            else: 
                st.write("資料擷取中...")

st.markdown("---")
st.subheader(f"🎯 明日關鍵時段預報追蹤 ({tmr_str} 儲冰防禦準備)")
if "🟢" in w["owm"]["status"] and w["owm"]["hourly"]:
    h_cols = st.columns(5)
    max_rad_tmr = max([w["owm"]["hourly"][h]["rad"] for h in target_hours if h in w["owm"]["hourly"]] + [1])

    for i, h in enumerate(target_hours):
        with h_cols[i]:
            st.markdown(f"<div style='text-align:center; font-size:18px; font-weight:bold; color:#1E3A8A;'>⏰ {h}</div>", unsafe_allow_html=True)
            if h in w["owm"]["hourly"]:
                h_data = w["owm"]["hourly"][h]
                h_temp, h_rad = h_data['temp'], h_data['rad']
                
                if tmr_is_holiday:
                    h_load = 160.0
                else:
                    h_temp_penalty = max(0, (h_temp - 25.0) * 5.5)
                    h_load = tmr_true_base_load + tmr_actual_load_growth + h_temp_penalty - tmr_shaved_kw
                
                if solar_mode == "🤖 API 短波輻射精準推算":
                    h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0)
                else:
                    weight = h_rad / max_rad_tmr if max_rad_tmr > 0 else 0
                    h_solar = min(manual_solar, manual_solar * weight)
                
                h_net = h_load - h_solar
                card_color = "#dc3545" if h_net > CONTRACT_LIMIT - 15 else ("#ffc107" if h_net > CONTRACT_LIMIT - 50 else "#28a745")
                
                st.write(f"🌤️ {h_data['wx']}")
                st.write(f"🌡️ {h_temp} °C | ☀️ {h_rad} W/m²")
                st.markdown(f"""
                    <div class="hourly-card" style="border-left: 4px solid {card_color};">
                        <div style="font-size:13px; color:#555;">🏭 總負載: {h_load:.1f}</div>
                        <div style="font-size:13px; color:#28a745;">🌞 太陽能: -{h_solar:.1f}</div>
                        <hr style="margin:6px 0; border: 0.5px solid #ddd;">
                        <div style="font-size:16px; font-weight:bold; color:{card_color};">⚡ 需量: {h_net:.0f} kW</div>
                    </div>
                """, unsafe_allow_html=True)
            else: 
                st.write("資料擷取中...")

st.markdown("---")
st.subheader("📊 明日防禦決策基準：聚焦最嚴苛時段 (破除鴨子曲線陷阱)")

st.markdown("**▶ 步驟一：園區建築物絕對最高耗能推算 (忽視太陽能)**")
c1, c2, c3, c4 = st.columns(4)
if tmr_is_holiday:
    c1.metric("非上班日基礎負載", f"{tmr_true_base_load:.1f} kW", "實測假日基本待機用電", delta_color="off")
    c2.metric("📈 動態與高溫加載", f"+0.0 kW", "假日無辦公空調需求")
else:
    c1.metric("歷史基礎與動態加載", f"{tmr_true_base_load + tmr_actual_load_growth:.1f} kW", f"依進駐率 {occupancy_rate}% 計算", delta_color="off")
    c2.metric("🌡️ 高溫熱負荷加載", f"+{tmr_temp_penalty:.1f} kW", f"預測高溫 {tmr_temp}°C")
c3.metric("🛡️ 磁浮 70% 封印降載", f"-{tmr_shaved_kw:.1f} kW", "硬體限制省下需量", delta_color="normal")
c4.metric("🔥 園區絕對最高負載", f"{final_predicted_demand:.1f} kW", "冷氣全開的物理極限", delta_color="off")

# 【V2.41 核心改造】只對決「最嚴苛時段」，不再用假象的顛峰太陽能抵扣
st.markdown(f"**▶ 步驟二：對決台電契約容量 (鎖定最危險的 {worst_hour} 進行真實防禦)**")
c5, c6, c7, c8 = st.columns(4)
c5.metric(f"🔥 {worst_hour} 預估負載", f"{worst_hour_load:.1f} kW", "該時段之建築耗能", delta_color="off")
c6.metric(f"📉 {worst_hour} 太陽能殘值", f"-{worst_hour_solar:.1f} kW", "太陽偏西或雲層遮蔽後之發電量", delta_color="normal")
c7.metric("⚡ 真實最高台電需量", f"{max_net_grid_demand:.1f} kW", "作為儲冰防禦的唯一標準", delta_color="inverse")
c8.metric("🛑 契約警戒線", f"{CONTRACT_LIMIT} kW", f"{season_tag}模式")

st.markdown(f"系統運行中 | 氣象大腦同步時間：{w['fetch_time']} | 設備參數：BCU-1(儲冰主機) & IB-1(2500RT-HR)")
