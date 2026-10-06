"""需量預測引擎（純 Python，不依賴 Streamlit）。

app.py 與 auto_log.py 共用同一份運算，避免兩邊數字不一致。
公式與原 app.py V3.9.5 決策大腦逐行對應；今日／明日兩段重複迴圈合併為 _hourly_loop()。
"""
from dataclasses import dataclass
from datetime import timedelta

from weather import TARGET_HOURS

# --- 原廠硬體規格 ---
ICE_CHILLER_KW = 241.0
ICE_CHILLER_CAP_RT = 242.5
ICE_BANK_MAX_RTHR = 2500.0
MAG_CHILLER_RT = 200.0
MAG_CAP_LIMIT = 0.50
AC_START, AC_END = "07:30", "18:00"          # 園區空調供應時間
PEAK_MELT_HRS = 2.0                          # 夏月平日 16:00–18:00 融冰全量取代磁浮
MAG_EFF = 0.7
MAG_PEAK_OFF_KW = MAG_CHILLER_RT * MAG_EFF   # 尖峰時段磁浮全關，冷房全由融冰供應
SOLAR_MAX_KW = 145.0

SOLAR_AUTO = "🤖 API 短波輻射精準推算"
SOLAR_MANUAL = "✋ 廠務手動強制設定"
AHU_AUTO = "🤖 溫控動態演算 (Auto)"
OVERTIME_ONTIME = "🌇 18:00 準時下班 (啟動夜間降載)"

# --- 台電契約容量（高壓三段式時間電價，112 年新制）---
CONTRACT_PEAK_KW = 452.0       # 尖峰：夏月週一～五 16:00–22:00
CONTRACT_SEMI_PEAK_KW = 516.0  # 半尖峰
CONTRACT_OFF_PEAK_KW = 616.0   # 週六半尖峰及離峰

# 歷史各月最高需量 (kW)
HISTORICAL_MAX_DEMAND = {1: 274, 2: 262, 3: 286, 4: 366, 5: 362, 6: 502, 7: 510, 8: 504, 9: 468, 10: 460, 11: 500, 12: 394}


def is_summer_day(d):
    """夏月：6/1–9/30，另含 5/16 之後與 10/15 之前。"""
    if 6 <= d.month <= 9:
        return True
    if d.month == 5 and d.day >= 16:
        return True
    if d.month == 10 and d.day <= 15:
        return True
    return False


def tou_period(d, hour):
    """台電高壓三段式時段。d＝日期，hour＝0–23。

    週六、週日依星期判斷；平日國定假日仍以平日時段計（台電離峰日清單與行事曆不完全相同，取保守值）。
    """
    wd = d.weekday()
    if wd == 6:
        return "離峰"
    if is_summer_day(d):
        if wd == 5:
            return "週六半尖峰" if hour >= 9 else "離峰"
        if 16 <= hour < 22:
            return "尖峰"
        return "半尖峰" if hour >= 9 else "離峰"
    in_day = 6 <= hour < 11 or hour >= 14
    if wd == 5:
        return "週六半尖峰" if in_day else "離峰"
    return "半尖峰" if in_day else "離峰"


_PERIOD_LIMIT = {"尖峰": CONTRACT_PEAK_KW, "半尖峰": CONTRACT_SEMI_PEAK_KW,
                 "週六半尖峰": CONTRACT_OFF_PEAK_KW, "離峰": CONTRACT_OFF_PEAK_KW}


def contract_limit_at(d, hour):
    """該日該小時的契約上限 (kW)。"""
    return _PERIOD_LIMIT[tou_period(d, hour)]


def day_min_limit(d):
    """該日最嚴格的契約上限 (kW)。"""
    return min(contract_limit_at(d, h) for h in range(24))


def get_cloud_penalty(status_code, c_low, c_mid):
    if status_code == 1: 
        if c_low > 20 or (c_low + c_mid) > 40:
            penalty = 1.0 - ((c_low * 0.85 + c_mid * 0.45) / 100.0)
            return max(0.1, penalty)
    elif status_code == 2:
        if c_low > 50:
            penalty = 1.0 - (c_low * 0.75 / 100.0)
            return max(0.15, penalty)
    return 1.0

def get_smoothed_temp(h_str, is_tmr, w_data):
    try:
        h_int = int(h_str[:2])
        temps = []
        for offset in [0, 1, 2]:
            target_h = h_int - offset
            if is_tmr:
                if target_h >= 0: t_val = w_data["all_temps_tmr"].get(f"{target_h:02d}:00", 25.0)
                else: t_val = w_data["all_temps_today"].get(f"{24+target_h:02d}:00", 25.0)
            else:
                if target_h >= 0: t_val = w_data["all_temps_today"].get(f"{target_h:02d}:00", 25.0)
                else: t_val = 25.0 
            temps.append(t_val)
        return temps[0] * 0.5 + temps[1] * 0.3 + temps[2] * 0.2
    except:
        return 25.0


@dataclass
class ForecastInputs:
    """側邊欄可調參數（預設值＝App 預設值，auto_log.py 直接用預設）。"""
    conf_hall_status: str = "無活動 (0 RT-HR)"
    expo_hall_status: str = "無活動 (0 RT-HR)"
    occupancy_rate: float = 100
    overtime_status: str = OVERTIME_ONTIME
    chiller_compensation: float = 100.0
    solar_mode: str = SOLAR_AUTO
    manual_solar: float = 80.0
    ahu_mode: str = AHU_AUTO
    hidden_ahu_load: float = 23.0
    emergency_mode: bool = False
    emergency_mag_limit_pct: int = int(MAG_CAP_LIMIT * 100)
    emergency_ahu_drop: float = 0.0

    @property
    def active_mag_limit(self):
        return self.emergency_mag_limit_pct / 100.0


def _hourly_loop(w, inp, *, is_tmr, day, is_holiday, event_kw, base_load, actual_load, shaved_kw):
    """逐時計算一天（今日或明日）。回傳 (calc, worst)；worst 為缺口最大的時段。"""
    hourly = w["hourly"] if is_tmr else w["today_hourly"]
    temps_key = "all_temps_tmr" if is_tmr else "all_temps_today"
    max_rad = max([hourly[h]["rad"] for h in TARGET_HOURS if h in hourly] + [1])

    calc = {}
    max_gap = -9999.0
    worst = {"net": 0.0, "hour": "未知", "load": 0.0, "solar": 0.0}

    for h in TARGET_HOURS:
        if h not in hourly:
            continue
        h_data = hourly[h]
        h_temp, h_rad = h_data["temp"], h_data["rad"]
        c_low, c_mid = h_data.get("c_low", 0), h_data.get("c_mid", 0)

        smoothed_temp = get_smoothed_temp(h, is_tmr, w) if temps_key in w else h_temp
        cp = get_cloud_penalty(w["status_code"], c_low, c_mid)
        if inp.solar_mode == SOLAR_AUTO:
            h_solar = SOLAR_MAX_KW * min(1.0, h_rad / 1000.0) * cp
        else:
            h_solar = min(inp.manual_solar, inp.manual_solar * (h_rad / max_rad if max_rad > 0 else 0))

        shading_factor = 1.0
        if h_solar < 20.0:
            shading_factor = 0.5
        elif h_solar < 50.0:
            shading_factor = 0.7

        if h == "08:00":
            h_ahu = 0.0
        elif inp.ahu_mode == AHU_AUTO:
            h_ahu = 23.0 + (inp.occupancy_rate / 100.0) * min(14.0, max(0, (smoothed_temp - 25.0) * 1.5))
        else:
            h_ahu = inp.hidden_ahu_load

        if inp.emergency_mode:  # 兵推：AHU 降載扣除
            h_ahu = max(0.0, h_ahu - inp.emergency_ahu_drop)

        dynamic_load = (h_ahu + max(0, (smoothed_temp - 25.0) * 5.5)) * shading_factor

        hour_int = int(h[:2])
        period = tou_period(day, hour_int)
        h_shaved = MAG_PEAK_OFF_KW if period == "尖峰" else shaved_kw
        if hour_int >= 18:  # 18:00 動態卸載與下班邏輯
            if is_holiday:
                h_load = 160.0 + event_kw
            elif inp.overtime_status == OVERTIME_ONTIME:
                h_load = 160.0
            else:
                h_load = base_load + (actual_load * 0.3) + (dynamic_load * 0.5) - h_shaved
        else:
            h_load = (160.0 + event_kw) if is_holiday else base_load + actual_load + dynamic_load - h_shaved

        h_net = h_load - h_solar
        limit = _PERIOD_LIMIT[period]
        gap = h_net - limit

        calc[h] = {"temp": h_temp, "rad": h_rad, "wx": h_data["wx"], "c_low": c_low, "c_mid": c_mid,
                   "c_high": h_data.get("c_high", 0), "cp": cp, "h_solar": h_solar, "h_load": h_load,
                   "h_net": h_net, "shading_factor": shading_factor, "current_limit": limit, "period": period, "h_shaved": h_shaved}

        if gap > max_gap:
            max_gap = gap
            worst = {"net": h_net, "hour": h, "load": h_load, "solar": h_solar}
    return calc, worst


def compute_forecast(w, inp, now_dt, today_is_holiday, tmr_is_holiday):
    """主運算。w＝weather.fetch_smart_weather() 的結果；回傳所有 UI／紀錄需要的數值。"""
    tmr_dt = now_dt + timedelta(days=1)
    current_month = now_dt.month
    is_summer_today = is_summer_day(now_dt)
    is_summer_tmr = is_summer_day(tmr_dt)
    contract_limit = day_min_limit(tmr_dt)
    season_tag = "夏月(新制)" if is_summer_tmr else "非夏月"
    base_load_historical = HISTORICAL_MAX_DEMAND.get(current_month, 400)
    api_is_online = w["status_code"] > 0

    ice_rest = inp.chiller_compensation if 1 <= current_month <= 5 else 0.0
    base_load = base_load_historical + ice_rest          # 今日／明日相同
    actual_load = 70.0 * (inp.occupancy_rate / 100.0)
    shaved_kw = MAG_CHILLER_RT * (1.0 - inp.active_mag_limit) * MAG_EFF

    event_ice_rthr = 0.0
    if "半天" in inp.conf_hall_status: event_ice_rthr += 75.0
    elif "全天" in inp.conf_hall_status: event_ice_rthr += 150.0
    if "半天" in inp.expo_hall_status: event_ice_rthr += 125.0
    elif "全天" in inp.expo_hall_status: event_ice_rthr += 250.0
    event_kw = (event_ice_rthr / 6.0) * MAG_EFF if event_ice_rthr > 0 else 0.0

    calc_today, calc_tmr = {}, {}
    today_max_net, today_worst_hour = 0.0, "未知"
    max_net_grid_demand, worst_hour, worst_hour_load, worst_hour_solar = 0.0, "未知", 0.0, 0.0
    worst_limit_tmr = contract_limit

    if api_is_online:
        calc_today, wt = _hourly_loop(w, inp, is_tmr=False, day=now_dt.date(), is_holiday=today_is_holiday,
                                      event_kw=0.0, base_load=base_load, actual_load=actual_load, shaved_kw=shaved_kw)
        today_max_net, today_worst_hour = wt["net"], wt["hour"]
        calc_tmr, wm = _hourly_loop(w, inp, is_tmr=True, day=tmr_dt.date(), is_holiday=tmr_is_holiday,
                                    event_kw=event_kw, base_load=base_load, actual_load=actual_load, shaved_kw=shaved_kw)
        max_net_grid_demand, worst_hour = wm["net"], wm["hour"]
        worst_hour_load, worst_hour_solar = wm["load"], wm["solar"]

        avg_cp = sum([calc_tmr[h]["cp"] for h in calc_tmr]) / len(calc_tmr) if calc_tmr else 1.0
        if inp.solar_mode == SOLAR_AUTO:
            est_solar = SOLAR_MAX_KW * min(1.0, w.get("tmr_rad", 400) / 1000.0) * avg_cp
        else:
            est_solar = inp.manual_solar
        worst_limit_tmr = calc_tmr[worst_hour]["current_limit"] if worst_hour in calc_tmr else contract_limit
    else:
        # 斷線盲估模式
        h_solar_blind = inp.manual_solar if inp.solar_mode == SOLAR_MANUAL else SOLAR_MAX_KW * 0.4
        shading_blind = 0.5 if h_solar_blind < 20.0 else (0.7 if h_solar_blind < 50.0 else 1.0)
        if inp.ahu_mode == AHU_AUTO:
            ahu_blind = 23.0 + (inp.occupancy_rate / 100.0) * min(14.0, max(0, (28.0 - 25.0) * 1.5))
        else:
            ahu_blind = inp.hidden_ahu_load
        if inp.emergency_mode:
            ahu_blind = max(0.0, ahu_blind - inp.emergency_ahu_drop)
        if tmr_is_holiday:
            h_load_blind = 160.0 + event_kw
        else:
            h_load_blind = base_load + actual_load + (ahu_blind + max(0, (28.0 - 25.0) * 5.5)) * shading_blind - shaved_kw
        today_max_net, today_worst_hour = h_load_blind - h_solar_blind, "斷線盲估"
        max_net_grid_demand, worst_hour = h_load_blind - h_solar_blind, "斷線盲估"
        worst_hour_load, worst_hour_solar = h_load_blind, h_solar_blind
        est_solar = h_solar_blind
        worst_limit_tmr = contract_limit

    demand_gap = max_net_grid_demand - (worst_limit_tmr - 15.0)
    needed_ice_rthr_for_grid = (demand_gap / MAG_EFF) * 6.0 if demand_gap > 0 else 0

    tmr_has_peak = tou_period(tmr_dt.date(), 16) == "尖峰"
    if tmr_is_holiday:
        extra_ice_rthr_for_cooling = 0.0
    elif tmr_has_peak:  # 16:00 起磁浮全關，融冰全量供冷到空調結束
        extra_ice_rthr_for_cooling = MAG_CHILLER_RT * PEAK_MELT_HRS
    else:
        extra_ice_rthr_for_cooling = MAG_CHILLER_RT * (1.0 - inp.active_mag_limit) * 4.0
    extra_ice_rthr_for_cooling += event_ice_rthr

    is_pure_holiday = tmr_is_holiday and event_ice_rthr == 0.0
    is_holiday_event = tmr_is_holiday and event_ice_rthr > 0.0

    if is_pure_holiday or is_holiday_event:
        suggested_ice_hrs = 0.0
        start_time_str, end_time_str = "關閉排程", "關閉排程"
        time_color = "#dc3545" if is_pure_holiday else "#28a745"
        if is_pure_holiday:
            melt_start, melt_end, melt_memo = "關閉排程", "關閉排程", "*明日為純假日，務必手動關閉自動排程！"
        else:
            melt_start, melt_end, melt_memo = "停用融冰", "直供冰水", "*【省錢策略】假日全天離峰，建議直接開啟磁浮主機，免除儲冰耗損！"
    else:
        suggested_ice_hrs = max(1.5, min(9.0, ((needed_ice_rthr_for_grid + extra_ice_rthr_for_cooling) * 1.2) / ICE_CHILLER_CAP_RT))
        end_minutes = 7 * 60
        exact_start = int(end_minutes - (suggested_ice_hrs * 60))
        start_minutes = (exact_start // 10) * 10
        if start_minutes < 0:
            start_minutes += 24 * 60
        start_time_str, end_time_str = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}", "07:00"
        time_color = "#D2691E"
        if tmr_has_peak:
            melt_start, melt_end, melt_memo = "16:00", AC_END, f"*尖峰 16:00 起磁浮全關，融冰全量供冷至 {AC_END} 空調結束。"
        else:
            melt_start, melt_end, melt_memo = "10:00", "16:00", "*依 IB-1 設計 13°C 進水條件執行。"

    return {
        "api_is_online": api_is_online,
        "is_summer_today": is_summer_today, "is_summer_tmr": is_summer_tmr,
        "contract_limit": contract_limit, "season_tag": season_tag,
        "base_load_historical": base_load_historical,
        "active_mag_limit": inp.active_mag_limit,
        "calc_today": calc_today, "calc_tmr": calc_tmr,
        "today_max_net": today_max_net, "today_worst_hour": today_worst_hour,
        "max_net_grid_demand": max_net_grid_demand, "worst_hour": worst_hour,
        "worst_hour_load": worst_hour_load, "worst_hour_solar": worst_hour_solar,
        "worst_limit_tmr": worst_limit_tmr, "est_solar": est_solar,
        "event_ice_rthr": event_ice_rthr, "event_kw": event_kw,
        "tmr_true_base_load": base_load, "tmr_actual_load_growth": actual_load, "tmr_shaved_kw": calc_tmr[worst_hour]["h_shaved"] if worst_hour in calc_tmr else shaved_kw,
        "tmr_has_peak": tmr_has_peak,
        "is_pure_holiday": is_pure_holiday, "is_holiday_event": is_holiday_event,
        "suggested_ice_hrs": suggested_ice_hrs,
        "start_time_str": start_time_str, "end_time_str": end_time_str, "time_color": time_color,
        "melt_start": melt_start, "melt_end": melt_end, "melt_memo": melt_memo,
    }
