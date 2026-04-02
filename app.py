import streamlit as st
import requests
import urllib3
from datetime import datetime, timedelta, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
TW_TZ = timezone(timedelta(hours=8))

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="中創園區空調聯防戰情室 V2.35", page_icon="❄️", layout="wide")

st.markdown("""
    <style>
    .ice-card { background-color: white; padding: 40px 20px; border-radius: 15px; text-align: center; box-shadow: 2px 2px 10px rgba(0,0,0,0.05); height: 100%; display: flex; flex-direction: column; justify-content: center; }
    .ice-value { font-size: 85px; font-weight: 900; color: #1f77b4; line-height: 1.1; }
    .ice-unit { font-size: 28px; color: #555; font-weight: bold; }
    .action-call { background-color: #1E3A8A; color: white; padding: 15px; border-radius: 10px; font-size: 24px; font-weight: bold; text-align: center; margin-top: 15px; }
    .schedule-box { padding: 20px; border-radius: 10px; border: 2px dashed #4682B4; background-color: #F0F8FF; font-size: 20px;}
    .schedule-time { font-size: 32px; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# --- 2. 參數與原廠硬體規格 ---
ICE_CHILLER_KW = 241.0       
ICE_CHILLER_CAP_RT = 242.5   
ICE_BANK_MAX_RTHR = 2500.0   
MAG_CHILLER_RT = 200.0       
MAG_CAP_LIMIT = 0.70         
MAG_EFF = 0.7                
SOLAR_MAX_KW = 138.0         

now_dt = datetime.now(TW_TZ)
current_month = now_dt.month
CONTRACT_LIMIT, season_tag = (452.0, "夏月") if 6 <= current_month <= 9 else (516.0, "非夏月")

# 上班日歷史高標 (含空調)
historical_max_demand = {1: 274, 2: 262, 3: 286, 4: 366, 5: 362, 6: 365, 7: 530, 8: 504, 9: 428, 10: 460, 11: 500, 12: 394}

with st.sidebar:
    st.header("⚙️ 系統與營運參數")
    primary_brain = st.radio("氣象大腦來源", ["🇪🇺 歐洲 ECMWF 輻射預測 (最準確)", "🇹🇼 台灣氣象署 (南投縣)"])
    st.markdown("---")
    st.header("🌞 太陽能預測校正")
    solar_mode = st.radio("太陽能預估模式", ["🤖 API 短波輻射精準推算", "✋ 廠務手動強制設定"])
    if solar_mode == "✋ 廠務手動強制設定":
        manual_solar = st.slider("手動設定明日太陽能 (kW)", min_value=0.0, max_value=SOLAR_MAX_KW, value=80.0, step=1.0)
    st.markdown("---")
    st.header("🏢 動態負載微調")
    occupancy_rate = st.slider("今日園區預估進駐率 (%)", min_value=0, max_value=100, value=70, step=5)
    chiller_compensation = st.number_input("預估磁浮主機平均耗電 (kW)", min_value=0.0, max_value=140.0, value=50.0, step=5.0)
    st.markdown("---")
    if st.button("🔄 強制同步最新氣象", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# --- 3. 假日判定與運算邏輯 ---
tmr_dt = now_dt + timedelta(days=1)
tmr_str = tmr_dt.strftime("%Y-%m-%d")
TAIWAN_HOLIDAYS_2026 = ["2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-27", "2026-04-03", "2026-04-06", "2026-05-01", "2026-06-19", "2026-09-25", "2026-09-28", "2026-10-09", "2026-10-26", "2026-12-25"]
is_holiday = tmr_dt.weekday() >= 5 or tmr_str in TAIWAN_HOLIDAYS_2026

@st.cache_data(ttl=300) 
def get_weather():
    lat, lon = "23.936537", "120.697917"
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,weather_code,shortwave_radiation&hourly=temperature_2m,cloud_cover,weather_code,shortwave_radiation&timezone=Asia%2FTaipei&models=ecmwf_ifs"
    r = requests.get(url, timeout=5).json()
    fetch_time = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
    
    # 擷取明日白天平均數值
    tmr_prefix = (datetime.now(TW_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    times = r['hourly']['time']
    d_idx = [i for i, t in enumerate(times) if tmr_prefix in t and "08:00" <= t.split("T")[1] <= "16:00"]
    avg_rad = sum([r['hourly']['shortwave_radiation'][i] for i in d_idx]) / len(d_idx)
    max_temp = max([r['hourly']['temperature_2m'][i] for i in d_idx])
    
    hourly_data = {}
    for h in ["08:00", "10:00", "12:00", "14:00", "16:00"]:
        t_str = f"{tmr_prefix}T{h}"
        if t_str in times:
            idx = times.index(t_str)
            hourly_data[h] = {"temp": r['hourly']['temperature_2m'][idx], "rad": r['hourly']['shortwave_radiation'][idx]}
            
    return {"fetch_time": fetch_time, "temp": r['current']['temperature_2m'], "rad": r['current']['shortwave_radiation'], "tmr_temp": max_temp, "tmr_rad": avg_rad, "hourly": hourly_data}

w = get_weather()

# --- 4. 核心負載模型校準 (V2.35 關鍵) ---
if is_holiday:
    # 假日模式：套用 160kW 實測基礎負載
    base_load = 160.0
    actual_load_growth = 0.0
    temp_penalty = 0.0
    shaved_kw_by_cap = 0.0
    load_detail_label = "非上班日實測基礎 (160kW)"
else:
    # 上班日模式
    base_load = historical_max_demand.get(current_month, 400) + (chiller_compensation if 1 <= current_month <= 5 else 0)
    actual_load_growth = 70.0 * (occupancy_rate / 100.0)
    temp_penalty = max(0, (w['tmr_temp'] - 25.0) * 5.5)
    shaved_kw_by_cap = MAG_CHILLER_RT * (1.0 - MAG_CAP_LIMIT) * MAG_EFF
    load_detail_label = f"歷史基礎與補償 ({base_load:.1f}kW)"

final_predicted_demand = base_load + actual_load_growth + temp_penalty - shaved_kw_by_cap

if solar_mode == "🤖 API 短波輻射精準推算":
    eff = 0.95 if w['tmr_rad'] >= 500 else 0.70 if w['tmr_rad'] >= 350 else 0.40 if w['tmr_rad'] >= 150 else 0.15
    est_solar = SOLAR_MAX_KW * eff
    solar_label = f"↑ 依 ECMWF 輻射 {w['tmr_rad']:.0f} W/m² 計算"
else:
    est_solar = manual_solar
    solar_label = "↑ ✋ 廠務手動強制校正"

net_grid_demand = final_predicted_demand - est_solar
suggested_ice_hrs = 0.0 if is_holiday else max(1.5, min(9.0, ((max(0, net_grid_demand - (CONTRACT_LIMIT - 15)) / MAG_EFF) * 6.0 + (MAG_CHILLER_RT * 0.3 * 4.0)) * 1.2 / ICE_CHILLER_CAP_RT))

start_time = f"{(7 * 60 - int(suggested_ice_hrs * 60)) // 60:02d}:{(7 * 60 - int(suggested_ice_hrs * 60)) % 60:02d}" if not is_holiday else "關閉排程"

# --- 5. 渲染 UI ---
st.title("❄️ 中創園區空調聯防：H300行動戰情室 V2.35")
if is_holiday:
    st.info(f"🎉 假日停機模式：明日 ({tmr_str}) 為休息日/補假。基礎負載已自動鎖定為實測之 160.0 kW。")

st.markdown("### 🔔 健維哥-空調核心指令 (今晚任務)")
c_action, c_metrics = st.columns([1.2, 1])
with c_action:
    st.markdown(f"""<div class="ice-card" style="border: 4px solid {'#17a2b8' if is_holiday else '#28a745'};"><div style="font-size: 24px; color: #666; font-weight: bold; margin-bottom: 10px;">建議今晚儲冰時間</div><div><span class="ice-value">{suggested_ice_hrs:.1f}</span><span class="ice-unit">小時</span></div></div>""", unsafe_allow_html=True)
with c_metrics:
    st.markdown(f"""<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px 15px; height: 100%; align-content: center;">
        <div><div style="font-size: 15px; color: #555;">目前園區氣溫</div><div style="font-size: 40px; font-weight: 700;">{w['temp']}°C</div></div>
        <div><div style="font-size: 15px; color: #555;">即時輻射強度</div><div style="font-size: 40px; font-weight: 700; color:#d35400;">{w['rad']:.0f}</div></div>
        <div><div style="font-size: 15px; color: #555;">明日預測最高溫</div><div style="font-size: 40px; font-weight: 700;">{w['tmr_temp']:.1f}°C</div></div>
        <div><div style="font-size: 15px; color: #555;">明日太陽能估值</div><div style="font-size: 40px; font-weight: 700; color:#28a745;">{est_solar:.1f}</div><div style="font-size: 12px; color:#28a745;">{solar_label}</div></div>
    </div>""", unsafe_allow_html=True)

st.markdown(f'<div class="action-call" style="background-color: {"#17a2b8" if is_holiday else "#1E3A8A"};">{"🟢 電力餘裕充足，執行例行儲冰即可。" if not is_holiday else "🎉 假日模式：暫停今晚製冰，請手動解除排程。"}</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
st.subheader("📝 中央監控系統 (儲融冰) 排程設定建議")
sc1, sc2 = st.columns(2)
with sc1:
    st.markdown(f"""<div class="schedule-box"><b>❄️ 夜間製冰排程</b><br><br>啟動：<span style="font-size:32px; font-weight:bold; color:#D2691E;">{start_time}</span><br>停止：<span style="font-size:32px; font-weight:bold; color:#D2691E;">{'07:00' if not is_holiday else '關閉排程'}</span><br><br><span style="font-size:14px; color:#666;">{'*依上班日硬體校準。' if not is_holiday else '*明日放假，無需製冰。'}</span></div>""", unsafe_allow_html=True)
with sc2:
    st.markdown(f"""<div class="schedule-box"><b>💧 日間融冰排程</b><br><br>啟動：<span style="font-size:32px; font-weight:bold; color:#D2691E;">{'10:00' if not is_holiday else '關閉排程'}</span><br>停止：<span style="font-size:32px; font-weight:bold; color:#D2691E;">{'16:00' if not is_holiday else '關閉排程'}</span><br><br><span style="font-size:14px; color:#666;">{'*進水 13°C 條件。' if not is_holiday else '*明日放假，手動解除排程！'}</span></div>""", unsafe_allow_html=True)

st.markdown("---")
st.subheader("📊 明日負載預測與決策基礎 (台電實切需量分析)")
c1, c2, c3, c4 = st.columns(4)
c1.metric(load_detail_label, f"{base_load:.1f} kW", f"進駐率 {occupancy_rate if not is_holiday else 0}%", delta_color="off")
c2.metric("🌡️ 預測負載變動", f"+{actual_load_growth + temp_penalty:.1f} kW", f"含擴編與高溫加載")
c3.metric("🛡️ 磁浮封印/太陽能", f"-{shaved_kw_by_cap + est_solar:.1f} kW", f"抵銷總功率")
c4.metric("⚡ 預估台電需量", f"{net_grid_demand:.1f} kW", f"對決 {CONTRACT_LIMIT}kW 契約")

st.markdown(f"系統運行中 | 氣象大腦：ECMWF 輻射模式 | 座標：23.9365, 120.6979")
