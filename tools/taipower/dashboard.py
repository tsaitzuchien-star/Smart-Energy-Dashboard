"""中創園區 台電計費電表 15 分鐘需量儀表板（Streamlit）。

    streamlit run dashboard.py
資料：taipower_live.py 每 15 分鐘更新的 taipower_data.csv（台電計費表網頁數字，非 BMS）。
"""
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import taipower_common as tc

ALERT_RATIO = 0.90          # 與現場「瞬間值達契約 90% 告警」一致
EVENTS_FILE = os.path.join(tc.DATA_DIR, "events.csv")
DEFAULT_EVENTS = [("2026-11-16", "非夏月製冰測試＋早上融冰測試（00:00–約 07:00），需量偏高，非故障")]
PERIOD_ORDER = ["尖峰", "半尖峰", "週六半尖峰", "離峰"]

st.set_page_config(page_title="中創園區 台電即時需量", page_icon="⚡", layout="wide")


@st.cache_data(ttl=60)
def load(mtime):
    df = tc.load_csv()
    recent = os.path.join(tc.DATA_DIR, "taipower_recent.csv")     # 讀雲端資料夾時：完整檔每天一次＋最近 35 天每 15 分鐘
    if os.path.exists(recent):
        df = pd.concat([df, tc.load_csv(recent)]).drop_duplicates(subset=["時間"], keep="last")
    if df.empty:
        return df
    df["t"] = pd.to_datetime(df["時間"])
    df["day"] = (df["t"] - pd.Timedelta(minutes=15)).dt.date        # 用電日（00:00 那筆算前一天）
    df["tod"] = (df["t"] - pd.Timedelta(minutes=15)).dt.strftime("%H:%M")   # 區間起始時刻
    df["alert_kw"] = (df["契約容量_kW"] * ALERT_RATIO).round(0)
    return df.sort_values("t").reset_index(drop=True)


def load_events():
    if os.path.exists(EVENTS_FILE):
        ev = pd.read_csv(EVENTS_FILE, encoding="utf-8-sig")
        return {str(r.iloc[0]): str(r.iloc[1]) for _, r in ev.iterrows()}
    return dict(DEFAULT_EVENTS)


def day_chart(df, d, compare=None, height=380):
    """當日需量面積圖＋依時段變化的契約容量階梯線＋90% 告警線；compare={標籤: 日期} 疊虛線。"""
    cur = df[df["day"] == d]
    x = alt.X("tod:O", title="時間（區間起始）", axis=alt.Axis(values=[f"{h:02d}:00" for h in range(0, 24, 2)], labelAngle=0))
    lines = []
    full = pd.DataFrame({"tod": [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]})
    lim = cur[["tod", "契約容量_kW", "alert_kw"]]
    if len(lim) < 96 and len(lim):
        # 今天還沒到的時段也畫契約線：用最近一個同時刻資料補（契約時段表每天相同類型一致）
        ref_day = df[(df["day"] < d) & (pd.to_datetime(df["day"]).dt.weekday == pd.Timestamp(d).weekday())]["day"].max()
        ref = df[df["day"] == ref_day][["tod", "契約容量_kW", "alert_kw"]]
        lim = full.merge(lim, on="tod", how="left").set_index("tod").combine_first(ref.set_index("tod")).reset_index()
    area = alt.Chart(cur).mark_area(line={"color": "#1f6fb4", "strokeWidth": 2}, color="#1f6fb4", opacity=0.18).encode(
        x=x, y=alt.Y("需量_kW:Q", title="需量 (kW)", scale=alt.Scale(zero=True)),
        tooltip=["時間", "時段", "需量_kW", "契約容量_kW", "使用率_%"])
    lines.append(area)
    lines.append(alt.Chart(lim).mark_line(color="#d62728", strokeDash=[6, 4], interpolate="step-after").encode(
        x=x, y="契約容量_kW:Q", tooltip=["tod", "契約容量_kW"]))
    lines.append(alt.Chart(lim).mark_line(color="#ff9f1c", strokeDash=[3, 3], interpolate="step-after").encode(
        x=x, y="alert_kw:Q", tooltip=["tod", alt.Tooltip("alert_kw", title="90% 告警")]))
    if compare:
        rows = []
        for label, cd in compare.items():
            c = df[df["day"] == cd][["tod", "需量_kW"]].copy()
            c["比較"] = f"{label}（{cd}）"
            rows.append(c)
        if rows:
            cdf = pd.concat(rows)
            lines.append(alt.Chart(cdf).mark_line(strokeDash=[2, 2], strokeWidth=1.5).encode(
                x=x, y="需量_kW:Q", color=alt.Color("比較:N", legend=alt.Legend(orient="top", title=None),
                                                       scale=alt.Scale(range=["#7f7f7f", "#9467bd", "#2ca02c"])),
                tooltip=["比較", "tod", "需量_kW"]))
    return alt.layer(*lines).properties(height=height).interactive(bind_y=False)


def same_weekday_last_year(d):
    return d - timedelta(days=364)


def live_section():
    path = tc.CSV_FILE
    if not os.path.exists(path):
        st.warning(f"找不到資料檔 {path}。請先執行 taipower_backfill.py 或 taipower_live.py。")
        st.stop()
    recent = os.path.join(tc.DATA_DIR, "taipower_recent.csv")
    if os.path.exists(recent):
        path = max([path, recent], key=os.path.getmtime)
    df = load(os.path.getmtime(path))
    can_fetch = "BMS" not in tc.DATA_DIR      # 讀雲端副本的電腦（例如你的電腦）不自己登入台電
    events = load_events()
    age_min = (datetime.now().timestamp() - os.path.getmtime(path)) / 60

    with st.sidebar:
        st.header("⚙️ 控制台")
        st.caption(f"資料檔更新：{datetime.fromtimestamp(os.path.getmtime(path)):%m/%d %H:%M}（{age_min:.0f} 分鐘前）")
        if age_min > 40:
            st.error("資料超過 40 分鐘沒更新，請看 taipower.log 或工作排程 CTIC_Taipower_Live。")
        if can_fetch and st.button("🔄 立即抓取台電最新數據", use_container_width=True):
            with st.spinner("登入台電抓取中（約 30–60 秒）…"):
                here = os.path.dirname(os.path.abspath(__file__))
                r = subprocess.run([sys.executable, os.path.join(here, "taipower_live.py")], capture_output=True, text=True)
            (st.success if r.returncode == 0 else st.error)("完成" if r.returncode == 0 else "抓取失敗，請看 taipower.log")
            st.cache_data.clear()
            st.rerun()
        st.caption("口徑：台電計費電表網頁 15 分鐘需量（時間＝區間結束）。SQL 總表電表約比台電低 3–4 kW；BMS 畫面 MCB 約低 13–14 kW。")

    tabs = st.tabs(["📈 今日即時", "📅 歷史查詢", "🧾 每月各時段最高", "🌡️ 熱圖", "📊 年度比較", "🩺 資料品質"])

    # ---- 今日 ----
    with tabs[0]:
        latest_day = df["day"].max()
        today = df[df["day"] == latest_day]
        last = today.iloc[-1]
        pk = today.loc[today["需量_kW"].idxmax()]
        margin = last["契約容量_kW"] - last["需量_kW"]
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("最新時間", last["時間"][-5:], f"{last['時段']}")
        c2.metric("最新 15 分鐘需量", f"{last['需量_kW']:.0f} kW", f"使用率 {last['使用率_%']}%", delta_color="off")
        c3.metric("本時段契約／90% 告警", f"{last['契約容量_kW']:.0f} kW", f"告警 {last['alert_kw']:.0f} kW", delta_color="off")
        c4.metric("距契約餘裕", f"{margin:.0f} kW")
        c5.metric("今日最高需量", f"{pk['需量_kW']:.0f} kW", f"{pk['時間'][-5:]}・{pk['時段']}", delta_color="off")
        over = today[today["需量_kW"] > today["契約容量_kW"]]
        near = today[today["需量_kW"] >= today["alert_kw"]]
        if len(over):
            o = over.iloc[0]
            st.error(f"🚨 超約：{o['時間'][-5:]} {o['需量_kW']:.0f} kW ＞ {o['時段']}契約 {o['契約容量_kW']:.0f} kW（今日共 {len(over)} 個 15 分鐘）")
        elif len(near):
            st.warning(f"⚠️ 今日有 {len(near)} 個 15 分鐘達契約 90%，最近一次 {near.iloc[-1]['時間'][-5:]}")
        if str(latest_day) in events:
            st.info(f"📌 特殊事件：{events[str(latest_day)]}")
        comp = {"昨天": latest_day - timedelta(days=1), "上週同日": latest_day - timedelta(days=7),
                "去年同星期": same_weekday_last_year(latest_day)}
        st.altair_chart(day_chart(df, latest_day, comp), use_container_width=True)
        with st.expander("今日 15 分鐘明細"):
            st.dataframe(today[["時間", "時段", "需量_kW", "契約容量_kW", "使用率_%"]].iloc[::-1], hide_index=True, use_container_width=True)

    # ---- 歷史 ----
    with tabs[1]:
        dmin, dmax = df["day"].min(), df["day"].max()
        c1, c2 = st.columns([1, 2])
        d = c1.date_input("日期", value=dmax - timedelta(days=1), min_value=dmin, max_value=dmax)
        cmp_days = c2.text_input("疊加比較日（逗號分隔，例 2025-11-24, 2024-11-25）", "")
        comp = {}
        for i, s in enumerate([x.strip() for x in cmp_days.split(",") if x.strip()][:3]):
            try:
                comp[f"比較{i+1}"] = date.fromisoformat(s)
            except ValueError:
                st.caption(f"看不懂日期：{s}")
        if not comp:
            comp = {"去年同星期": same_weekday_last_year(d)}
        sub = df[df["day"] == d]
        if sub.empty:
            st.info("這天沒有資料")
        else:
            pk = sub.loc[sub["需量_kW"].idxmax()]
            st.markdown(f"**{d}（{'一二三四五六日'[d.weekday()]}）** 最高 {pk['需量_kW']:.0f} kW @ {pk['時間'][-5:]}（{pk['時段']}），"
                        f"日用電約 {sub['需量_kW'].sum() * 0.25:,.0f} 度，{len(sub)} 筆")
            if str(d) in events:
                st.info(f"📌 {events[str(d)]}")
            st.altair_chart(day_chart(df, d, comp), use_container_width=True)
        st.download_button("下載全部資料 CSV", df[tc.COLUMNS].to_csv(index=False).encode("utf-8-sig"), "taipower_data.csv")

    # ---- 每月各時段最高 ----
    with tabs[2]:
        st.caption("依「日曆月」統計；台電帳單計費期間依抄表日，數字可能與帳單差幾 kW。超約＝15 分鐘需量 ＞ 該時段契約。")
        m = df.assign(月=df["day"].map(lambda x: x.strftime("%Y-%m")))
        g = m.groupby(["月", "時段"])
        tab = g.apply(lambda x: pd.Series({
            "最高_kW": x["需量_kW"].max(),
            "發生時間": x.loc[x["需量_kW"].idxmax(), "時間"],
            "契約_kW": x.loc[x["需量_kW"].idxmax(), "契約容量_kW"],
            "達90%次數": int((x["需量_kW"] >= x["alert_kw"]).sum()),
            "超約次數": int((x["需量_kW"] > x["契約容量_kW"]).sum()),
        }), include_groups=False).reset_index()
        tab["使用率_%"] = (tab["最高_kW"] / tab["契約_kW"] * 100).round(1)
        tab["時段"] = pd.Categorical(tab["時段"], PERIOD_ORDER)
        tab = tab.sort_values(["月", "時段"], ascending=[False, True])
        st.dataframe(tab, hide_index=True, use_container_width=True,
                     column_config={"使用率_%": st.column_config.ProgressColumn(min_value=0, max_value=110, format="%.1f%%")})
        ov = m[m["需量_kW"] > m["契約容量_kW"]]
        st.markdown(f"**全部超約紀錄（{len(ov)} 筆）**")
        st.dataframe(ov[["時間", "時段", "需量_kW", "契約容量_kW", "使用率_%"]].iloc[::-1], hide_index=True, use_container_width=True)

    # ---- 熱圖 ----
    with tabs[3]:
        c1, c2 = st.columns(2)
        s = c1.date_input("起", value=dmax - timedelta(days=60), min_value=dmin, max_value=dmax, key="hs")
        e = c2.date_input("迄", value=dmax, min_value=dmin, max_value=dmax, key="he")
        h = df[(df["day"] >= s) & (df["day"] <= e)].copy()
        h["日期"] = h["day"].astype(str)
        hm = alt.Chart(h).mark_rect().encode(
            x=alt.X("tod:O", title="時間", axis=alt.Axis(values=[f"{x:02d}:00" for x in range(0, 24, 2)], labelAngle=0)),
            y=alt.Y("日期:O", title=None, sort="descending"),
            color=alt.Color("需量_kW:Q", scale=alt.Scale(scheme="turbo"), title="kW"),
            tooltip=["時間", "時段", "需量_kW", "使用率_%"]).properties(height=max(300, 9 * h["日期"].nunique()))
        st.altair_chart(hm, use_container_width=True)

    # ---- 年度 ----
    with tabs[4]:
        y = df.assign(年=df["t"].dt.year)
        yt = y.groupby("年").agg(最高_kW=("需量_kW", "max"), 平均_kW=("需量_kW", "mean"), 天數=("day", "nunique")).round(1)
        yt["最高發生"] = y.loc[y.groupby("年")["需量_kW"].idxmax(), ["年", "時間"]].set_index("年")["時間"]
        st.dataframe(yt, use_container_width=True)
        st.caption("年最高為台電網頁 15 分鐘值，含異常事件（如 2025-05-22 儲冰主機誤啟動），與定案總表口徑可能不同，對外引用以總表為準。")
        ldc = y.copy()
        ldc["rank"] = ldc.groupby("年")["需量_kW"].rank(ascending=False, pct=True) * 100
        ldc["年"] = ldc["年"].astype(str)
        st.altair_chart(alt.Chart(ldc.sample(min(len(ldc), 20000), random_state=1)).mark_line().encode(
            x=alt.X("rank:Q", title="時間百分比 (%)"), y=alt.Y("需量_kW:Q", title="需量 (kW)"), color="年:N"
        ).properties(height=320, title="負載持續曲線"), use_container_width=True)

    # ---- 資料品質 ----
    with tabs[5]:
        cnt = tc.day_counts(df)
        short = cnt[(cnt < 96) & (cnt.index < str(dmax))]
        all_days = pd.date_range(dmin, dmax - timedelta(days=1)).strftime("%Y-%m-%d")
        missing = sorted(set(all_days) - set(cnt.index))
        st.markdown(f"資料期間 {dmin} → {dmax}，共 {len(cnt)} 天、{len(df):,} 筆。")
        st.markdown(f"不完整日（< 96 筆）：{len(short)} 天；完全缺漏：{len(missing)} 天。執行 `taipower_backfill.py` 可重抓。")
        st.dataframe(short.rename("筆數").reset_index().rename(columns={"index": "日期"}), hide_index=True)
        if missing:
            st.write("缺漏日：", ", ".join(missing[:50]))


st.title("⚡ 中創園區 台電計費電表即時需量")
if hasattr(st, "fragment"):
    st.fragment(run_every="5min")(live_section)()     # 每 5 分鐘自動重讀資料
else:
    live_section()
