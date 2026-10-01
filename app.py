import streamlit as st
from dataclasses import replace
from datetime import datetime, timedelta

import pandas as pd

from calendar_tw import load_calendar
from forecast import (
    ForecastInputs, compute_forecast,
    MAG_CHILLER_RT, MAG_CAP_LIMIT, MAG_EFF, SOLAR_MAX_KW,
    SOLAR_AUTO, SOLAR_MANUAL, AHU_AUTO, OVERTIME_ONTIME,
)
from weather import fetch_smart_weather, TARGET_HOURS, TW_TZ

# --- 1. 網頁基本設定（必須是第一個 Streamlit 指令）---
st.set_page_config(page_title="中創園區契約容量暨空調聯防", page_icon="❄️", layout="wide")


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


# --- 樣式 ---
def html(s):
    """把多行 HTML 壓成單行，避免 Streamlit markdown 把縮排／空行當成程式碼區塊。"""
    return "".join(line.strip() for line in s.strip().splitlines())


st.markdown(html("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800&family=Noto+Sans+TC:wght@400;500;700&display=swap');
html, body, [class*="css"], .stApp { font-family: 'Noto Sans TC', sans-serif; }
.block-container { padding-top: 4rem; max-width: 1440px; }
.num { font-family: 'Manrope', sans-serif; }
.hdr { display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; margin-bottom:8px; }
.hdr-l { display:flex; align-items:center; gap:16px; }
.logo { width:52px; height:52px; border-radius:14px; background:#1E4F8C; display:flex; align-items:center; justify-content:center; font-size:28px; flex-shrink:0; }
.hdr h1 { margin:0; padding:0; font-size:28px; font-weight:700; line-height:1.2; }
.sub { font-size:14px; color:#5B6875; margin-top:4px; }
.hdr-r { display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
.pill { display:inline-flex; align-items:center; gap:8px; padding:8px 14px; border-radius:999px; font-size:14px; font-weight:700; }
.dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
.card { background:#fff; border:1px solid #DDE4EC; border-radius:16px; padding:24px 28px; box-sizing:border-box; }
.card h3 { margin:0; padding:0; font-size:18px; font-weight:700; }
.card .note { font-size:13px; color:#5B6875; margin-top:2px; }
.hero { display:flex; flex-direction:column; gap:14px; }
.hero-top { display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; font-size:15px; color:#5B6875; font-weight:500; }
.big { display:flex; align-items:baseline; gap:18px; flex-wrap:wrap; }
.big .t { font-family:'Manrope',sans-serif; font-size:76px; font-weight:800; line-height:1; letter-spacing:-1px; }
.big .arrow { font-size:36px; color:#5B6875; }
.hero .lead { font-size:22px; font-weight:700; }
.hero .body { font-size:15px; line-height:1.7; }
.tiles { display:flex; gap:14px; flex-wrap:wrap; margin-top:14px; }
.tile { flex:1 1 150px; padding:14px 16px; border-radius:12px; background:#F3F6FA; }
.tile .k { font-size:13px; color:#5B6875; }
.tile .v { font-family:'Manrope',sans-serif; font-size:28px; font-weight:800; margin-top:4px; }
.tile .u { font-size:14px; font-weight:600; color:#5B6875; margin-left:4px; }
.legend { display:flex; gap:18px; flex-wrap:wrap; font-size:13px; color:#5B6875; margin:12px 0 10px; }
.legend i { display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:6px; vertical-align:-1px; }
.legend .dash { width:22px; height:0; border-top:2px dashed #B3261E; border-radius:0; vertical-align:3px; }
.chart { display:flex; height:260px; }
.col { position:relative; flex:1; height:260px; display:flex; align-items:flex-end; justify-content:center; }
.col.peak { background:#FBF1F0; }
.bar { width:58%; max-width:64px; border-radius:6px 6px 0 0; padding-top:6px; box-sizing:border-box; text-align:center; font-family:'Manrope',sans-serif; font-size:13px; font-weight:700; }
.lim { position:absolute; left:0; right:0; border-top:2px dashed #B3261E; }
.xlab { display:flex; margin-top:6px; }
.xlab div { flex:1; text-align:center; font-family:'Manrope',sans-serif; font-size:12px; color:#5B6875; }
.xlab span { display:block; font-family:'Noto Sans TC',sans-serif; font-size:11px; color:#8F1D17; font-weight:700; }
.part { margin-bottom:12px; }
.part .row { display:flex; justify-content:space-between; font-size:14px; }
.part .row b { font-family:'Manrope',sans-serif; }
.track { height:8px; border-radius:999px; background:#E8EEF5; margin-top:6px; }
.track div { height:8px; border-radius:999px; }
.total { margin-top:6px; padding:14px 16px; border-radius:12px; }
.total .r { display:flex; justify-content:space-between; align-items:baseline; }
.total .n { font-family:'Manrope',sans-serif; font-size:30px; font-weight:800; }
.cmp { display:flex; align-items:center; gap:10px; font-size:13px; margin-bottom:8px; }
.cmp .lab { width:34px; }
.cmp .tr { flex:1; height:14px; border-radius:999px; background:#E8EEF5; }
.cmp .tr div { height:14px; border-radius:999px; }
.cmp b { font-family:'Manrope',sans-serif; width:40px; text-align:right; }
.cloudrow { display:flex; align-items:center; gap:8px; font-size:12px; margin-top:6px; }
.cloudrow .tr { flex:1; height:6px; border-radius:999px; background:#DCE4EE; }
.cloudrow .tr div { height:6px; border-radius:999px; background:#1E4F8C; }
.cloudrow b { font-family:'Manrope',sans-serif; width:38px; text-align:right; }
.warnbar { padding:12px 16px; border-radius:12px; background:#FDF1D3; color:#5A3B00; font-weight:700; margin:8px 0 12px; }
div[data-testid="stRadio"] label p, div[data-testid="stSlider"] label p { font-size:13px; }
@media (max-width: 720px) {
  .big .t { font-size:52px; }
  .card { padding:18px 16px; }
  .hdr h1 { font-size:22px; }
}
</style>
"""), unsafe_allow_html=True)


def card_container():
    try:
        return st.container(border=True)
    except TypeError:          # 舊版 Streamlit 沒有 border 參數
        return st.container()


def show_df(df):
    try:
        st.dataframe(df, hide_index=True, width="stretch")
    except TypeError:
        st.dataframe(df, hide_index=True, use_container_width=True)


# --- 時間與日期 ---
target_hours = TARGET_HOURS
now_dt = datetime.now(TW_TZ)
tmr_dt = now_dt + timedelta(days=1)
today_str = now_dt.strftime("%Y-%m-%d")
tmr_str = tmr_dt.strftime("%Y-%m-%d")
week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
display_date_full = f"{now_dt.strftime('%Y/%m/%d')} {week_list[now_dt.weekday()]}"
tmr_week = week_list[tmr_dt.weekday()]


# --- 台灣行事曆（含補班）---
@st.cache_data(ttl=86400)
def get_calendar(years):
    return load_calendar(years)


cal = get_calendar(tuple(sorted({now_dt.year, tmr_dt.year})))
today_is_holiday = cal.is_holiday(now_dt.date())
tmr_is_holiday = cal.is_holiday(tmr_dt.date())


# --- 智慧氣象抓取（邏輯在 weather.py）---
@st.cache_data(ttl=300)
def get_smart_weather():
    try:
        vc_key = st.secrets["VC_API_KEY"] if "VC_API_KEY" in st.secrets else None
    except Exception:
        vc_key = None
    return fetch_smart_weather(vc_key=vc_key)


w = get_smart_weather()
temp, tmr_temp = w.get("temp", 25), w.get("tmr_temp", 25)
current_rad = w.get("rad", 0)
api_is_online = w["status_code"] > 0

# --- 頁首 ---
if w["status_code"] == 1:
    src_txt, src_bg, src_fg = "氣象來源 ECMWF · 運作正常", "#DFF1E9", "#0F5C41"
elif w["status_code"] == 2:
    src_txt, src_bg, src_fg = "ECMWF 壅塞 · 已切換 Visual Crossing 備援", "#FDF1D3", "#7A4F00"
else:
    src_txt, src_bg, src_fg = "氣象斷線 · 保守盲估模式", "#F8DDDA", "#8F1D17"

st.markdown(html(f"""
<div class="hdr">
  <div class="hdr-l">
    <div class="logo">❄️</div>
    <div>
      <h1>中創園區契約容量暨空調聯防</h1>
      <div class="sub">H300 戰情室 · 每天下午確認，設定今晚的儲冰排程</div>
    </div>
  </div>
  <div class="hdr-r">
    <div class="sub">{display_date_full} · {now_dt.strftime('%H:%M')}</div>
    <div class="pill" style="background:{src_bg};color:{src_fg};"><span class="dot" style="background:{src_fg};"></span>{src_txt}</div>
  </div>
</div>
"""), unsafe_allow_html=True)

for _msg in cal.warnings:
    st.warning(_msg)

# --- 明日條件（主畫面，同仁優先確認）---
def short_label(s):
    return s.split(" (")[0].replace("🌇 ", "").replace("🌙 ", "")


with card_container():
    st.markdown("**明日條件**　<span style='font-size:13px;color:#5B6875;'>請優先確認，系統會依平假日與租借時長精算儲冰策略</span>", unsafe_allow_html=True)
    cc1, cc2, cc3, cc4 = st.columns([1, 1, 1, 1.2])
    with cc1:
        conf_hall_status = st.radio("國際會議廳", ["無活動 (0 RT-HR)", "半天租借 (+75 RT-HR)", "全天租借 (+150 RT-HR)"],
                                    horizontal=True, format_func=lambda s: s.split(" (")[0].replace("租借", ""))
    with cc2:
        expo_hall_status = st.radio("展演大廳", ["無活動 (0 RT-HR)", "半天租借 (+125 RT-HR)", "全天租借 (+250 RT-HR)"],
                                    horizontal=True, format_func=lambda s: s.split(" (")[0].replace("租借", ""))
    with cc3:
        occupancy_rate = st.slider("園區預估進駐率 (%)", min_value=0, max_value=100, value=100, step=5)
    with cc4:
        overtime_status = st.radio("廠商加班預測", [
            "🌇 18:00 準時下班 (啟動夜間降載)",
            "🌙 19:30 晚間加班 (維持基礎供應)",
        ], horizontal=True, format_func=short_label)

# --- 側邊欄：進階參數 ---
with st.sidebar:
    st.info("V4.0：新版戰情室介面（運算引擎同 V3.9.5）")
    st.header("⚙️ 進階參數")

    chiller_compensation = st.number_input("預估磁浮主機平均耗電 (kW)", min_value=0.0, max_value=140.0, value=100.0, step=5.0)

    st.markdown("---")
    st.subheader("🌞 太陽能預測校正")
    solar_mode = st.radio("太陽能預估模式", [SOLAR_AUTO, SOLAR_MANUAL])
    if solar_mode == SOLAR_MANUAL:
        manual_solar = st.slider("手動設定巔峰太陽能 (kW)", min_value=0.0, max_value=SOLAR_MAX_KW, value=80.0, step=1.0)
    else:
        manual_solar = 80.0

    st.markdown("---")
    st.subheader("🎛️ 隱藏空調主機負載 (G11, GB1, GB2)")
    st.caption("A136 獨立挑高空間熱力學極限")
    ahu_mode = st.radio("預測模式", [AHU_AUTO, "✋ 手動固定基載"])
    if ahu_mode == "✋ 手動固定基載":
        hidden_ahu_load = st.slider("預估隱藏 AHU 耗電 (kW)", min_value=0.0, max_value=50.0, value=23.0, step=1.0)
    else:
        st.success("已啟用空間熱力學與排程連動演算")
        hidden_ahu_load = 23.0

    st.markdown("---")
    st.subheader("🚨 緊急降載沙盤推演")
    st.caption("當預估需量暴增時，向主管展示降載成效。")
    emergency_mode = st.toggle("🔴 啟動緊急防禦模式 (兵推)", value=False)
    if emergency_mode:
        emergency_mag_limit_pct = st.slider("強制封印磁浮主機上限 (%)", min_value=30, max_value=70, value=int(MAG_CAP_LIMIT * 100), step=5, help="藉由犧牲部分冷度，換取巨大的需量空間")
        emergency_ahu_drop = st.slider("強迫 AHU 提溫降載 (kW)", min_value=0.0, max_value=37.0, value=20.0, step=1.0, help="模擬現場將 G11, GB1, GB2 溫度調高2度所省下的耗電")
    else:
        emergency_mag_limit_pct = int(MAG_CAP_LIMIT * 100)
        emergency_ahu_drop = 0.0

    st.markdown("---")
    if st.button("🔄 強制同步最新氣象", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.markdown(f"<div style='color:#5B6875;font-size:13px;margin-top:8px;'>氣象同步時間：<b>{w['fetch_time']}</b></div>", unsafe_allow_html=True)

# --- 決策運算（邏輯在 forecast.py，auto_log.py 共用）---
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
start_time_str, end_time_str = fc["start_time_str"], fc["end_time_str"]
melt_start, melt_end, melt_memo = fc["melt_start"], fc["melt_end"], fc["melt_memo"]
is_holiday_mode = is_pure_holiday or is_holiday_event

# --- 狀態等級 ---
if is_pure_holiday:
    lv_txt, lv_bg, lv_fg, lv_dot = "假日 · 暫停儲冰", "#DCEBF8", "#0B4A7A", "#2F80C0"
elif is_holiday_event:
    lv_txt, lv_bg, lv_fg, lv_dot = "假日活動 · 省錢策略", "#DFF1E9", "#0F5C41", "#1A7F5A"
elif api_is_online and max_net_grid_demand > worst_limit_tmr:
    lv_txt, lv_bg, lv_fg, lv_dot = "超約風險", "#F8DDDA", "#8F1D17", "#B3261E"
elif suggested_ice_hrs <= 2:
    lv_txt, lv_bg, lv_fg, lv_dot = "餘裕充足", "#DFF1E9", "#0F5C41", "#1A7F5A"
elif suggested_ice_hrs <= 5:
    lv_txt, lv_bg, lv_fg, lv_dot = "逼近警戒", "#FDF1D3", "#7A4F00", "#F2B632"
else:
    lv_txt, lv_bg, lv_fg, lv_dot = "超約風險", "#F8DDDA", "#8F1D17", "#B3261E"

if emergency_mode:
    saved_kw_total = (MAG_CHILLER_RT * (MAG_CAP_LIMIT - inp.active_mag_limit) * MAG_EFF) + emergency_ahu_drop
    st.markdown(f"<div class='warnbar'>🚨 兵推模式運作中：已強制介入系統參數，預估可為園區緊急省下 {saved_kw_total:.1f} kW 的需量空間。</div>", unsafe_allow_html=True)

tab_tonight, tab_detail, tab_help = st.tabs(["今晚任務", "逐時明細", "參數說明"])

# ====================== 今晚任務 ======================
with tab_tonight:
    # 文字內容
    if is_holiday_mode:
        time_html = '<div class="big"><div class="t">暫停儲冰</div></div>'
        lead = "今晚不需要儲冰，並手動解除自動排程" if is_pure_holiday else "假日活動：建議暫停儲冰，明日直接啟動磁浮主機"
        body = (f"明日（{tmr_str} {tmr_week}）為休息日，" + ("無租借活動，務必手動關閉自動排程。" if is_pure_holiday
                else f"有租借活動（{event_ice_rthr:.0f} RT-HR），白天電價極低，直供冰水最划算。"))
    else:
        time_html = (f'<div class="big"><div class="t">{start_time_str}</div><div class="arrow">→</div>'
                     f'<div class="t">{end_time_str}</div></div>')
        lead = f"夜間儲冰 {suggested_ice_hrs:.1f} 小時"
        margin = worst_limit_tmr - max_net_grid_demand
        if not api_is_online:
            body = "氣象斷線，以下為保守盲估值，請人工確認現場狀況。"
        elif margin >= 0:
            body = (f"明日（{tmr_str} {tmr_week}）{worst_hour} 預估台電需量 <b>{max_net_grid_demand:.0f} kW</b>，"
                    f"距警戒線 {worst_limit_tmr:.0f} kW 尚有 <b>{margin:.0f} kW</b>。請依右側清單完成三項設定，並在 15:50 前完成磁浮降載。")
        else:
            body = (f"明日（{tmr_str} {tmr_week}）{worst_hour} 預估台電需量 <b>{max_net_grid_demand:.0f} kW</b>，"
                    f"已超過警戒線 {worst_limit_tmr:.0f} kW <b>{-margin:.0f} kW</b>！請務必長時間儲冰，並準備手動卸載。")

    left, right = st.columns([1.35, 1])
    with left:
        st.markdown(html(f"""
        <div class="card hero">
          <div class="hero-top"><span>今晚任務 · {now_dt.month} 月 {now_dt.day} 日夜間</span>
            <span class="pill" style="background:{lv_bg};color:{lv_fg};"><span class="dot" style="background:{lv_dot};border-radius:2px;"></span>{lv_txt}</span></div>
          {time_html}
          <div class="lead">{lead}</div>
          <div class="body">{body}</div>
        </div>
        """), unsafe_allow_html=True)
    with right:
        with card_container():
            st.markdown("**要在 BMS 設定的三件事**　<span style='font-size:13px;color:#5B6875;'>設定完成後請勾選</span>", unsafe_allow_html=True)
            if is_holiday_mode:
                items = [
                    ("夜間儲冰", "關閉排程", "明日為假日，請手動關閉自動排程" if is_pure_holiday else "假日離峰，暫停儲冰"),
                    ("日間融冰", f"{melt_start}" if is_pure_holiday else f"{melt_start} / {melt_end}", melt_memo.lstrip("*")),
                    ("磁浮主機（人工設定）", "維持現狀", "依現場需求調度或維持關機"),
                ]
            else:
                items = [
                    ("夜間儲冰", f"{start_time_str} → {end_time_str}", "最晚 07:00 結束，避開早晨需量尖峰"),
                    ("日間融冰", f"{melt_start} → {melt_end}", melt_memo.lstrip("*")),
                    ("磁浮主機（人工設定）", "08:00 上限 70%　15:50 降載 50%", "BMS 連動前，請併入廠務每日巡檢"),
                ]
            for i, (name, val, memo) in enumerate(items):
                st.checkbox(f"**{name}**　{val}  \n{memo}", key=f"chk_{i}_{tmr_str}")

    # --- 逐時圖 + 組成 ---
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    view = st.radio("顯示日期", ["明日預測", "今日追蹤"], horizontal=True, label_visibility="collapsed")
    is_tmr_view = view == "明日預測"
    chart_col, part_col = st.columns([2.3, 1])

    def bars_html(calc, is_summer):
        vals = [calc[h]["h_net"] for h in TARGET_HOURS if h in calc]
        top = max([550.0, 516.0] + vals) * 1.05
        H = 260.0
        cols, labs = [], []
        for h in TARGET_HOURS:
            if h not in calc:
                continue
            d = calc[h]
            v, lim = d["h_net"], d["current_limit"]
            hour = int(h[:2])
            peak = is_summer and 16 <= hour < 22
            if v > lim - 15:
                fill, txt = "#B3261E", "#FFFFFF"
            elif v > lim - 50:
                fill, txt = "#F2B632", "#14202B"
            else:
                fill, txt = "#1A7F5A", "#FFFFFF"
            cols.append(f'<div class="col{" peak" if peak else ""}"><div class="lim" style="bottom:{lim / top * H:.0f}px"></div>'
                        f'<div class="bar" style="height:{max(v, 0) / top * H:.0f}px;background:{fill};color:{txt};">{v:.0f}</div></div>')
            labs.append(f'<div>{h}{"<span>夜尖峰</span>" if peak else ""}</div>')
        return f'<div class="chart">{"".join(cols)}</div><div class="xlab">{"".join(labs)}</div>'

    with chart_col:
        calc_view = calc_tmr if is_tmr_view else calc_today
        date_view = f"{tmr_dt.month} 月 {tmr_dt.day} 日（{tmr_week[-1]}）" if is_tmr_view else f"{now_dt.month} 月 {now_dt.day} 日（{week_list[now_dt.weekday()][-1]}）"
        if api_is_online and calc_view:
            st.markdown(html(f"""
            <div class="card">
              <h3>{"明日" if is_tmr_view else "今日"}逐時需量預測</h3>
              <div class="note">{date_view} · 單位 kW · 扣除太陽能後的台電需量</div>
              <div class="legend">
                <span><i style="background:#1A7F5A"></i>餘裕充足</span>
                <span><i style="background:#F2B632"></i>逼近警戒</span>
                <span><i style="background:#B3261E"></i>超約風險</span>
                <span><i class="dash"></i>契約警戒線（日間 516 kW；夏月 16:00–22:00 為 452 kW）</span>
              </div>
              {bars_html(calc_view, is_summer_tmr if is_tmr_view else is_summer_today)}
            </div>
            """), unsafe_allow_html=True)
        else:
            st.warning("📡 氣象 API 暫時斷線，無法顯示逐時預測。")

    with part_col:
        if tmr_is_holiday:
            parts = [("非上班日基礎負載", 160.0, "#1E4F8C")]
            if event_kw > 0:
                parts.append(("假日活動空調加載", event_kw, "#1E4F8C"))
            total = 160.0 + event_kw
            title = "明日為非上班日"
            sub = "假日用電組成（kW）"
        else:
            thermal = worst_hour_load + tmr_shaved_kw - tmr_true_base_load - tmr_actual_load_growth
            parts = [
                (f"歷史基礎需量（{now_dt.month} 月）", tmr_true_base_load, "#1E4F8C"),
                ("進駐加載", tmr_actual_load_growth, "#1E4F8C"),
                ("空調熱力與慣性加載" if thermal >= 0 else "下班卸載調整", thermal, "#1E4F8C" if thermal >= 0 else "#1A7F5A"),
                (f"磁浮 {int(inp.emergency_mag_limit_pct)}% 封印降載", -tmr_shaved_kw, "#1A7F5A"),
                (f"太陽能（{worst_hour}）", -worst_hour_solar, "#1A7F5A"),
            ]
            total = max_net_grid_demand
            title = f"最危險時段 {worst_hour}"
            sub = "這個需量是怎麼組成的（kW）"
        vmax = max([abs(v) for _, v, _ in parts] + [1.0])
        rows = "".join(
            f'<div class="part"><div class="row"><span>{lab}</span><b>{"+" if val > 0 and i > 0 else ""}{val:.0f}</b></div>'
            f'<div class="track"><div style="width:{abs(val) / vmax * 100:.0f}%;background:{col};"></div></div></div>'
            for i, (lab, val, col) in enumerate(parts))
        margin2 = worst_limit_tmr - total
        if margin2 < 0:
            tb, tf, tmsg = "#F8DDDA", "#8F1D17", f"已超過警戒線 {worst_limit_tmr:.0f} kW {-margin2:.0f} kW"
        elif margin2 < 15 + 35:
            tb, tf, tmsg = "#FDF1D3", "#5A3B00", f"距警戒線 {worst_limit_tmr:.0f} kW 尚有 {margin2:.0f} kW"
        else:
            tb, tf, tmsg = "#DFF1E9", "#0F5C41", f"距警戒線 {worst_limit_tmr:.0f} kW 尚有 {margin2:.0f} kW"
        st.markdown(html(f"""
        <div class="card">
          <h3>{title}</h3>
          <div class="note" style="margin-bottom:14px;">{sub}</div>
          {rows}
          <div class="total" style="background:{tb};color:{tf};">
            <div class="r"><b>預估台電需量</b><span class="n">{total:.0f} kW</span></div>
            <div style="font-size:13px;">{tmsg}</div>
          </div>
        </div>
        """), unsafe_allow_html=True)

# ====================== 氣象、兵推、準確度（首頁下半部）======================
with tab_tonight:
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    def tile(k, v, u=""):
        return f'<div class="tile"><div class="k">{k}</div><div class="v">{v}<span class="u">{u}</span></div></div>'

    if w["status_code"] == 1:
        cloud_rows = "".join(
            f'<div class="cloudrow"><span style="width:18px">{n}</span><div class="tr"><div style="width:{v}%"></div></div><b>{v}%</b></div>'
            for n, v in [("低", w["cloud_low"]), ("中", w["cloud_mid"]), ("高", w["cloud_high"])])
        cloud_tile = f'<div class="tile" style="flex:1.4 1 220px;"><div class="k">目前雲量（低雲是發電殺手）</div>{cloud_rows}</div>'
    elif w["status_code"] == 2:
        cloud_tile = (f'<div class="tile" style="flex:1.4 1 220px;"><div class="k">目前總雲量</div>'
                      f'<div class="cloudrow"><div class="tr"><div style="width:{w["cloud"]}%"></div></div><b>{w["cloud"]}%</b></div></div>')
    else:
        cloud_tile = '<div class="tile" style="flex:1.4 1 220px;"><div class="k">雲量</div><div class="v" style="font-size:18px;">雙氣象源皆斷線</div></div>'
    calib = "（高溫動態校正）" if w.get("temp_is_calibrated") else ""

    wcol, ecol = st.columns([2.3, 1])
    with wcol:
        st.markdown(html(f"""
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;">
            <h3>氣象與太陽能</h3><span class="note">資料擷取 {w['fetch_time']}</span></div>
          <div class="tiles">
            {tile("目前園區氣溫" + calib, temp, "°C")}
            {tile("明日預測最高溫", tmr_temp, "°C")}
            {tile("目前短波輻射", current_rad, "W/m²")}
            {tile("明日平均太陽能", f"{est_solar:.1f}", "kW")}
            {cloud_tile}
          </div>
        </div>
        """), unsafe_allow_html=True)
    with ecol:
        if api_is_online:
            if emergency_mode:
                base_inp, sim_inp, sim_tag = replace(inp, emergency_mode=False, emergency_mag_limit_pct=int(MAG_CAP_LIMIT * 100), emergency_ahu_drop=0.0), inp, "兵推"
            else:
                sim_inp = replace(inp, emergency_mode=True, emergency_mag_limit_pct=40, emergency_ahu_drop=20.0)
                base_inp, sim_tag = inp, "兵推試算"
            b = compute_forecast(w, base_inp, now_dt, today_is_holiday, tmr_is_holiday)["max_net_grid_demand"]
            e = compute_forecast(w, sim_inp, now_dt, today_is_holiday, tmr_is_holiday)["max_net_grid_demand"]
            scale = max(b, e, 1.0)
            hint = "" if emergency_mode else "　（預設試算；左側「進階參數」可自訂並正式啟動）"
            st.markdown(html(f"""
            <div class="card">
              <h3>兵推沙盤</h3>
              <div class="note" style="margin-bottom:12px;">磁浮上限 {int(MAG_CAP_LIMIT * 100)}% 改 {sim_inp.emergency_mag_limit_pct}%，AHU 降載 {sim_inp.emergency_ahu_drop:.0f} kW{hint}</div>
              <div class="cmp"><span class="lab">基準</span><div class="tr"><div style="width:{b / scale * 100:.0f}%;background:#F2B632;"></div></div><b>{b:.0f}</b></div>
              <div class="cmp"><span class="lab" style="width:60px;">{sim_tag}</span><div class="tr"><div style="width:{e / scale * 100:.0f}%;background:#1A7F5A;"></div></div><b>{e:.0f}</b></div>
              <div style="display:flex;justify-content:space-between;align-items:baseline;">
                <span class="note">代價：部分區域冷度下降</span>
                <span class="num" style="font-size:22px;font-weight:800;color:#0F5C41;">省下 {b - e:.0f} kW</span></div>
            </div>
            """), unsafe_allow_html=True)
        else:
            st.markdown(html("""
            <div class="card"><h3>兵推沙盤</h3>
            <div class="note" style="margin-top:6px;">氣象斷線，無法試算。</div></div>
            """), unsafe_allow_html=True)

    # --- 預測準確度（紀錄累積進度）---
    @st.cache_data(ttl=3600)
    def count_log_days():
        """讀 auto_log 寫入的 Google Sheet，回傳 v2 資料列數；未設定憑證或失敗回傳 None。"""
        try:
            import json
            import gspread
            from google.oauth2.service_account import Credentials
            raw = st.secrets["GOOGLE_CREDENTIALS"]
            info = json.loads(raw) if isinstance(raw, str) else dict(raw)
            scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
            sheet = gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes)).open("中創園區空調戰情大數據").sheet1
            return sum(1 for v in sheet.col_values(15)[1:] if str(v).startswith("v2"))
        except Exception:
            return None

    log_days = count_log_days()
    TARGET_DAYS = 14
    if log_days is None:
        acc_l, acc_r, acc_w = "尚未連接紀錄表", "— / 14 天", 0
        acc_note = "每天 18:00 自動記錄預測值。在 Streamlit Secrets 加入 GOOGLE_CREDENTIALS 後，這裡會顯示累積天數；滿 14 天後可並排比對「預測」與「台電實際需量」。"
    else:
        acc_l, acc_r, acc_w = ("紀錄累積中" if log_days < TARGET_DAYS else "累積完成"), f"{min(log_days, TARGET_DAYS)} / {TARGET_DAYS} 天", min(log_days, TARGET_DAYS) / TARGET_DAYS * 100
        acc_note = "每天 18:00 自動記錄預測值。累積滿 14 天後，這裡會並排顯示「預測」與「台電實際需量」。"
    st.markdown(html(f"""
    <div class="card" style="margin-top:16px;display:flex;align-items:center;gap:28px;flex-wrap:wrap;">
      <div style="font-size:16px;font-weight:700;width:92px;flex-shrink:0;">預測準確度</div>
      <div style="flex:1 1 260px;">
        <div style="display:flex;justify-content:space-between;font-size:13px;color:#5B6875;"><span>{acc_l}</span><b class="num" style="color:#14202B;">{acc_r}</b></div>
        <div class="track"><div style="width:{acc_w:.0f}%;background:#1E4F8C;"></div></div>
      </div>
      <div class="note" style="flex:1 1 280px;line-height:1.6;">{acc_note}</div>
    </div>
    <div class="note" style="margin-top:14px;">設備參數：CHU-2（磁浮冰機）· BCU-1（儲冰主機）· IB-1（2500 RT-HR）· AHU-G11 / GB1 / GB2</div>
    """), unsafe_allow_html=True)

with tab_detail:
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    def detail_df(calc):
        rows = []
        for h in TARGET_HOURS:
            if h not in calc:
                continue
            d = calc[h]
            clouds = f'{d["c_low"]} / {d["c_mid"]} / {d["c_high"]}' if w["status_code"] == 1 else f'{d["c_low"]}（總）'
            rows.append({
                "時間": h, "天氣": d["wx"], "氣溫 °C": d["temp"], "輻射 W/m²": d["rad"], "雲量 低/中/高 %": clouds,
                "雲層衰減": f'-{int((1 - d["cp"]) * 100)}%' if d["cp"] < 1.0 else "無",
                "總負載 kW": round(d["h_load"], 1), "太陽能 kW": round(d["h_solar"], 1),
                "台電需量 kW": round(d["h_net"], 1), "警戒線 kW": d["current_limit"],
            })
        return pd.DataFrame(rows)

    st.markdown(f"**明日逐時明細（{tmr_str}）**")
    if api_is_online and calc_tmr:
        show_df(detail_df(calc_tmr))
    else:
        st.warning("📡 API 暫時斷線。")
    st.markdown(f"**今日逐時明細（{today_str}，現場比對用）**")
    if api_is_online and calc_today:
        show_df(detail_df(calc_today))
    else:
        st.warning("📡 API 暫時斷線。")

# ====================== 參數說明 ======================
with tab_help:
    st.markdown(f"""
**怎麼用**：每天下午先確認「明日條件」（會議廳、展演大廳、進駐率、加班），再照「今晚任務」把三項設定輸入 BMS 並勾選。

**季別**：明日為 **{season_tag}**，契約警戒線 {fc['contract_limit']:.0f} kW；夏月（5/16–10/15）16:00–22:00 為 452 kW，其餘 516 kW。

**假日判斷**：依政府行事曆（含補班日），每天更新；抓不到時頁面上方會出現警告，並暫以週末判斷。臨時變更（颱風假等）可寫在 `data/calendar_overrides.json`。

**需量怎麼算**：歷史基礎需量 + 進駐加載 + 空調熱力與慣性加載 − 磁浮 50% 封印降載 − 太陽能 = 預估台電需量。

**設備參數**：CHU-2（磁浮冰機）· BCU-1（儲冰主機）· IB-1（2500 RT-HR）· AHU-G11 / GB1 / GB2。

以上皆為預測值，不等於台電實測需量。
""")
