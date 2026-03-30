import streamlit as st
import requests
import urllib3
from datetime import datetime, timedelta

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="中創園區空調聯防戰情室 V2.4", page_icon="⚡", layout="wide")

with st.sidebar:
    st.header("⚙️ 系統控制")
    if st.button("🔄 重新連線雙氣象源", type="primary", use_container_width=True):
        st.cache_data.clear()
    st.markdown("---")
    
    st.header("🧠 決策大腦優先權")
    primary_brain = st.radio(
        "選擇主要氣象來源",
        ["🇩🇪 國際開源氣象 (園區精確座標)", "🇹🇼 台灣氣象署 (南投縣大範圍)"],
        label_visibility="collapsed"
    )
    
    st.markdown("---")
    st.header("🏢 營運動態參數")
    attendance_rate = st.slider("預估人員出勤率 (%)", 50, 100, 80) / 100.0

    st.markdown("---")
    st.header("☁️ 現場即時校正")
    cloud_emergency = st.toggle("🔴 啟動【突發雲湧】防禦模式", value=False)
    
    st.markdown("---")
    st.header("🕹️ 展示模式")
    use_manual = st.checkbox("✅ 啟用模擬拉桿", value=False)
    manual_cloud = st.slider("模擬雲量 (%)", 0, 100, 20, disabled=not use_manual)
    manual_temp = st.slider("模擬外部氣溫 (°C)", 15, 40, 28, disabled=not use_manual)

# --- 2. 硬體、歷史與台電參數 ---
SOLAR_MAX_KW = 146.0
MAG_MAX_KW = 141.8
current_month = datetime.now().month

# 【V2.4 核心升級】台電夏月/非夏月契約容量自動切換
if 6 <= current_month <= 9:
    CONTRACT_LIMIT = 452.0
    season_tag = "夏月"
else:
    CONTRACT_LIMIT = 516.0
    season_tag = "非夏月"

# 歷史最高需量基準
historical_max_demand = {1: 274, 2: 262, 3: 286, 4: 366, 5: 362, 6: 365, 7: 530, 8: 504, 9: 428, 10: 460, 11: 500, 12: 394}
base_load_historical = historical_max_demand.get(current_month, 400)

# 擴編滿載增加 70kW，乘上出勤率打折
MAX_BASE_LOAD_GROWTH = 70.0 
actual_load_growth = MAX_BASE_LOAD_GROWTH * attendance_rate

# 國際氣象碼轉譯函數
def wmo_to_text(wmo):
    if wmo == 0: return "晴朗"
    elif wmo in [1, 2]: return "多雲"
    elif wmo == 3: return "陰天"
    elif 50 <= wmo <= 69: return "降雨"
    elif 80 <= wmo <= 82: return "陣雨"
    elif wmo >= 95: return "雷陣雨"
    return "未知"

# --- 3. 雙氣象源深度抓取 (含外氣溫度) ---
@st.cache_data(ttl=300)
def get_dual_weather():
    results = {
        "cwa": {"status": "🔴 連線失敗", "wx": "未知", "cloud": 0, "pop": 0, "temp": 25.0, "update": "N/A", "tmr_wx": "未知", "tmr_cloud": 0, "tmr_temp": 25.0},
        "owm": {"status": "🔴 連線失敗", "wx": "未知", "cloud": 0, "pop": 0, "temp": 25.0, "update": "N/A", "tmr_wx": "未知", "tmr_cloud": 0, "tmr_temp": 25.0}
    }
    
    # A. 台灣氣象署 (CWA)
    try:
        cwa_api = "CWA-3DD5DB13-517F-4C53-8A1C-0D2FB1595975"
        cwa_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={cwa_api}&locationName=南投縣"
        res = requests.get(cwa_url, verify=False, timeout=8)
        if res.status_code == 200:
            data = res.json()
            locs = data.get('records', {}).get('location', [])
            if locs:
                elements = locs[0].get('weatherElement', [])
                for e in elements:
                    name = e.get('elementName')
                    times = e.get('time', [])
                    val_now = times[0].get('parameter', {}).get('parameterName', '').strip()
                    
                    if name == 'Wx': results['cwa']['wx'] = val_now
                    elif name == 'PoP': results['cwa']['pop'] = int(val_now) if val_now.isdigit() else 0
                    elif name == 'MaxT': results['cwa']['temp'] = float(val_now) if val_now.isdigit() else 25.0
                    
                    tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                    for t in times:
                        if tomorrow_date in t.get('startTime', '') and "06:00" in t.get('startTime', ''):
                            v = t.get('parameter', {}).get('parameterName', '').strip()
                            if name == 'Wx': results['cwa']['tmr_wx'] = v
                            elif name == 'MaxT': results['cwa']['tmr_temp'] = float(v) if v.isdigit() else 25.0

                def wx_to_cloud(wx_str):
                    if "晴" in wx_str and "雲" not in wx_str: return 10
                    elif "多雲" in wx_str and "晴" in wx_str: return 30
                    elif "多雲" in wx_str: return 60
                    elif "陰" in wx_str: return 85
                    return 50
                
                results['cwa']['cloud'] = wx_to_cloud(results['cwa']['wx'])
                results['cwa']['tmr_cloud'] = wx_to_cloud(results['cwa']['tmr_wx'])
                results['cwa']['update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                results['cwa']['status'] = "🟢 正常連線"
    except Exception:
        pass

    # B. 德國 Open-Meteo
    try:
        lat, lon = "23.936537", "120.697917"
        om_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,weather_code&hourly=temperature_2m,cloud_cover,weather_code&timezone=Asia%2FTaipei"
        res = requests.get(om_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            results['owm']['cloud'] = data['current']['cloud_cover']
            results['owm']['temp'] = data['current']['temperature_2m']
            results['owm']['wx'] = wmo_to_text(data['current']['weather_code'])
            
            tmr_prefix = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                times = data['hourly']['time']
                idx_12 = times.index(f"{tmr_prefix}T12:00")
                results['owm']['tmr_cloud'] = data['hourly']['cloud_cover'][idx_12]
                results['owm']['tmr_temp'] = data['hourly']['temperature_2m'][idx_12]
                results['owm']['tmr_wx'] = wmo_to_text(data['hourly']['weather_code'][idx_12])
            except Exception:
                pass
                
            results['owm']['update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            results['owm']['status'] = "🟢 正常連線"
    except Exception:
        pass

    return results

w_data = get_dual_weather()

# --- 決策大腦切換邏輯 ---
if use_manual:
    wx, cloud_cover, current_temp, source_tag = "模擬天氣", manual_cloud, manual_temp, "🕹️ 展示模式"
    tmr_wx, tmr_cloud, tmr_temp = "模擬明日", manual_cloud, manual_temp
elif cloud_emergency:
    wx, cloud_cover, current_temp, source_tag = "突發雲湧", 85, 30.0, "🚨 人工防禦模式"
    tmr_wx, tmr_cloud, tmr_temp = "突發雲湧延續", 85, 30.0
else:
    if "國際" in primary_brain and "🟢" in w_data['owm']['status']:
        wx, cloud_cover, current_temp = w_data['owm']['wx'], w_data['owm']['cloud'], w_data['owm']['temp']
        tmr_wx, tmr_cloud, tmr_temp = w_data['owm']['tmr_wx'], w_data['owm']['tmr_cloud'], w_data['owm']['tmr_temp']
        source_tag = "🎯 德國開源氣象 (鎖定中創園區精確座標)"
    elif "台灣" in primary_brain and "🟢" in w_data['cwa']['status']:
        wx, cloud_cover, current_temp = w_data['cwa']['wx'], w_data['cwa']['cloud'], w_data['cwa']['temp']
        tmr_wx, tmr_cloud, tmr_temp = w_data['cwa']['tmr_wx'], w_data['cwa']['tmr_cloud'], w_data['cwa']['tmr_temp']
        source_tag = "✅ 台灣氣象署 (南投縣大範圍預報)"
    else:
        wx, cloud_cover, current_temp, source_tag = "多雲", 60, 25.0, "🛡️ 離線防禦模式"
        tmr_wx, tmr_cloud, tmr_temp = "多雲", 60, 25.0

# --- 4. 大腦運算 ---
TEMP_PENALTY_KW_PER_DEGREE = 5.5
temp_penalty_kw = max(0, (tmr_temp - 25.0) * TEMP_PENALTY_KW_PER_DEGREE)
final_predicted_demand = base_load_historical + actual_load_growth + temp_penalty_kw

solar_eff = 0.95 if cloud_cover < 15 else 0.60 if cloud_cover < 40 else 0.30 if cloud_cover < 75 else 0.15
est_solar_kw = SOLAR_MAX_KW * solar_eff

# 安全餘裕動態計算
safe_margin_kw = CONTRACT_LIMIT - final_predicted_demand + est_solar_kw
usable_mag_kw = max(0, min(MAG_MAX_KW, safe_margin_kw))

suggested_ice_hrs = max(1.5, min(9.0, ((2500 - (usable_mag_kw / MAG_MAX_KW * 240 * 9)) * 1.2 / 2500 * 9)))

# --- 5. 渲染 UI ---
st.title("🏆 中創園區空調聯防戰情室 V2.4")
st.info(f"🧠 **目前大腦決策基準：** {source_tag}")

st.subheader("🌐 雙核心氣象與外氣溫度監測")
c1, c2 = st.columns(2)

with c1:
    st.markdown("### 🇹🇼 台灣氣象署 (CWA)")
    if "🟢" in w_data['cwa']['status']: st.success(w_data['cwa']['status'])
    else: st.error(w_data['cwa']['status'])
    c1_a, c1_b, c1_c = st.columns(3)
    c1_a.metric("即時天氣", w_data['cwa']['wx'])
    c1_b.metric("智能雲量", f"{w_data['cwa']['cloud']} %")
    c1_c.metric("外部氣溫", f"{w_data['cwa']['temp']} °C")

with c2:
    st.markdown("### 🇩🇪 德國衛星氣象 (Open-Meteo)")
    if "🟢" in w_data['owm']['status']: st.success(w_data['owm']['status'])
    else: st.error(w_data['owm']['status'])
    c2_a, c2_b, c2_c = st.columns(3)
    c2_a.metric("即時天氣", w_data['owm']['wx'])
    c2_b.metric("衛星雲量", f"{w_data['owm']['cloud']} %")
    c2_c.metric("精確氣溫", f"{w_data['owm']['temp']} °C")

st.markdown("---")
st.subheader("🔥 負載動態補償大腦 (精算版)")
cc1, cc2, cc3, cc4 = st.columns(4)
cc1.metric("歷史基礎負載", f"{base_load_historical} kW")

discount_text = f"{attendance_rate * 10:g}"
cc2.metric("📈 擴編動態加載", f"+{actual_load_growth:.1f} kW", f"依新增人數打 {discount_text} 折", delta_color="inverse")
cc3.metric("🌡️ 外氣溫度補償", f"+{temp_penalty_kw:.1f} kW", f"明日預估 {tmr_temp}°C", delta_color="inverse")
cc4.metric("最終預測負載", f"{final_predicted_demand:.1f} kW")

st.markdown("---")
st.subheader("⚡ 聯防大腦決策與今晚儲冰建議")
ca, cb, cc = st.columns(3)
ca.metric("明日太陽能發電估值", f"{est_solar_kw:.1f} kW", delta=f"依據 {tmr_cloud}% 雲量計算")

# 顯示自動判定的台電季節警戒線
cb.metric("系統安全餘裕電力", f"{safe_margin_kw:.1f} kW", delta=f"↑ {season_tag}警戒線: {CONTRACT_LIMIT} kW", delta_color="off")
cc.metric("建議今晚儲冰時間", f"{suggested_ice_hrs:.1f} 小時")

if safe_margin_kw < MAG_MAX_KW:
    st.error(f"🚨 警告：逼近{season_tag}契約極限！預測電力餘裕嚴重不足，請務必拉滿儲冰時數！")
else:
    st.info("🟢 餘裕充足，明日白天可優先利用太陽能直供磁浮主機。")