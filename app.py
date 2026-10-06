import streamlit as st
from datetime import datetime, timedelta

import altair as alt
import pandas as pd

from calendar_tw import load_calendar
from forecast import ForecastInputs, compute_forecast, off_days_before, OVERTIME_ONTIME
import trend
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
.chart.dense .bar { width:80%; font-size:11px; padding-top:4px; }
.xlab.dense div { font-size:11px; }
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


def show_chart(chart):
    try:
        st.altair_chart(chart, width="stretch")
    except TypeError:
        st.altair_chart(chart, use_container_width=True)


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

# 側邊欄已移除：舊版手動參數、兵推沙盤（公式約高估降載量 2 倍）與強制同步氣象（本來每 5 分鐘自動更新）。
# 預測一律用 ForecastInputs 預設值，與每天 18:00 自動紀錄相同。

# --- 決策運算（邏輯在 forecast.py，auto_log.py 共用）---
inp = ForecastInputs(
    conf_hall_status=conf_hall_status, expo_hall_status=expo_hall_status,
    occupancy_rate=occupancy_rate, overtime_status=overtime_status,
)
fc = compute_forecast(w, inp, now_dt, today_is_holiday, tmr_is_holiday,
                      prev_off_days=off_days_before(tmr_dt.date(), cal.is_holiday))

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

SHEET_ID = "1NZ0OPky-I-oWXfFTeVR8qpFT1pFBwQvRoSerJYJJMwY"   # 中創園區空調戰情大數據


def open_book():
    """以 Streamlit Secrets 的 GOOGLE_CREDENTIALS 唯讀開啟紀錄試算表；未設定時拋出例外。"""
    import json
    import gspread
    from google.oauth2.service_account import Credentials
    raw = st.secrets["GOOGLE_CREDENTIALS"]
    info = json.loads(raw) if isinstance(raw, str) else dict(raw)
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    # 用試算表 ID 開啟：依名稱開啟要透過 Drive 搜尋，唯讀 Sheets 權限不夠
    return gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes)).open_by_key(SHEET_ID)


def load_trend_sheets():
    """讀實測需量、主紀錄表、預測與實測比對三張表；回傳 (資料, 錯誤訊息)。失敗不快取，修好設定後重新整理即可。"""
    try:
        has_creds = "GOOGLE_CREDENTIALS" in st.secrets
    except Exception:   # 完全沒有 secrets 檔（本機執行）
        has_creds = False
    if not has_creds:
        return None, "尚未連接紀錄試算表（Streamlit Secrets 需設定 GOOGLE_CREDENTIALS）。"
    try:
        return _read_trend_sheets(), None
    except Exception as e:
        return None, f"讀取紀錄試算表失敗：{type(e).__name__}: {str(e)[:200]}"


@st.cache_data(ttl=1800)
def _read_trend_sheets():
    book = open_book()
    out = {}
    for key, title in (("actual", "實測需量"), ("log", None), ("compare", "預測與實測比對")):
        try:
            ws = book.sheet1 if title is None else book.worksheet(title)
            out[key] = ws.get_all_values()
        except Exception:
            out[key] = []
    return out


tab_tonight, tab_detail, tab_trend, tab_help = st.tabs(["今晚任務", "逐時明細", "實測趨勢", "參數說明"])

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
                    f"距警戒線 {worst_limit_tmr:.0f} kW 尚有 <b>{margin:.0f} kW</b>。請依右側清單完成三項設定，並在 15:50 前完成磁浮{'全關' if fc['tmr_has_peak'] else '降載'}。")
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
                    ("夜間儲冰", f"{start_time_str} → {end_time_str}",
                     (f"收假前一晚：休了 {fc['prev_off_days']} 天，儲冰槽回溫，已多排約 "
                      f"{fc['rewarm_hrs']:.1f} 小時；" if fc["rewarm_rthr"] else "")
                     + "最晚 07:00 結束，避開早晨需量尖峰"),
                    ("日間融冰", f"{melt_start} → {melt_end}", melt_memo.lstrip("*")),
                    ("磁浮主機（人工設定）",
                     "08:00 上限 70%　15:50 全關（融冰供冷）" if fc["tmr_has_peak"] else "08:00 上限 70%　15:50 降載 50%",
                     "；".join(fc["mag_plan"]) if fc["mag_plan"] else "BMS 連動前，請併入廠務每日巡檢"),
                ]
            for i, (name, val, memo) in enumerate(items):
                st.checkbox(f"**{name}**　{val}  \n{memo}", key=f"chk_{i}_{tmr_str}")

    # --- 明日最壞情況（實測校正模型）---
    if fc["risk"] and not is_holiday_mode:
        names = {"semi": "白天半尖峰", "peak": "尖峰空調時段"}
        cells = ""
        for blk, r in fc["risk"].items():
            col = "#B3261E" if r["margin"] < 0 else ("#7A4F00" if r["margin"] < 30 else "#0F5C41")
            cells += (f'<div class="tile"><div class="k">{names[blk]} {r["hours"]}（上限 {r["limit"]:.0f}）</div>'
                      f'<div class="v">{r["pred"]:.0f}<span class="u"> kW 預估</span></div>'
                      f'<div style="font-size:13px;color:{col};">最壞情況（95%）{r["p95"]:.0f} kW，距上限 {r["margin"]:.0f} kW</div>'
                      f'<div style="font-size:13px;color:#5B6875;">即時門檻：{r["alert"]:.0f} call 報（90%）／{r["action"]:.0f} 降磁浮／{r["release"]:.0f} 恢復</div></div>')
        mi = fc["model_info"] or {}
        note = (f"實測校正模型（資料至 {mi.get('trained_through')}，近 {mi.get('eval_days')} 個上班日逐時誤差約 {mi.get('mae_work')} kW）。"
                "最壞情況＝預估最高＋過去 95% 的日子不會超過的突波量；突波來自冰機加載等瞬間變化，天氣預報抓不到，"
                "所以收到 call 報後要在 15 分鐘平均超過降載門檻前，從中央監控調降磁浮。")
        if mi.get("summer_only") and not fc["is_summer_tmr"]:
            note += "　⚠️ 模型目前只看過夏月資料，非夏月前幾週誤差可能較大，等 10 月實測進來會自動修正。"
        st.markdown(html(f"""
        <div class="card" style="margin-top:16px;">
          <h3>明日最壞情況與即時降載門檻</h3>
          <div class="note" style="margin-bottom:10px;">{note}</div>
          <div class="tiles">{cells}</div>
        </div>
        """), unsafe_allow_html=True)

    # --- 逐時圖 + 組成 ---
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    view = st.radio("顯示日期", ["明日預測", "今日追蹤"], horizontal=True, label_visibility="collapsed")
    is_tmr_view = view == "明日預測"
    chart_col, part_col = st.columns([2.3, 1])

    def bars_html(calc):
        vals = [calc[h]["h_net"] for h in TARGET_HOURS if h in calc]
        top = max([550.0] + [calc[h]["current_limit"] for h in calc] + vals) * 1.05
        H = 260.0
        cols, labs = [], []
        for h in TARGET_HOURS:
            if h not in calc:
                continue
            d = calc[h]
            v, lim = d["h_net"], d["current_limit"]
            hour = int(h[:2])
            peak = d["period"] == "尖峰"
            if v > lim - 15:
                fill, txt = "#B3261E", "#FFFFFF"
            elif v > lim - 50:
                fill, txt = "#F2B632", "#14202B"
            else:
                fill, txt = "#1A7F5A", "#FFFFFF"
            cols.append(f'<div class="col{" peak" if peak else ""}"><div class="lim" style="bottom:{lim / top * H:.0f}px"></div>'
                        f'<div class="bar" style="height:{max(v, 0) / top * H:.0f}px;background:{fill};color:{txt};">{v:.0f}</div></div>')
            labs.append(f'<div>{h[:2] if len(calc) > 8 else h}{"<span>尖峰</span>" if peak else ""}</div>')
        dense = " dense" if len(calc) > 8 else ""
        return f'<div class="chart{dense}">{"".join(cols)}</div><div class="xlab{dense}">{"".join(labs)}</div>'

    with chart_col:
        calc_view = calc_tmr if is_tmr_view else calc_today
        date_view = f"{tmr_dt.month} 月 {tmr_dt.day} 日（{tmr_week[-1]}）" if is_tmr_view else f"{now_dt.month} 月 {now_dt.day} 日（{week_list[now_dt.weekday()][-1]}）"
        if api_is_online and calc_view:
            st.markdown(html(f"""
            <div class="card">
              <h3>{"明日" if is_tmr_view else "今日"}逐時需量預測</h3>
              <div class="note">{date_view} · 單位 kW · 扣除太陽能後的台電需量{"（每小時最大 15 分鐘需量，實測校正）" if fc["model_used"] else ""}</div>
              <div class="legend">
                <span><i style="background:#1A7F5A"></i>餘裕充足</span>
                <span><i style="background:#F2B632"></i>逼近警戒</span>
                <span><i style="background:#B3261E"></i>超約風險</span>
                <span><i class="dash"></i>契約警戒線（尖峰 452／半尖峰 516／週六半尖峰及離峰 616 kW）</span>
              </div>
              {bars_html(calc_view)}
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
                ("磁浮全關（融冰供冷）" if calc_tmr.get(worst_hour, {}).get("period") == "尖峰"
                 else f"磁浮 {int(inp.emergency_mag_limit_pct)}% 封印降載", -tmr_shaved_kw, "#1A7F5A"),
                (f"太陽能（{worst_hour}）", -worst_hour_solar, "#1A7F5A"),
            ]
            phys = calc_tmr.get(worst_hour, {}).get("h_net_phys")
            if phys is not None:
                parts.append(("實測校正（模型與實測的差）", max_net_grid_demand - phys, "#6B4FA0"))
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

# ====================== 氣象、準確度（首頁下半部）======================
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

    # --- 預測準確度（紀錄累積進度）---
    @st.cache_data(ttl=3600)
    def count_log_days():
        """讀 auto_log 寫入的 Google Sheet，回傳 v2／v3 實算資料列數；未設定憑證或失敗回傳 None。"""
        try:
            return sum(1 for v in open_book().sheet1.col_values(15)[1:] if str(v).startswith(("v2", "v3")))
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
    <div class="note" style="margin-top:14px;">設備參數：CHU-2（磁浮冰機 240 RT）· BCU-1（儲冰主機）· IB-1（2500 RT-HR）· AHU-G11 / GB1 / GB2</div>
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
                "台電需量 kW": round(d["h_net"], 1), "時段": d["period"], "警戒線 kW": d["current_limit"],
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

# ====================== 實測趨勢 ======================
with tab_trend:
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    sheets, err = load_trend_sheets()
    actual = trend.parse_actual(sheets["actual"]) if sheets else {}
    if err or not actual:
        st.info(err or "「實測需量」工作表還沒有資料。")
    else:
        fc_hourly = trend.parse_compare(sheets["compare"])
        # 每日紀錄優先；沒有紀錄的日子（6/8–9/30 歷史氣象回測）用比對表逐時預測的當日最高
        fc_daily = {**trend.daily_from_hourly(fc_hourly), **trend.parse_forecast_log(sheets["log"])}
        daily = trend.daily_table(actual, fc_daily)
        span = st.radio("期間", ["近 30 天", "近 90 天", "全部"], index=2, horizontal=True)
        if span != "全部":
            cut = daily["日期"].max() - pd.Timedelta(days=30 if span == "近 30 天" else 90)
            daily = daily[daily["日期"] >= cut]

        last = daily.dropna(subset=["距契約最近(kW)"])
        worst = last.loc[last["距契約最近(kW)"].idxmin()] if not last.empty else None
        both = daily.dropna(subset=["實測日間最高(kW)", "預測最高(kW)"])
        c1, c2, c3 = st.columns(3)
        c1.metric("期間日間最高", f'{daily["實測日間最高(kW)"].max():.0f} kW')
        if worst is not None:
            c2.metric("距契約最近", f'{worst["距契約最近(kW)"]:.1f} kW',
                      f'{worst["日期"]:%m/%d} {worst["距契約最近時段"]}', delta_color="off")
        c3.metric("預測平均誤差", f'{both["誤差(kW)"].abs().mean():.0f} kW' if not both.empty else "—",
                  f"{len(both)} 天可比對", delta_color="off")

        long = daily.melt(id_vars=["日期", "星期"], value_vars=["實測日間最高(kW)", "預測最高(kW)"],
                          var_name="項目", value_name="kW").dropna(subset=["kW"])
        color = alt.Scale(domain=["實測日間最高(kW)", "預測最高(kW)"], range=["#1E4F8C", "#E07A1F"])
        lines = alt.Chart(long).mark_line(point=alt.OverlayMarkDef(size=28)).encode(
            x=alt.X("日期:T", title=None, axis=alt.Axis(format="%m/%d")),
            y=alt.Y("kW:Q", title="kW", scale=alt.Scale(domain=[0, 640])),
            color=alt.Color("項目:N", scale=color, legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("日期:T", format="%Y-%m-%d"), "星期", "項目", alt.Tooltip("kW:Q", format=".1f")])
        refs = pd.DataFrame({"kW": [452, 516], "線": ["尖峰 452", "半尖峰 516"]})
        rules = alt.Chart(refs).mark_rule(strokeDash=[6, 4], color="#B3261E").encode(y="kW:Q", tooltip=["線"])
        st.markdown("**每日日間最高需量（08:00–18:00，每小時最大 15 分鐘平均）**")
        show_chart((lines + rules).properties(height=320))
        if both.empty:
            st.caption("目前還沒有同一天同時有預測與實測的資料：預測從每日 18:00 自動紀錄開始累積，"
                       "實測需請監控廠商匯出後貼到「實測需量」工作表。兩邊都有之後，橘線會出現在同一天。")

        st.markdown("**單日逐時曲線**")
        days = sorted(daily["日期"].dt.date.unique(), reverse=True)
        pick = st.selectbox("日期", days, format_func=lambda d: f"{d:%Y-%m-%d}（{trend.WEEKDAYS[d.weekday()]}）")
        hourly = trend.hourly_table(pick, actual, fc_hourly)
        hl = hourly.melt(id_vars=["時間", "時段"], value_vars=["實測(kW)", "預測(kW)"],
                         var_name="項目", value_name="kW").dropna(subset=["kW"])
        hcolor = alt.Scale(domain=["實測(kW)", "預測(kW)"], range=["#1E4F8C", "#E07A1F"])
        h_lines = alt.Chart(hl).mark_line(point=True).encode(
            x=alt.X("時間:T", title=None, axis=alt.Axis(format="%H:%M")),
            y=alt.Y("kW:Q", title="kW", scale=alt.Scale(domain=[0, 640])),
            color=alt.Color("項目:N", scale=hcolor, legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("時間:T", format="%H:%M"), "時段", "項目", alt.Tooltip("kW:Q", format=".1f")])
        h_lim = alt.Chart(hourly).mark_line(interpolate="step-after", strokeDash=[6, 4], color="#B3261E").encode(
            x="時間:T", y="契約上限(kW):Q", tooltip=["時段", "契約上限(kW)"])
        show_chart((h_lines + h_lim).properties(height=280))

        with st.expander("每日明細"):
            show_df(daily.sort_values("日期", ascending=False).assign(日期=lambda x: x["日期"].dt.strftime("%Y-%m-%d")))
        st.caption("實測為監控主機每分鐘瞬間值換算的 15 分鐘平均（台電計費方式）；空白代表監控當天停機或保養，不補值。"
                   "2026/6/8–9/30 的預測是事後用當時歷史氣象回測重算（氣象接近實況，誤差主要來自負載模型），"
                   "之後的預測則是每天 18:00 實際產生的紀錄。")

# ====================== 參數說明 ======================
with tab_help:
    st.markdown(f"""
**怎麼用**：每天下午先確認「明日條件」（會議廳、展演大廳、進駐率、加班），再照「今晚任務」把三項設定輸入 BMS 並勾選。

**季別與契約**：明日為 **{season_tag}**，當日最嚴格的契約警戒線 {fc['contract_limit']:.0f} kW。警戒線依台電高壓三段式時段切換：尖峰 452 kW（僅夏月 5/16–10/15 週一～五 16:00–22:00）、半尖峰 516 kW、週六半尖峰及離峰 616 kW；週日全日離峰。平日國定假日仍以平日時段計（保守）。

**假日判斷**：依政府行事曆（含補班日），每天更新；抓不到時頁面上方會出現警告，並暫以週末判斷。臨時變更（颱風假等）可寫在 `data/calendar_overrides.json`。

**需量怎麼算**：歷史基礎需量 + 進駐加載 + 空調熱力與慣性加載 − 磁浮降載（尖峰 16:00 起全關、由融冰供冷；其他時段封印 50%）− 太陽能 = 預估台電需量。園區空調供應時間 07:30–18:00。

**設備參數**：CHU-2（磁浮冰機 240 RT）· BCU-1（儲冰主機）· IB-1（2500 RT-HR）· AHU-G11 / GB1 / GB2。

以上皆為預測值，不等於台電實測需量。
""")
