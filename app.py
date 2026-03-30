import streamlit as st
import requests
import urllib3
from datetime import datetime, timedelta

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="中創園區空調聯防戰情室 V2.6", page_icon="❄️", layout="wide")

# 自定義 CSS
st.markdown("""
    <style>
    .ice-card {
        background-color: #f0f2f6;
        padding: 30px;
        border-radius: 15px;
        border-left: 10px solid #007bff;
        text-align: center;
    }
    .ice-value {
        font-size: 80px !important;
        font-weight: 800;
        color: #1f77b4;
        line-height: 1;
    }
    .ice-unit {
        font-size: 30px;
        color: #555;
    }
    .action-call {
        background-color: #1E3A8A;
        color: white;
        padding: 15px;
        border-radius: 10px;
        font-size: 24px;
        font-weight: bold;
        text-align: center;
        margin-top: 20px;
    }
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
current_month = datetime.now().month
CONTRACT_LIMIT, season_tag = (452.0, "夏月") if 6 <= current_month <= 9 else (516.0, "非夏月")

historical_max_demand = {1: 274, 2: 262, 3: 286, 4: 366, 5: 362, 6: 365, 7: 530, 8: 504, 9: 428, 10: 460, 11: 500, 12: 394}
base_load_historical = historical_max_demand.get(current_month, 400)

# 【V2.6 修正】老闆指示：先不打折，直接以 70kW 滿載計算最終預測負載
actual_load_growth = 70.0 

# 國際氣象碼轉譯函數
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
    res_dict = {"cwa": {"status": "🔴", "wx": "未知", "cloud": 0, "temp": 25.0, "tmr_temp": 25.0},
                "owm": {"status": "🔴", "wx": "未知", "cloud": 0, "temp": 25.0, "tmr_temp": 25.0, "hourly": {}}}
    
    tmr_prefix = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    # 德國 Open-Meteo (國際站 - 高解析度)
    try:
        lat, lon = "23.936537", "120.697917"
        om_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,weather_code&hourly=temperature_2m,cloud_cover,weather_code&timezone=Asia%2FTaipei"
        r = requests.get(om_url, timeout=5).json()
        
        res_dict["owm"] = {
            "status": "🟢", 
            "wx": wmo_to_text(r['current']['weather_code']), 
            "cloud": r['current']['cloud_cover'], 
            "temp": r['current']['temperature_2m'],
            "hourly": {}
        }
        
        # 抓取老闆指定的 5 個黃金時段
        target_hours = ["08:00", "10:00", "12:00", "14:00", "16:00"]
        times_list = r['hourly']['time']
        
        for hour in target_hours:
            t_str = f"{tmr_prefix}T{hour}"
            if t_str in times_list:
                idx = times_list.index(t_str)
                res_dict["owm"]["hourly"][hour] = {
                    "temp": r['hourly']['temperature_2m'][idx],
                    "cloud": r['hourly']['cloud_cover'][idx],
                    "wx": wmo_to_text(r['hourly']['weather_code'][idx])
                }
        
        # 明日最高溫代表 (取 12:00 到 15:00 的最大值)
        try:
            tmr_temps = [r['hourly']['temperature_2m'][times_list.index(f"{tmr_prefix}T{h}:00")] for h in range(12, 16)]
            res_dict["owm"]["tmr_temp"] = max(tmr_temps)
        except:
            res_dict["owm"]["tmr_temp"] = res_dict["owm"]["hourly"].get("12:00", {}).get("temp", 28.0)
            
    except Exception as e: 
        pass
    
    # 台灣氣象署 (CWA)
    try:
        cwa_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization=CWA-3DD5DB13-517F-4C53-8A1C-0D2FB1595975&locationName=南投縣"
        r = requests.get(cwa_url, verify=False, timeout=5).json()
        wx = r['records']['location'][0]['weatherElement'][0]['time'][0]['parameter']['parameterName']
        res_dict["cwa"] = {"status": "🟢", "wx": wx, "cloud": 30 if "晴" in wx else 70, "temp": 25.0, "tmr_temp": 28.0}
    except: 
        pass
        
    return res_dict

w = get_dual_weather()

# 決策選擇
sel = w["owm"] if "國際" in primary_brain and w["owm"]["status"] == "🟢" else w["cwa"]
cloud, temp, tmr_temp = (manual_cloud, manual_temp, manual_temp) if use_manual else (sel["cloud"], sel["temp"], sel["tmr_temp"])

# --- 4. 大腦運算 ---
temp_penalty = max(0, (tmr_temp - 25.0) * 5.5)

# 終極預測負載：歷史基準 + 擴編 70kW(滿載) + 明日高溫熱負荷
final_predicted_demand = base_load_historical + actual_load_growth + temp_penalty

solar_eff = 0.95 if cloud < 15 else 0.60 if cloud < 40 else 0.30 if cloud < 75 else 0.15
est_solar = SOLAR_MAX_KW * solar_eff

safe_margin = CONTRACT_LIMIT - final_predicted_demand + est_solar
usable_mag = max(0, min(MAG_MAX_KW, safe_margin))
suggested_ice_hrs = max(1.5, min(9.0, ((2500 - (usable_mag / MAG_MAX_KW * 240 * 9)) * 1.2 / 2500 * 9)))

# --- 5. 渲染 UI ---
st.title("❄️ 中創園區空調聯防：同仁行動戰情室 V2.6")

# 重點區：建議儲冰時間
st.markdown("### 🔔 空調同仁核心指令 (今晚任務)")
col_main, col_info = st.columns([2, 1])

with col_main:
    border_color = "#28a745" if suggested_ice_hrs <= 2 else "#ffc107" if suggested_ice_hrs <= 4 else "#dc3545"
    st.markdown(f"""
        <div style="background-color: white; padding: 40px; border-radius: 20px; border: 5px solid {border_color}; text-align: center; box-shadow: 10px 10px 20px rgba(0,0,0,0.1);">
            <p style="font-size: 30px; margin-bottom: 0px; color: #666;">建議今晚儲冰時間</p>
            <span class="ice-value">{suggested_ice_hrs:.1f}</span>
            <span class="ice-unit"> 小時</span>
        </div>
        """, unsafe_allow_html=True)

with col_info:
    st.metric("明日預測最高溫 (防禦基準)", f"{tmr_temp} °C", delta=f"{tmr_temp-25:.1f} °C (高溫熱負荷啟動)", delta_color="inverse")
    st.metric("明日太陽能發電估值", f"{est_solar:.1f} kW", delta=f"依據 {cloud}% 雲量計算 (保底值)")

action_msg = "🟢 電力餘裕充足，執行例行儲冰即可。" if suggested_ice_hrs <= 2 else "🟡 預計明日高溫或多雲，請確實檢查儲冰系統運作。" if suggested_ice_hrs <= 4 else "🔴 警告：明日負載極高，務必完成長時間儲冰，嚴防超約！"
st.markdown(f'<div class="action-call">{action_msg}</div>', unsafe_allow_html=True)

st.markdown("---")

# 【V2.6 新增】老闆專屬：明日關鍵時段預報追蹤
st.subheader("🎯 明日關鍵時段預報追蹤 (觀測驗證區)")
st.caption("以下數據由「🇩🇪 德國衛星高解析度微氣候模型」提供，精確鎖定園區 GPS 座標。方便明早進行預報準確度驗證。")

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
                
                # 雲量進度條 (視覺化)
                c_val = h_data['cloud']
                st.progress(c_val / 100, text=f"☁️ 雲量 {c_val}%")
            else:
                st.write("資料擷取中...")
else:
    st.warning("目前國際衛星資料連線中斷，無法顯示逐小時預報。")

st.markdown("---")

# 次要資訊：負載拆解
st.subheader("📊 決策基礎數據分析")
c1, c2, c3, c4 = st.columns(4)
c1.metric("歷史基礎負載", f"{base_load_historical:.1f} kW")
# 明確標示不打折
c2.metric("📈 擴編動態加載", f"+{actual_load_growth:.1f} kW", "全勤滿載計算 (最壞劇本防禦)", delta_color="inverse")
c3.metric("溫度加載負載", f"+{temp_penalty:.1f} kW")
c4.metric("契約警戒線", f"{CONTRACT_LIMIT} kW", f"{season_tag}模式")

# 底部狀態小燈
st.markdown(f"系統運行中 | 資料更新：{datetime.now().strftime('%H:%M:%S')} | 座標鎖定：23.9365, 120.6979")
