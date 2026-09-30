import streamlit as st
from datetime import datetime, timedelta

from calendar_tw import load_calendar
from forecast import (
    ForecastInputs, compute_forecast,
    MAG_CHILLER_RT, MAG_CAP_LIMIT, MAG_EFF, SOLAR_MAX_KW,
)
from weather import fetch_smart_weather, TARGET_HOURS, TW_TZ

# --- 1. 網頁基本設定（必須是第一個 Streamlit 指令）---
st.set_page_config(page_title="中創園區契約容量暨空調聯防 V3.9.5", page_icon="❄️", layout="wide")
# --- 0. 戰情室機密登入防護 ---
def check_password():
    """驗證密碼，若正確才允許顯示後續內容"""
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if not st.session_state["password_correct"]:
        st.markdown("<br><br><h2 style='text-align: center; color: #1E3A8A; margin-bottom: 20px;'>🔒 中創園區空調戰情室</h2>", unsafe_allow_html=True)
        
        with st.form("login_form"):
            pwd = st.text_input("請輸入H300專屬授權碼：", type="password", placeholder="請輸入密碼...")
            submit_btn = st.form_submit_button("✅ 確認登入", use_container_width=True)
            
        if submit_btn:
            if pwd == "ASCH300!":
                st.session_state["password_correct"] = True
                st.rerun()
            else:
                st.error("❌ 授權碼錯誤，請重新輸入。")
        return False
    return True

if not check_password():
    st.stop()
st.markdown("""
    <style>
    .ice-card { background-color: white; border-radius: 15px; text-align: center; box-shadow: 2px 2px 10px rgba(0,0,0,0.05); display: flex; flex-direction: column; justify-content: center; min-height: 320px; }
    .ice-value { font-size: 115px; font-weight: 900; color: #1f77b4; line-height: 1.0; }
    .ice-unit { font-size: 32px; color: #555; font-weight: bold; margin-left: 8px; }
    .ice-date { font-size: 18px; color: #888; margin-bottom: 10px; font-weight: bold; }
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
target_hours = TARGET_HOURS
now_dt = datetime.now(TW_TZ)
tmr_dt = now_dt + timedelta(days=1)
today_str = now_dt.strftime("%Y-%m-%d")
tmr_str = tmr_dt.strftime("%Y-%m-%d")
week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
display_date_full = f"{now_dt.strftime('%Y/%m/%d')} {week_list[now_dt.weekday()]}"

with st.sidebar:
    st.info("📡 V3.9.5：兵推防禦回歸 + 18:00動態卸載引擎")
    
    st.header("📅 明日場地租借 (首要確認)")
    st.markdown("<div style='font-size:13px; color:#666; margin-bottom:10px;'>同仁請優先確認此項。系統會自動依據平假日與租借時長，精算最省錢的冰水防禦戰略。</div>", unsafe_allow_html=True)
    
    conf_hall_status = st.selectbox("國際會議廳明日活動", ["無活動 (0 RT-HR)", "半天租借 (+75 RT-HR)", "全天租借 (+150 RT-HR)"])
    expo_hall_status = st.selectbox("展演大廳明日活動", ["無活動 (0 RT-HR)", "半天租借 (+125 RT-HR)", "全天租借 (+250 RT-HR)"])
    
    st.markdown("---")
    st.header("⚙️ 系統與營運參數")
    
    st.header("🏢 動態負載微調")
    occupancy_rate = st.slider("今日園區預估進駐率 (%)", min_value=0, max_value=100, value=100, step=5)
    
    overtime_status = st.radio("今日廠商加班預測", [
        "🌇 18:00 準時下班 (啟動夜間降載)", 
        "🌙 19:30 晚間加班 (維持基礎供應)"
    ])
    
    chiller_compensation = st.number_input("預估磁浮主機平均耗電 (kW)", min_value=0.0, max_value=140.0, value=100.0, step=5.0)
    
    st.markdown("---")
    st.header("🌞 太陽能預測校正")
    solar_mode = st.radio("太陽能預估模式", ["🤖 API 短波輻射精準推算", "✋ 廠務手動強制設定"])
    if solar_mode == "✋ 廠務手動強制設定":
        manual_solar = st.slider("手動設定巔峰太陽能 (kW)", min_value=0.0, max_value=SOLAR_MAX_KW, value=80.0, step=1.0)
    else: manual_solar = 80.0
    
    st.markdown("---")
    st.header("🎛️ 隱藏空調主機負載 (G11, GB1, GB2)")
    st.markdown("<div style='font-size:13px; color:#666; margin-bottom:10px;'>A136 獨立挑高空間熱力學極限</div>", unsafe_allow_html=True)
    ahu_mode = st.radio("預測模式", ["🤖 溫控動態演算 (Auto)", "✋ 手動固定基載"])
    if ahu_mode == "✋ 手動固定基載":
        hidden_ahu_load = st.slider("預估隱藏 AHU 耗電 (kW)", min_value=0.0, max_value=50.0, value=23.0, step=1.0)
    else:
        st.success("已啟用空間熱力學與排程連動演算")
        hidden_ahu_load = 23.0 
    
    # ==========================================
    # 🚨 危機處理：緊急降載沙盤推演 (從 V3.2.2 完美加回)
    # ==========================================
    st.markdown("---")
    st.header("🚨 危機處理：緊急降載沙盤推演")
    st.markdown("<div style='font-size:13px; color:#dc3545; font-weight:bold; margin-bottom:10px;'>當預估需量暴增時，向主管展示降載成效。</div>", unsafe_allow_html=True)
    emergency_mode = st.toggle("🔴 啟動緊急防禦模式 (兵推)", value=False)
    
    if emergency_mode:
        emergency_mag_limit_pct = st.slider("強制封印磁浮主機上限 (%)", min_value=30, max_value=70, value=int(MAG_CAP_LIMIT*100), step=5, help="藉由犧牲部分冷度，換取巨大的需量空間")
        emergency_ahu_drop = st.slider("強迫 AHU 提溫降載 (kW)", min_value=0.0, max_value=37.0, value=20.0, step=1.0, help="模擬現場將 G11, GB1, GB2 溫度調高2度所省下的耗電")
    else:
        emergency_mag_limit_pct = int(MAG_CAP_LIMIT * 100) 
        emergency_ahu_drop = 0.0
    
    active_mag_limit = emergency_mag_limit_pct / 100.0

    st.markdown("---")
    if st.button("🔄 強制同步最新氣象", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# --- 2. 台灣行事曆（含補班）---
@st.cache_data(ttl=86400)
def get_calendar(years):
    return load_calendar(years)

cal = get_calendar(tuple(sorted({now_dt.year, tmr_dt.year})))
today_is_holiday = cal.is_holiday(now_dt.date())
tmr_is_holiday = cal.is_holiday(tmr_dt.date())

# --- 3. 智慧氣象抓取（邏輯在 weather.py）---
@st.cache_data(ttl=300)
def get_smart_weather():
    try:
        vc_key = st.secrets["VC_API_KEY"] if "VC_API_KEY" in st.secrets else None
    except Exception:
        vc_key = None
    return fetch_smart_weather(vc_key=vc_key)

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

# --- 4. 決策大腦運算（邏輯在 forecast.py，auto_log.py 共用）---
inp = ForecastInputs(
    conf_hall_status=conf_hall_status, expo_hall_status=expo_hall_status,
    occupancy_rate=occupancy_rate, overtime_status=overtime_status,
    chiller_compensation=chiller_compensation,
    solar_mode=solar_mode, manual_solar=manual_solar,
    ahu_mode=ahu_mode, hidden_ahu_load=hidden_ahu_load,
    emergency_mode=emergency_mode, emergency_mag_limit_pct=emergency_mag_limit_pct,
    emergency_ahu_drop=emergency_ahu_drop,
)
fc = compute_forecast(w, inp, now_dt, today_is_holiday, tmr_is_holiday)

is_summer_today, is_summer_tmr = fc["is_summer_today"], fc["is_summer_tmr"]
season_tag = fc["season_tag"]
calc_today, calc_tmr = fc["calc_today"], fc["calc_tmr"]
today_max_net, today_worst_hour = fc["today_max_net"], fc["today_worst_hour"]
max_net_grid_demand, worst_hour = fc["max_net_grid_demand"], fc["worst_hour"]
worst_hour_load, worst_hour_solar = fc["worst_hour_load"], fc["worst_hour_solar"]
worst_limit_tmr, est_solar = fc["worst_limit_tmr"], fc["est_solar"]
event_ice_rthr, event_kw = fc["event_ice_rthr"], fc["event_kw"]
tmr_true_base_load = fc["tmr_true_base_load"]
tmr_actual_load_growth = fc["tmr_actual_load_growth"]
tmr_shaved_kw = fc["tmr_shaved_kw"]
is_pure_holiday, is_holiday_event = fc["is_pure_holiday"], fc["is_holiday_event"]
suggested_ice_hrs = fc["suggested_ice_hrs"]
start_time_str, end_time_str, time_color = fc["start_time_str"], fc["end_time_str"], fc["time_color"]
melt_start, melt_end, melt_memo = fc["melt_start"], fc["melt_end"], fc["melt_memo"]

# --- 5. 渲染 UI ---
st.title("❄️ 中創園區契約容量暨空調聯防：H300行動戰情室 V3.9.5")
for _msg in cal.warnings:
    st.warning(_msg)

# [V3.9.5 加回] 兵推模式警告標語
if emergency_mode:
    saved_kw_total = (MAG_CHILLER_RT * (MAG_CAP_LIMIT - active_mag_limit) * MAG_EFF) + emergency_ahu_drop
    st.markdown(f"<div style='background-color:#fff3cd; color:#856404; padding:12px; border-radius:8px; border-left: 6px solid #ffc107; font-size:18px; margin-bottom:15px; font-weight:bold;'>🚨 兵推模式運作中：已強制介入系統參數，預估可為園區緊急省下 {saved_kw_total:.1f} kW 的救命需量空間！</div>", unsafe_allow_html=True)

if w["status_code"] == 1: st.markdown("<div class='status-banner-ecmwf'>📡 系統狀態：🟢 雙源比對引擎啟動 (V3.9.5 極限防禦運算中)</div>", unsafe_allow_html=True)
elif w["status_code"] == 2: st.markdown("<div class='status-banner-vc'>📡 系統狀態：🟡 ECMWF 遭遇壅塞，已無縫啟動 VC 企業備援</div>", unsafe_allow_html=True)
else: st.markdown("<div class='status-banner-fail'>📡 系統狀態：🔴 雙氣象源皆斷線 (已切換至保守盲估模式)</div>", unsafe_allow_html=True)

if is_pure_holiday: action_msg = f"🎉 假日停機警報：明日 ({tmr_str}) 為休息日！請【暫停今晚儲冰】，並手動解除排程。"
elif is_holiday_event: action_msg = f"💡 省錢防禦啟動：明日為假日活動。白天電價極低，建議【暫停今晚儲冰】，明日直接啟動磁浮主機最划算！"
elif suggested_ice_hrs <= 2: action_msg = f"🟢 預估明日最高需量 {max_net_grid_demand:.1f} kW，電力餘裕充足，執行例行儲冰即可。"
elif suggested_ice_hrs <= 5: action_msg = f"🟡 預估明日最高需量 {max_net_grid_demand:.1f} kW 逼近警戒！請加強儲冰。"
else: action_msg = f"🔴 警告：明日危險時段需量暴增至 {max_net_grid_demand:.1f} kW！嚴防超約，務必長時間儲冰！"

st.markdown("### 🔔 空調團隊-空調核心指令 (今晚任務)")
c_action, c_metrics = st.columns([1.2, 1])
with c_action:
    border_color = "#17a2b8" if is_pure_holiday else ("#28a745" if suggested_ice_hrs <= 2 or is_holiday_event else "#ffc107" if suggested_ice_hrs <= 5 else "#dc3545")
    st.markdown(f"""<div class="ice-card" style="border: 4px solid {border_color};"><div style="font-size: 28px; color: #666; font-weight: bold; margin-bottom: 5px;">建議今晚儲冰時間</div><div class="ice-date">{display_date_full}</div><div><span class="ice-value">{suggested_ice_hrs:.1f}</span><span class="ice-unit">小時</span></div></div>""", unsafe_allow_html=True)

with c_metrics:
    cal_text = "<span style='font-size:12px; color:#d35400; margin-left:5px;'>(高溫動態校正)</span>" if w.get("temp_is_calibrated") else ""
    st.markdown(f"""<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px 15px; min-height: 320px; align-content: center;"><div><div style="font-size: 15px; color: #555;">目前園區氣溫{cal_text}</div><div style="font-size: 38px; font-weight: 700; color: #2c3e50;">{temp} <span style="font-size: 16px;">°C</span></div></div><div><div style="font-size: 15px; color: #555;">明日預測最高溫</div><div style="font-size: 38px; font-weight: 700; color: #2c3e50;">{tmr_temp} <span style="font-size: 16px;">°C</span></div></div><div><div style="font-size: 15px; color: #555;">目前短波輻射強度</div><div style="font-size: 38px; font-weight: 700; color: #d35400;">{current_rad} <span style="font-size: 16px;">W/m²</span></div></div><div><div style="font-size: 15px; color: #555;">明日平均太陽能</div><div style="font-size: 38px; font-weight: 700; color: #2c3e50;">{est_solar:.1f} <span style="font-size: 16px;">kW</span></div></div><div style="background: #f0f8ff; padding: 10px 15px; border-radius: 8px; border-left: 4px solid #17a2b8;"><div style="font-size: 14px; color: #555; font-weight: bold;">今日最危險 ({today_worst_hour})</div><div style="font-size: 38px; font-weight: 900; color: #17a2b8;">{today_max_net:.1f} <span style="font-size: 16px;">kW</span></div></div><div style="background: #ffeaea; padding: 10px 15px; border-radius: 8px; border-left: 4px solid #dc3545;"><div style="font-size: 14px; color: #555; font-weight: bold;">明日最危險 ({worst_hour})</div><div style="font-size: 38px; font-weight: 900; color: #dc3545;">{max_net_grid_demand:.1f} <span style="font-size: 16px;">kW</span></div></div></div>""", unsafe_allow_html=True)

st.markdown(f'<div class="action-call" style="background-color: {"#17a2b8" if is_pure_holiday else "#28a745" if is_holiday_event else "#1E3A8A"};">{action_msg}</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
st.subheader("📝 中央監控系統 (儲融冰) 排程設定建議")

sc1, sc2, sc3 = st.columns(3)

with sc1:
    memo_1 = "*明日為純假日，無需儲備。" if is_pure_holiday else ("*假日離峰，暫停儲冰。" if is_holiday_event else "*最晚於 07:00 結束，避開早晨需量尖峰。")
    st.markdown(f"""<div class="schedule-box"><b>❄️ 夜間儲冰排程</b><br><br>啟動：<span class="schedule-time" style="color:{time_color};">{start_time_str}</span><br>停止：<span class="schedule-time" style="color:{time_color};">{end_time_str}</span><br><br><span style="font-size:16px; color:#666;">{memo_1}</span></div>""", unsafe_allow_html=True)

with sc2:
    st.markdown(f"""<div class="schedule-box"><b>💧 日間融冰排程</b><br><br>啟動：<span class="schedule-time" style="color:{time_color};">{melt_start}</span><br>停止：<span class="schedule-time" style="color:{time_color};">{melt_end}</span><br><br><span style="font-size:16px; color:#666;">{melt_memo}</span></div>""", unsafe_allow_html=True)

with sc3:
    if is_pure_holiday or is_holiday_event:
        chiller_morn = "維持現狀"
        chiller_aft = "維持現狀"
        chiller_color_m = "#666"
        chiller_color_a = "#666"
        chiller_memo = "*明日為假日，依現場需求調度或維持關機。"
    else:
        chiller_morn = "上限 70%"
        chiller_aft = "降載 50%"
        chiller_color_m = "#28a745" 
        chiller_color_a = "#dc3545" 
        chiller_memo = "*BMS連動前，請併入廠務每日例行巡檢執行。"
        
    st.markdown(f"""<div class="schedule-box"><b>🎛️ 磁浮主機 (人工設定)</b><br><br>08:00：<span class="schedule-time" style="font-size: 28px; color:{chiller_color_m};">{chiller_morn}</span><br>15:50：<span class="schedule-time" style="font-size: 28px; color:{chiller_color_a};">{chiller_aft}</span><br><br><span style="font-size:16px; color:#666;">{chiller_memo}</span></div>""", unsafe_allow_html=True)


st.markdown("---")
st.subheader(f"⚡ 今日關鍵時段即時追蹤 ({today_str} 現場比對專用)")
if api_is_online:
    h_cols_today = st.columns(len(target_hours))
    for i, h in enumerate(target_hours):
        with h_cols_today[i]:
            header_text = f"⏰ {h}" if not (is_summer_today and 16 <= int(h[:2]) < 22) else f"⚠️ {h} (夜尖峰)"
            st.markdown(f"<div style='text-align:center; font-size:18px; font-weight:bold; color:#17a2b8;'>{header_text}</div>", unsafe_allow_html=True)
            if h in calc_today:
                d = calc_today[h]
                card_color = "#dc3545" if d['h_net'] > d['current_limit'] - 15 else ("#ffc107" if d['h_net'] > d['current_limit'] - 50 else "#28a745")
                st.write(f"🌤️ {d['wx']}<br>🌡️ {d['temp']} °C | ☀️ {d['rad']} W/m²", unsafe_allow_html=True)
                
                cloud_html = f"<div>☁️ 雲分布 (低/中/高)</div><div style='font-weight:bold;'>{d['c_low']}% / {d['c_mid']}% / {d['c_high']}%</div>" if w["status_code"]==1 else f"<div>☁️ 雲分布 (總雲量)</div><div style='font-weight:bold;'>{d['c_low']}%</div>"
                if d['cp'] < 1.0 and solar_mode == "🤖 API 短波輻射精準推算": cloud_html += f"<div style='color:#e74c3c; font-size:11px; font-weight:bold; margin-top:3px; background:#ffeaea; border-radius:4px;'>🌩️ 雲層衰減: -{int((1-d['cp'])*100)}%</div>"
                if d['shading_factor'] < 1.0: cloud_html += f"<div style='color:#0c5460; font-size:11px; font-weight:bold; margin-top:3px; background:#d1ecf1; border-radius:4px;'>🌧️ 遮蔽冷卻卸載: -{int((1-d['shading_factor'])*100)}%</div>"
                
                st.markdown(f"""<div class="hourly-card-today" style="border-left: 4px solid {card_color};"><div class="cloud-badge">{cloud_html}</div><div style="font-size:13px; color:#555;">🏭 總負載: {d['h_load']:.1f}</div><div style="font-size:13px; color:#28a745;">🌞 太陽能: -{d['h_solar']:.1f}</div><div style="height:1px; background-color:#b8daff; margin:2px 0;"></div><div style="font-size:16px; font-weight:bold; color:{card_color};">⚡ 需量: {d['h_net']:.0f} kW</div></div>""", unsafe_allow_html=True)
            else: st.write("資料擷取中...")
else: st.warning("📡 API 暫時斷線。")

st.markdown("---")
st.subheader(f"🎯 明日關鍵時段預報追蹤 ({tmr_str} 儲冰防禦準備)")
if api_is_online:
    h_cols = st.columns(len(target_hours))
    for i, h in enumerate(target_hours):
        with h_cols[i]:
            header_text = f"⏰ {h}" if not (is_summer_tmr and 16 <= int(h[:2]) < 22) else f"⚠️ {h} (夜尖峰)"
            st.markdown(f"<div style='text-align:center; font-size:18px; font-weight:bold; color:#1E3A8A;'>{header_text}</div>", unsafe_allow_html=True)
            if h in calc_tmr:
                d = calc_tmr[h]
                card_color = "#dc3545" if d['h_net'] > d['current_limit'] - 15 else ("#ffc107" if d['h_net'] > d['current_limit'] - 50 else "#28a745")
                st.write(f"🌤️ {d['wx']}<br>🌡️ {d['temp']} °C | ☀️ {d['rad']} W/m²", unsafe_allow_html=True)
                
                cloud_html = f"<div>☁️ 雲分布 (低/中/高)</div><div style='font-weight:bold;'>{d['c_low']}% / {d['c_mid']}% / {d['c_high']}%</div>" if w["status_code"]==1 else f"<div>☁️ 雲分布 (總雲量)</div><div style='font-weight:bold;'>{d['c_low']}%</div>"
                if d['cp'] < 1.0 and solar_mode == "🤖 API 短波輻射精準推算": cloud_html += f"<div style='color:#e74c3c; font-size:11px; font-weight:bold; margin-top:3px; background:#ffeaea; border-radius:4px;'>🌩️ 雲層衰減: -{int((1-d['cp'])*100)}%</div>"
                if d['shading_factor'] < 1.0: cloud_html += f"<div style='color:#0c5460; font-size:11px; font-weight:bold; margin-top:3px; background:#d1ecf1; border-radius:4px;'>🌧️ 遮蔽冷卻卸載: -{int((1-d['shading_factor'])*100)}%</div>"
                
                st.markdown(f"""<div class="hourly-card" style="border-left: 4px solid {card_color};"><div class="cloud-badge">{cloud_html}</div><div style="font-size:13px; color:#555;">🏭 總負載: {d['h_load']:.1f}</div><div style="font-size:13px; color:#28a745;">🌞 太陽能: -{d['h_solar']:.1f}</div><div style="height:1px; background-color:#ddd; margin:2px 0;"></div><div style="font-size:16px; font-weight:bold; color:{card_color};">⚡ 需量: {d['h_net']:.0f} kW</div></div>""", unsafe_allow_html=True)
            else: st.write("資料擷取中...")
else: st.warning("📡 API 暫時斷線。")

st.markdown("---")
st.subheader("📊 明日防禦決策基準：聚焦最嚴苛時段")
c1, c2, c3, c4 = st.columns(4)

if tmr_is_holiday:
    c1.metric("非上班日基礎負載", "160.0 kW", "實測假日基本待機用電", delta_color="off")
    c2.metric("📈 假日活動空調加載", f"+{event_kw:.1f} kW", f"磁浮直供 ({event_ice_rthr} RT-HR)" if event_kw > 0 else "無租借活動", delta_color="off" if event_kw == 0 else "normal")
    c4.metric("🔥 園區絕對最高負載", f"{(160.0 + event_kw):.1f} kW", "假日基本送風+活動耗電" if event_kw > 0 else "假日安全負載", delta_color="off")
    c3.metric("🛡️ 磁浮降載防禦", "-0.0 kW", "假日未達主機上限無需降載", delta_color="off")
else:
    c1.metric("歷史基礎與進駐加載", f"{tmr_true_base_load + tmr_actual_load_growth:.1f} kW", f"進駐率 {occupancy_rate}%", delta_color="off")
    c2.metric("🌡️ 空調熱力與慣性加載", f"+{(worst_hour_load + tmr_shaved_kw - tmr_true_base_load - tmr_actual_load_growth):.1f} kW", f"包含 V3.9.5 遮蔽與下班卸載", delta_color="off")
    c4.metric("🔥 園區最嚴苛總負載", f"{worst_hour_load + tmr_shaved_kw:.1f} kW", "加上防禦前的物理極限", delta_color="off")
    
    # [V3.9.5 更新] 兵推模式標籤動態切換
    mag_txt = f"兵推強制降載 ({emergency_mag_limit_pct}%)" if emergency_mode else f"硬體限制省下需量 ({int(MAG_CAP_LIMIT*100)}%)"
    c3.metric(f"🛡️ 磁浮 {emergency_mag_limit_pct if emergency_mode else int(MAG_CAP_LIMIT*100)}% 封印降載", f"-{tmr_shaved_kw:.1f} kW", mag_txt, delta_color="normal")

c5, c6, c7, c8 = st.columns(4)
c5.metric(f"🔥 {worst_hour} 防禦後負載", f"{worst_hour_load:.1f} kW", "該時段之真實耗能", delta_color="off")
c6.metric(f"📉 {worst_hour} 太陽能殘值", f"-{worst_hour_solar:.1f} kW", "太陽偏西或雲層遮蔽後之發電量", delta_color="normal")
c7.metric("⚡ 真實最高台電需量", f"{max_net_grid_demand:.1f} kW", "作為儲備防禦的最高標準", delta_color="inverse")
c8.metric("🛑 該時段警戒線", f"{worst_limit_tmr} kW", f"{season_tag}動態防禦", delta_color="off")

st.markdown("---")
st.markdown(f"<div style='text-align: center; color: #666;'>系統運行中 | 氣象更新時間：{w['fetch_time']} | 設備參數：CHU-2(磁浮冰機) & BCU-1(儲冰主機) & IB-1(2500RT-HR) & AHU-G11 & AHU-GB1 & AHU-GB2 </div>", unsafe_allow_html=True)
