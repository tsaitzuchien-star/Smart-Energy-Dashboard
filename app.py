import streamlit as st
import requests
import urllib3
from datetime import datetime, timedelta, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 強制鎖定台灣時區 (UTC+8)
TW_TZ = timezone(timedelta(hours=8))

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="中創園區空調聯防戰情室 V2.13", page_icon="❄️", layout="wide")

st.markdown("""
    <style>
    .ice-card { background-color: white; padding: 15px; border-radius: 15px; text-align: center; box-shadow: 2px 2px 10px rgba(0,0,0,0.05); }
    .ice-value { font-size: 70px; font-weight: 900; color: #1f77b4; line-height: 1.1; }
    .ice-unit { font-size: 24px; color: #555; font-weight: bold; }
    .action-call { background-color: #1E3A8A; color: white; padding: 15px; border-radius: 10px; font-size: 24px; font-weight: bold; text-align: center; margin-top: 15px; }
    .schedule-box { padding: 20px; border-radius: 10px; border: 2px dashed #4682B4; background-color: #F0F8FF; font-size: 20px;}
    .schedule-time { font-size: 32px; font-weight: bold; color: #D2691E; }
    </style>
    """, unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ 系統與營運參數")
    primary_brain = st.radio("大腦決策來源", ["🇩🇪 國際開源氣象 (園區座標)", "🇹🇼 台灣氣象署 (南投縣)"])
    st.markdown("---")
    st.header("☁️ 現場即時校正")
    cloud_emergency = st.toggle("🔴 啟動【突發雲湧】防禦模式", value=False)
    st.markdown("---")
    st.header("🕹️ 展示模式")
    use_manual = st.checkbox("✅ 啟用模擬拉桿", value=False)
    manual_cloud = st.slider("模擬雲量 (%)", 0, 100, 20, disabled=not use_manual)
    manual_temp = st.slider("模擬氣溫 (°C)", 15, 40, 28, disabled=not use_manual)

# --- 2. 參數與台電規則 ---
SOLAR_MAX_KW, MAG_MAX_KW = 146.0, 141.8
current_month = datetime.now(TW_TZ).month
CONTRACT_LIMIT, season_tag = (452.0, "夏月") if 6 <= current_month <= 9 else (516.0, "非夏月")

historical_max_demand = {1: 274, 2: 262, 3: 286, 4: 366, 5: 362, 6: 365, 7: 530, 8: 504, 9: 428, 10: 460, 11: 500, 12: 394}
base_load_historical = historical_max_demand.get(current_month, 400)
actual_load_growth = 70.0  # 全勤滿載防禦

def wmo_to_text(wmo):
    if wmo == 0: return "晴朗"
    elif wmo in [1, 2]: return "多雲"
    elif wmo == 3: return "陰天"
    elif 50 <= wmo <= 69: return "降雨"
    elif 80 <= wmo <= 82: return "陣雨"
    elif wmo >= 95: return "雷陣雨"
    return "未知"

# --- 3. 氣象抓取 ---
@st.cache_data(ttl=300)
def get_dual_weather():
    res_dict = {"cwa": {"status": "🔴", "wx": "未知", "cloud": 0, "temp": 25.0, "humidity": "--", "tmr_temp": 25.0},
                "owm": {"status": "🔴", "wx": "未知", "cloud": 0, "temp": 25.0, "humidity": "--", "tmr_temp": 25.0, "hourly": {}}}
    
    tmr_prefix = (datetime.now(TW_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        lat, lon = "23.936537", "120.697917"
        om_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,cloud_cover,weather_code&hourly=temperature_2m,cloud_cover,weather_code&timezone=Asia%2FTaipei"
        r = requests.get(om_url, timeout=5).json()
        res_dict["owm"] = {
            "status": "🟢", 
            "wx": wmo_to_text(r['current']['weather_code']), 
            "cloud": r['current']['cloud_cover'], 
            "temp": r['current']['temperature_2m'], 
            "humidity": r['current']['relative_humidity_2m'],
            "hourly": {}
        }
        
        target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
        times_list = r['hourly']['time']
        for hour in target_hours:
            t_str = f"{tmr_prefix}T{hour}"
            if t_str in times_list:
                idx = times_list.index(t_str)
                res_dict["owm"]["hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "cloud": r['hourly']['cloud_cover'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
        
        try:
            tmr_temps = [r['hourly']['temperature_2m'][times_list.index(f"{tmr_prefix}T{h}:00")] for h in range(12, 16)]
            res_dict["owm"]["tmr_temp"] = max(tmr_temps)
        except:
            res_dict["owm"]["tmr_temp"] = res_dict["owm"]["hourly"].get("12:00", {}).get("temp", 28.0)
    except: pass
    
    try:
        cwa_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization=CWA-3DD5DB13-517F-4C53-8A1C-0D2FB1595975&locationName=南投縣"
        r = requests.get(cwa_url, verify=False, timeout=5).json()
        wx = r['records']['location'][0]['weatherElement'][0]['time'][0]['parameter']['parameterName']
        res_dict["cwa"] = {"status": "🟢", "wx": wx, "cloud": 30 if "晴" in wx else 70, "temp": 25.0, "humidity": "--", "tmr_temp": 28.0}
    except: pass
        
    return res_dict

w = get_dual_weather()
sel = w["owm"] if "國際" in primary_brain and w["owm"]["status"] == "🟢" else w["cwa"]

if use_manual:
    cloud, temp, tmr_temp, humidity = manual_cloud, manual_temp, manual_temp, 60
else:
    cloud, temp, tmr_temp, humidity = sel["cloud"], sel["temp"], sel["tmr_temp"], sel.get("humidity", "--")

# --- 4. 大腦運算 ---
temp_penalty = max(0, (tmr_temp - 25.0) * 5.5)
final_predicted_demand = base_load_historical + actual_load_growth + temp_penalty
solar_eff = 0.95 if cloud < 15 else 0.60 if cloud < 40 else 0.30 if cloud < 75 else 0.15
est_solar = SOLAR_MAX_KW * solar_eff
safe_margin = CONTRACT_LIMIT - final_predicted_demand + est_solar
suggested_ice_hrs = max(1.5, min(9.0, ((2500 - (max(0, min(MAG_MAX_KW, safe_margin)) / MAG_MAX_KW * 240 * 9)) * 1.2 / 2500 * 9)))

end_minutes = 6 * 60 + 30
start_minutes = int(end_minutes - (suggested_ice_hrs * 60))
if start_minutes < 0: start_minutes += 24 * 60
start_h, start_m = start_minutes // 60, start_minutes % 60
start_time_str = f"{start_h:02d}:{start_m:02d}"
end_time_str = "06:30"

# --- 5. 渲染 UI ---
st.title("❄️ 中創園區空調聯防：H300行動戰情室 V2.13")
st.markdown("### 🔔 健維哥-空調核心指令 (今晚任務)")

# 橫幅觀測列
st.markdown(f"""
    <div style="display: flex; align-items: center; justify-content: space-between; background-color: #f8f9fa; padding: 15px 30px; border-radius: 12px; border: 1px solid #e9ecef; border-left: 6px solid #17a2b8; margin-bottom: 25px; box-shadow: 2px 2px 10px rgba(0,0,0,0.02);">
        <div style="font-size: 16px; color: #555; font-weight: bold;">📍 目前園區即時微氣候觀測</div>
        <div style="font-size: 20px; font-weight: bold; color: #1f2937; letter-spacing: 1px;">
            🌡️ {temp}°C &nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp; 💧 {humidity}% &nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp; ☁️ {cloud}%
        </div>
    </div>
""", unsafe_allow_html=True)

# 【終極排版】採用 1:1:1 絕對均分佈局
c_action, c_temp, c_solar = st.columns(3)

with c_action:
    border_color = "#28a745" if suggested_ice_hrs <= 2 else "#ffc107" if suggested_ice_hrs <= 4 else "#dc3545"
    st.markdown(f"""
        <div class="ice-card" style="border: 4px solid {border_color};">
            <div style="font-size: 18px; color: #666; font-weight: bold; margin-bottom: 5px;">建議今晚儲冰時間</div>
            <div>
                <span class="ice-value">{suggested_ice_hrs:.1f}</span>
                <span class="ice-unit">小時</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

with c_temp:
    st.metric("明日預測最高溫 (防禦基準)", f"{tmr_temp} °C", delta=f"{tmr_temp-25:.1f} °C (高溫熱負荷啟動)", delta_color="inverse")

with c_solar:
    st.metric("明日太陽能發電估值", f"{est_solar:.1f} kW", delta=f"依據 {cloud}% 雲量計算 (保底值)")

action_msg = "🟢 電力餘裕充足，執行例行儲冰即可。" if suggested_ice_hrs <= 2 else "🟡 預計明日高溫或多雲，請確實檢查儲冰系統運作。" if suggested_ice_hrs <= 4 else "🔴 警告：明日負載極高，務必完成長時間儲冰，嚴防超約！"
st.markdown(f'<div class="action-call">{action_msg}</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
st.markdown("### 📝 中央監控系統 (儲融冰) 排程設定建議")
sc1, sc2 = st.columns(2)
with sc1:
    st.markdown(f"""
        <div class="schedule-box">
            <b>❄️ 夜間製冰排程 (Ice Storage)</b><br><br>
            啟動：<span class="schedule-time">{start_time_str}</span><br>
            停止：<span class="schedule-time">{end_time_str}</span><br><br>
            <span style="font-size:16px; color:#666;">*已鎖定清晨最低溫時段，最大化主機冷凝效率。</span>
        </div>
    """, unsafe_allow_html=True)
with sc2:
    st.markdown(f"""
        <div class="schedule-box">
            <b>💧 日間融冰排程 (Ice Melting)</b><br><br>
            啟動：<span class="schedule-time">10:00</span><br>
            停止：<span class="schedule-time">16:00</span><br><br>
            <span style="font-size:16px; color:#666;">*精準覆蓋台電尖峰時段與建築物最高熱負載期。</span>
        </div>
    """, unsafe_allow_html=True)

st.markdown("---")
st.subheader("🎯 明日關鍵時段預報追蹤")
if "🟢" in w["owm"]["status"] and w["owm"]["hourly"]:
    h_cols = st.columns(5)
    target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
    for i, h in enumerate(target_hours):
        with h_cols[i]:
            st.markdown(f"**⏰ {h}**")
            if h in w["owm"]["hourly"]:
                h_data = w["owm"]["hourly"][h]
                st.write(f"🌤️ {h_data['wx']}")
                st.write(f"🌡️ **{h_data['temp']} °C**")
                st.progress(h_data['cloud'] / 100, text=f"☁️ 雲量 {h_data['cloud']}%")
            else: st.write("資料擷取中...")

st.markdown("---")
st.subheader("📊 決策基礎數據分析")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("歷史基礎負載", f"{base_load_historical:.1f} kW")
c2.metric("📈 擴編動態加載", f"+{actual_load_growth:.1f} kW", "全勤滿載計算", delta_color="inverse")
c3.metric("🌡️ 溫度加載", f"+{temp_penalty:.1f} kW")
c4.metric("🔥 最終預測負載", f"{final_predicted_demand:.1f} kW", "總和預估", delta_color="off")
c5.metric("⚡ 契約警戒線", f"{CONTRACT_LIMIT} kW", f"{season_tag}模式")

st.markdown(f"系統運行中 | 資料更新：{datetime.now(TW_TZ).strftime('%H:%M:%S')} | 座標鎖定：23.9365, 120.6979")
