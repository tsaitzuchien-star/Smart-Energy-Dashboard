import streamlit as st
import requests
import urllib3
from datetime import datetime, timedelta, timezone
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 強制鎖定台灣時區 (UTC+8)
TW_TZ = timezone(timedelta(hours=8))

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="中創園區空調聯防戰情室 V2.23", page_icon="❄️", layout="wide")

st.markdown("""
    <meta http-equiv="refresh" content="300">
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
    
    st.success("🔮 **明日預測防禦基準**\n\n已鎖定：🇩🇪 德國微氣候模型")
    
    st.markdown("---")
    # 【V2.23 全新功能】專屬即時觀測的手動切換器
    current_source = st.radio(
        "📍 選擇【目前園區】即時觀測來源：", 
        ["🇹🇼 台灣氣象署 (南投縣)", "🇩🇪 德國開源氣象 (園區座標)"],
        help="您可以根據窗外實際天氣，手動選擇目前最準確的氣象來源。"
    )
    
    st.markdown("---")
    st.header("🔄 資料同步控制")
    if st.button("🔄 立即強制同步 (手動)", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.info("💡 系統已啟動【自動巡航】，每 5 分鐘會自動與雙氣象源同步數據。")

# --- 2. 參數與台電規則 ---
SOLAR_MAX_KW, MAG_MAX_KW = 146.0, 141.8
current_month = datetime.now(TW_TZ).month
CONTRACT_LIMIT, season_tag = (452.0, "夏月") if 6 <= current_month <= 9 else (516.0, "非夏月")

historical_max_demand = {1: 274, 2: 262, 3: 286, 4: 366, 5: 362, 6: 365, 7: 530, 8: 504, 9: 428, 10: 460, 11: 500, 12: 394}
base_load_historical = historical_max_demand.get(current_month, 400)
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
    
    res_dict = {"fetch_time": fetch_time,
                "cwa": {"status": "🔴", "wx": "未知", "cloud": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_cloud": 50},
                "owm": {"status": "🔴", "wx": "未知", "cloud": 0, "temp": 25.0, "tmr_temp": 25.0, "tmr_cloud": 50, "hourly": {}}}
    
    tmr_prefix = (datetime.now(TW_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        lat, lon = "23.936537", "120.697917"
        om_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,weather_code&hourly=temperature_2m,cloud_cover,weather_code&timezone=Asia%2FTaipei"
        r = requests.get(om_url, timeout=5).json()
        res_dict["owm"]["status"] = "🟢"
        res_dict["owm"]["wx"] = wmo_to_text(r['current']['weather_code'])
        res_dict["owm"]["cloud"] = r['current']['cloud_cover']
        res_dict["owm"]["temp"] = r['current']['temperature_2m']
        
        times_list = r['hourly']['time']
        target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
        for hour in target_hours:
            t_str = f"{tmr_prefix}T{hour}"
            if t_str in times_list:
                idx = times_list.index(t_str)
                res_dict["owm"]["hourly"][hour] = {"temp": r['hourly']['temperature_2m'][idx], "cloud": r['hourly']['cloud_cover'][idx], "wx": wmo_to_text(r['hourly']['weather_code'][idx])}
        
        try:
            tmr_temps = [r['hourly']['temperature_2m'][times_list.index(f"{tmr_prefix}T{h:02d}:00")] for h in range(12, 16)]
            res_dict["owm"]["tmr_temp"] = max(tmr_temps)
            
            tmr_clouds = [r['hourly']['cloud_cover'][times_list.index(f"{tmr_prefix}T{h:02d}:00")] for h in [10, 12, 14]]
            res_dict["owm"]["tmr_cloud"] = max(tmr_clouds)
        except:
            res_dict["owm"]["tmr_temp"] = res_dict["owm"]["hourly"].get("12:00", {}).get("temp", 28.0)
            res_dict["owm"]["tmr_cloud"] = res_dict["owm"]["hourly"].get("12:00", {}).get("cloud", 50)
    except: pass
    
    try:
        cwa_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization=CWA-3DD5DB13-517F-4C53-8A1C-0D2FB1595975&locationName=南投縣"
        r = requests.get(cwa_url, verify=False, timeout=5).json()
        loc = r['records']['location'][0]
        wx = loc['weatherElement'][0]['time'][0]['parameter']['parameterName']
        
        if "晴" in wx and "多雲" in wx: c_val = 30
        elif "多雲" in wx and "陰" in wx: c_val = 70
        elif "晴" in wx: c_val = 10
        elif "陰" in wx: c_val = 90
        elif "雨" in wx: c_val = 85
        else: c_val = 50
        
        min_t = float(loc['weatherElement'][2]['time'][0]['parameter']['parameterName'])
        max_t = float(loc['weatherElement'][4]['time'][0]['parameter']['parameterName'])
        curr_t = round((min_t + max_t) / 2.0, 1)

        res_dict["cwa"] = {"status": "🟢", "wx": wx, "cloud": c_val, "temp": curr_t, "tmr_temp": 28.0, "tmr_cloud": 50}
    except: pass
        
    return res_dict

w = get_dual_weather()

with st.sidebar:
    st.markdown(f"<div style='color: #666; font-size: 14px; margin-top: 10px;'>⏱️ 氣象大腦最後同步：<br><b>{w['fetch_time']}</b></div>", unsafe_allow_html=True)

# --- 4. 混血大腦運算邏輯 ---

# 4.1 即時狀態：【V2.23】根據側邊欄手動選擇
if "台灣氣象署" in current_source:
    if w["cwa"]["status"] == "🟢":
        current_cloud = w["cwa"]["cloud"]
        current_temp = w["cwa"]["temp"]
        label_current = "🇹🇼 氣象署即時觀測"
    else:
        current_cloud = w["owm"]["cloud"]
        current_temp = w["owm"]["temp"]
        label_current = "連線異常，已切換🇩🇪備援"
else:
    if w["owm"]["status"] == "🟢":
        current_cloud = w["owm"]["cloud"]
        current_temp = w["owm"]["temp"]
        label_current = "🇩🇪 德國衛星即時觀測"
    else:
        current_cloud = w["cwa"]["cloud"]
        current_temp = w["cwa"]["temp"]
        label_current = "連線異常，已切換🇹🇼備援"

# 4.2 明日預測：永遠以 🇩🇪 德國衛星防禦為主
if w["owm"]["status"] == "🟢":
    tmr_temp = w["owm"]["tmr_temp"]
    tmr_cloud = w["owm"].get("tmr_cloud", 50)
    label_forecast = "🇩🇪 德國微氣候模型"
else:
    tmr_temp = w["cwa"]["tmr_temp"]
    tmr_cloud = w["cwa"].get("tmr_cloud", 50)
    label_forecast = "🇹🇼 氣象署 (備援)"

# 使用「明日預測雲量 (tmr_cloud)」推算明日發電
temp_penalty = max(0, (tmr_temp - 25.0) * 5.5)
final_predicted_demand = base_load_historical + actual_load_growth + temp_penalty
solar_eff = 0.95 if tmr_cloud < 15 else 0.60 if tmr_cloud < 40 else 0.30 if tmr_cloud < 75 else 0.15
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
st.title("❄️ 中創園區空調聯防：H300行動戰情室 V2.23")
st.markdown("### 🔔 健維哥-空調核心指令 (今晚任務)")

c_action, c_metrics = st.columns([1.2, 1])

with c_action:
    border_color = "#28a745" if suggested_ice_hrs <= 2 else "#ffc107" if suggested_ice_hrs <= 4 else "#dc3545"
    st.markdown(f"""
        <div class="ice-card" style="border: 4px solid {border_color};">
            <div style="font-size: 24px; color: #666; font-weight: bold; margin-bottom: 10px;">建議今晚儲冰時間</div>
            <div>
                <span class="ice-value">{suggested_ice_hrs:.1f}</span>
                <span class="ice-unit">小時</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

with c_metrics:
    st.markdown(f"""
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px 15px; height: 100%; align-content: center;">
            <div>
                <div style="font-size: 15px; color: #555; margin-bottom: 4px;">目前園區氣溫</div>
                <div style="font-size: 45px; font-weight: 700; color: #2c3e50; line-height: 1.1;">{current_temp} <span style="font-size: 20px; color: #555;">°C</span></div>
                <div style="display: inline-block; background: #f0f2f6; color: #666; padding: 2px 8px; border-radius: 10px; font-size: 13px; margin-top: 6px;">↑ {label_current}</div>
            </div>
            <div>
                <div style="font-size: 15px; color: #555; margin-bottom: 4px;">目前園區雲量</div>
                <div style="font-size: 45px; font-weight: 700; color: #2c3e50; line-height: 1.1;">{current_cloud} <span style="font-size: 20px; color: #555;">%</span></div>
                <div style="display: inline-block; background: #f0f2f6; color: #666; padding: 2px 8px; border-radius: 10px; font-size: 13px; margin-top: 6px;">↑ 決定今日效率</div>
            </div>
            <div>
                <div style="font-size: 15px; color: #555; margin-bottom: 4px;">明日預測最高溫 (防禦基準)</div>
                <div style="font-size: 45px; font-weight: 700; color: #2c3e50; line-height: 1.1;">{tmr_temp} <span style="font-size: 20px; color: #555;">°C</span></div>
                <div style="display: inline-block; background: #ffeaea; color: #dc3545; padding: 2px 8px; border-radius: 10px; font-size: 13px; margin-top: 6px;">↑ {label_forecast}</div>
            </div>
            <div>
                <div style="font-size: 15px; color: #555; margin-bottom: 4px;">明日太陽能發電估值</div>
                <div style="font-size: 45px; font-weight: 700; color: #2c3e50; line-height: 1.1;">{est_solar:.1f} <span style="font-size: 20px; color: #555;">kW</span></div>
                <div style="display: inline-block; background: #e6f4ea; color: #28a745; padding: 2px 8px; border-radius: 10px; font-size: 13px; margin-top: 6px;">↑ 依據明日雲量 {int(tmr_cloud)}% 推估</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

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
st.subheader("🎯 明日關鍵時段預報追蹤 (來源: 🇩🇪 德國微氣候模型)")
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

st.markdown(f"系統運行中 | 雙引擎同步時間：{w['fetch_time']} | 座標鎖定：23.9365, 120.6979")
