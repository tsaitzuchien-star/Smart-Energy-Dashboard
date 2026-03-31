import streamlit as st
import requests
import urllib3
from datetime import datetime, timedelta, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 強制鎖定台灣時區 (UTC+8)
TW_TZ = timezone(timedelta(hours=8))

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="中創園區空調聯防戰情室 V2.26", page_icon="❄️", layout="wide")

st.markdown("""
    <style>
    .ice-card { 
        background-color: white; 
        padding: 40px 20px; 
        border-radius: 15px; 
        text-align: center; 
        box-shadow: 2px 2px 10px rgba(0,0,0,0.05); 
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }
    .ice-value { font-size: 85px; font-weight: 900; color: #1f77b4; line-height: 1.1; }
    .ice-unit { font-size: 28px; color: #555; font-weight: bold; }
    .action-call { background-color: #1E3A8A; color: white; padding: 15px; border-radius: 10px; font-size: 24px; font-weight: bold; text-align: center; margin-top: 15px; }
    .schedule-box { padding: 20px; border-radius: 10px; border: 2px dashed #4682B4; background-color: #F0F8FF; font-size: 20px;}
    .schedule-time { font-size: 32px; font-weight: bold; color: #D2691E; }
    </style>
    """, unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ 系統與營運參數")
    primary_brain = st.radio("大腦決策來源", ["🇩🇪 國際開源氣象 (園區座標)", "🇹🇼 台灣氣象署 (南投縣)"])
    st.markdown("---")
    st.header("🔄 資料同步控制")
    if st.button("🔄 強制同步最新氣象", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# --- 2. 參數與原廠硬體規格 ---
ICE_CHILLER_KW = 241.0       
ICE_CHILLER_CAP_RT = 242.5   
ICE_BANK_MAX_RTHR = 2500.0   
MAG_EFF = 0.7                
SOLAR_MAX_KW = 146.0         
now_dt = datetime.now(TW_TZ)
current_month = now_dt.month
CONTRACT_LIMIT, season_tag = (452.0, "夏月") if 6 <= current_month <= 9 else (516.0, "非夏月")

historical_max_demand = {1: 274, 2: 262, 3: 286, 4: 366, 5: 362, 6: 365, 7: 530, 8: 504, 9: 428, 10: 460, 11: 500, 12: 394}
base_load_historical = historical_max_demand.get(current_month, 400)
ice_restoration_kw = 60.0 if 1 <= current_month <= 5 else 0.0
true_base_load = base_load_historical + ice_restoration_kw
actual_load_growth = 70.0  

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
    fetch_time = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
    res_dict = {"fetch_time": fetch_time, "cwa": {"status": "🔴", "wx": "未知", "cloud": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_cloud": 30}, "owm": {"status": "🔴", "wx": "未知", "cloud": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_cloud": 30, "hourly": {}}}
    tmr_prefix = (datetime.now(TW_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        lat, lon = "23.936537", "120.697917"
        om_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,weather_code&hourly=temperature_2m,cloud_cover,weather_code&timezone=Asia%2FTaipei"
        r = requests.get(om_url, timeout=5).json()
        res_dict["owm"]["status"] = "🟢"
        res_dict["owm"]["wx"] = wmo_to_text(r['current']['weather_code'])
        res_dict["owm"]["cloud"] = r['current']['cloud_cover']
        res_dict["owm"]["temp"] = r['current']['temperature_2m']
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
        try:
            tmr_clouds = [r['hourly']['cloud_cover'][times_list.index(f"{tmr_prefix}T{h:02d}:00")] for h in range(8, 17, 2)]
            res_dict["owm"]["tmr_cloud"] = int(sum(tmr_clouds) / len(tmr_clouds))
        except:
            res_dict["owm"]["tmr_cloud"] = res_dict["owm"]["cloud"]
    except: pass
    try:
        cwa_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization=CWA-3DD5DB13-517F-4C53-8A1C-0D2FB1595975&locationName=南投縣"
        r = requests.get(cwa_url, verify=False, timeout=5).json()
        wx = r['records']['location'][0]['weatherElement'][0]['time'][0]['parameter']['parameterName']
        res_dict["cwa"]["status"] = "🟢"
        res_dict["cwa"]["wx"] = wx
        res_dict["cwa"]["cloud"] = 30 if "晴" in wx else 70
        res_dict["cwa"]["tmr_cloud"] = res_dict["cwa"]["cloud"]
    except: pass
    return res_dict

w = get_dual_weather()
sel = w["owm"] if "國際" in primary_brain and w["owm"]["status"] == "🟢" else w["cwa"]
cloud, temp, tmr_temp, tmr_cloud = sel["cloud"], sel["temp"], sel["tmr_temp"], sel["tmr_cloud"]

with st.sidebar:
    st.markdown(f"<div style='color: #666; font-size: 14px; margin-top: 10px;'>⏱️ 氣象大腦最後同步：<br><b>{w['fetch_time']}</b></div>", unsafe_allow_html=True)

# --- 4. 大腦精準運算 ---
temp_penalty = max(0, (tmr_temp - 25.0) * 5.5)
final_predicted_demand = true_base_load + actual_load_growth + temp_penalty
solar_eff = 0.95 if tmr_cloud < 15 else 0.60 if tmr_cloud < 40 else 0.30 if tmr_cloud < 75 else 0.15
est_solar = SOLAR_MAX_KW * solar_eff

buffer = 15.0
demand_gap = final_predicted_demand - est_solar - (CONTRACT_LIMIT - buffer)

if demand_gap > 0:
    needed_ice_rt = demand_gap / MAG_EFF
    needed_ice_rthr = needed_ice_rt * 6.0 
    suggested_ice_hrs = (needed_ice_rthr * 1.2) / ICE_CHILLER_CAP_RT
else:
    suggested_ice_hrs = 1.5 

suggested_ice_hrs = max(1.5, min(9.0, suggested_ice_hrs))

end_minutes = 7 * 60 
start_minutes = int(end_minutes - (suggested_ice_hrs * 60))
if start_minutes < 0: start_minutes += 24 * 60
start_time_str = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}"
end_time_str = "07:00"

# --- 5. 渲染 UI ---
st.title("❄️ 中創園區空調聯防：H300行動戰情室 V2.26")
st.markdown("### 🔔 健維哥-空調核心指令 (今晚任務)")

c_action, c_metrics = st.columns([1.2, 1])
with c_action:
    border_color = "#28a745" if suggested_ice_hrs <= 2 else "#ffc107" if suggested_ice_hrs <= 4 else "#dc3545"
    st.markdown(f"""<div class="ice-card" style="border: 4px solid {border_color};"><div style="font-size: 24px; color: #666; font-weight: bold; margin-bottom: 10px;">建議今晚儲冰時間</div><div><span class="ice-value">{suggested_ice_hrs:.1f}</span><span class="ice-unit">小時</span></div></div>""", unsafe_allow_html=True)

with c_metrics:
    st.markdown(f"""<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px 15px; height: 100%; align-content: center;"><div><div style="font-size: 15px; color: #555; margin-bottom: 4px;">目前園區氣溫</div><div style="font-size: 45px; font-weight: 700; color: #2c3e50; line-height: 1.1;">{temp} <span style="font-size: 20px; color: #555;">°C</span></div><div style="display: inline-block; background: #f0f2f6; color: #666; padding: 2px 8px; border-radius: 10px; font-size: 13px; margin-top: 6px;">↑ 即時微氣候觀測</div></div><div><div style="font-size: 15px; color: #555; margin-bottom: 4px;">目前園區雲量</div><div style="font-size: 45px; font-weight: 700; color: #2c3e50; line-height: 1.1;">{cloud} <span style="font-size: 20px; color: #555;">%</span></div><div style="display: inline-block; background: #f0f2f6; color: #666; padding: 2px 8px; border-radius: 10px; font-size: 13px; margin-top: 6px;">↑ 影響現在發電</div></div><div><div style="font-size: 15px; color: #555; margin-bottom: 4px;">明日預測最高溫 (防禦基準)</div><div style="font-size: 45px; font-weight: 700; color: #2c3e50; line-height: 1.1;">{tmr_temp} <span style="font-size: 20px; color: #555;">°C</span></div><div style="display: inline-block; background: #ffeaea; color: #dc3545; padding: 2px 8px; border-radius: 10px; font-size: 13px; margin-top: 6px;">↑ {tmr_temp-25:.1f} °C (高溫熱負荷)</div></div><div><div style="font-size: 15px; color: #555; margin-bottom: 4px;">明日太陽能發電估值</div><div style="font-size: 45px; font-weight: 700; color: #2c3e50; line-height: 1.1;">{est_solar:.1f} <span style="font-size: 20px; color: #555;">kW</span></div><div style="display: inline-block; background: #e6f4ea; color: #28a745; padding: 2px 8px; border-radius: 10px; font-size: 13px; margin-top: 6px;">↑ 依據明日 {tmr_cloud}% 雲量計算</div></div></div>""", unsafe_allow_html=True)

action_msg = "🟢 電力餘裕充足，執行例行儲冰即可。" if suggested_ice_hrs <= 2 else "🟡 預計明日高溫或多雲，請確實檢查儲冰系統運作。" if suggested_ice_hrs <= 4 else "🔴 警告：明日負載極高，務必完成長時間儲冰，嚴防超約！"
st.markdown(f'<div class="action-call">{action_msg}</div>', unsafe_allow_html=True)

st.markdown("<br>### 📝 中央監控系統 (儲融冰) 排程設定建議", unsafe_allow_html=True)
sc1, sc2 = st.columns(2)
with sc1:
    st.markdown(f"""<div class="schedule-box"><b>❄️ 夜間製冰排程 (Ice Storage)</b><br><br>啟動：<span class="schedule-time">{start_time_str}</span><br>停止：<span class="schedule-time">{end_time_str}</span><br><br><span style="font-size:16px; color:#666;">*已優化截止時間，減少儲槽待機損耗。</span></div>""", unsafe_allow_html=True)
with sc2:
    st.markdown(f"""<div class="schedule-box"><b>💧 日間融冰排程 (Ice Melting)</b><br><br>啟動：<span class="schedule-time">10:00</span><br>停止：<span class="schedule-time">16:00</span><br><br><span style="font-size:16px; color:#666;">*依 IB-1 設計 13°C 進水條件執行。</span></div>""", unsafe_allow_html=True)

# 【V2.26 修正】把不小心刪掉的預報區塊補回來啦！
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
st.subheader("📊 明日負載預測與決策基礎")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("歷史基礎負載", f"{base_load_historical:.1f} kW")
c2.metric("📈 擴編動態加載", f"+{actual_load_growth:.1f} kW", "全勤滿載計算")
c3.metric("🌡️ 溫度加載", f"+{temp_penalty:.1f} kW")
c4.metric("🔥 明日最終預測負載", f"{final_predicted_demand:.1f} kW", f"含 {ice_restoration_kw}kW 融冰還原")
c5.metric("⚡ 契約警戒線", f"{CONTRACT_LIMIT} kW", f"{season_tag}模式")

st.markdown(f"系統運行中 | 氣象大腦同步時間：{w['fetch_time']} | 設備參數：BCU-1(儲冰主機) & IB-1(2500RT-HR)")
