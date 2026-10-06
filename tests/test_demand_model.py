import os, sys, unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import calibrate
import demand_model as dm
import forecast
from forecast import ForecastInputs, compute_forecast
from test_auto_log import TZ, fake_weather


def synth_rows(days=40, start=date(2026, 7, 1)):
    """合成資料：y = 小時基準 + 10×(T−28) − 0.05×(R−400)；週末較低；夏月平日 16、17 點磁浮全關少 75 kW。"""
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        hol = d.weekday() >= 5
        for h in dm.HOURS:
            T = 28 + (i % 5) - 2 + (h - 12) * 0.3
            R = max(0.0, 800 - abs(h - 12) * 130) * (0.5 + (i % 3) * 0.25)
            base = 150 if hol else 300 + 10 * h
            off = not hol and h in (16, 17)
            y = base + 10 * (T - 28) - 0.05 * (R - 400) - (75 if off else 0)
            CC = 50 + (i % 7)
            rows.append({"date": d, "hour": h, "daytype": dm.day_type(hol), "y": y, "mag_off": off,
                         "T": T, "RH": 70 + (i % 4), "CC": CC, "R": R, "P": 0.0,
                         "pv": max(0.0, 5 + 0.08 * R - 0.2 * CC)})
    return rows


class DemandModelTests(unittest.TestCase):
    def test_fit_recovers_weather_effect_and_mag_off(self):
        rows = synth_rows()
        m = dm.fit(rows, rows[-1]["date"], alpha=0.0)
        r = next(x for x in rows if x["daytype"] == "work" and x["hour"] == 14)
        self.assertAlmostEqual(dm.predict_hour(m, "work", 14, r), r["y"], delta=1.0)
        r16 = next(x for x in rows if x["daytype"] == "work" and x["hour"] == 16)
        self.assertAlmostEqual(dm.predict_hour(m, "work", 16, r16, mag_off=True), r16["y"], delta=1.0)
        # 非夏月 16 點磁浮照常開：預測要把 75 kW 加回來
        self.assertAlmostEqual(dm.predict_hour(m, "work", 16, r16, mag_off=False) - r16["y"], dm.MAG_OFF_KW, delta=1.0)

    def test_missing_weather_falls_back_to_mean(self):
        m = dm.fit(synth_rows(), date(2026, 8, 9))
        self.assertIsNotNone(dm.predict_hour(m, "work", 10, {}))
        self.assertIsNone(dm.predict_hour(m, "nope", 10, {}))

    def test_fit_pv_recovers_and_clamps(self):
        rows = synth_rows()
        m = {"pv": dm.fit_pv(rows, rows[-1]["date"], halflife=1e9)}
        r = next(x for x in rows if x["hour"] == 12 and x["pv"] > 20)
        self.assertAlmostEqual(dm.predict_pv(m, 12, r), r["pv"], delta=3.0)
        self.assertEqual(dm.predict_pv(m, 12, {"R": -1e5, "CC": 0}), 0.0)
        self.assertIsNone(dm.predict_pv(m, 12, {"R": 500}))
        self.assertIsNone(dm.fit_pv(rows[:50], rows[-1]["date"]))   # 不足 14 天

    def test_quantile(self):
        self.assertEqual(dm.quantile([1, 2, 3, 4, 5], 0.5), 3)
        self.assertAlmostEqual(dm.quantile([0, 10], 0.95), 9.5)

    def test_load_missing_file(self):
        self.assertIsNone(dm.load("/nonexistent/model.json"))

    def test_shipped_model_loads(self):
        m = dm.load()
        self.assertIsNotNone(m)
        self.assertIn("work", m["coef"])
        self.assertIn("semi", m["resid_q"]["work"])


class CalibrateTests(unittest.TestCase):
    def test_parse_actual_skips_blank(self):
        a = calibrate.parse_actual([["日期", "時間", "需量"], ["2026-08-28", "11:00", "515.8"], ["2026/8/29", "10:00", ""]])
        self.assertEqual(a, {(date(2026, 8, 28), 11): 515.8})

    def test_parse_openmeteo_requires_all_vars(self):
        j = {"hourly": {"time": ["2026-08-28T11:00", "2026-08-28T12:00"],
                        **{v + "_previous_day1": [1.0, None] for v in calibrate._OM_VARS.values()}}}
        self.assertEqual(list(calibrate.parse_openmeteo(j)), [(date(2026, 8, 28), 11)])

    def test_parse_pv_sheet_shifts_to_interval_start_and_needs_all_meters(self):
        head = [["系統名稱", "紀錄時間", "V", "A", "當前功率(W)", "kWh"]]
        meters = ["BIPV-1", "BIPV-2", "斜坡PV", "鋼構PV"]
        rows = head + [[m, f"2026-08-28 {t}", "220", "1", "10000", "1"] for m in meters for t in ("10:15:00", "10:30:00", "10:45:00", "11:00:00")]
        rows += [[m, "2026-08-28 11:15:00", "220", "1", "10000", "1"] for m in meters[:3]]   # 少一組 → 不採用
        pv = calibrate.parse_pv_sheet(rows)
        self.assertEqual(pv, {(date(2026, 8, 28), 10): 40.0})

    def test_calibrate_rolling_metrics(self):
        m = calibrate.calibrate(synth_rows(days=35))
        self.assertLess(m["metrics"]["work"]["mae"], 5)
        self.assertIn("semi", m["resid_q"]["work"])
        self.assertEqual(m["trained_through"], "2026-08-04")
        self.assertIn("pv", m["metrics"])
        self.assertLess(m["metrics"]["pv"]["mae"], 5)


def model_weather(now):
    w = fake_weather(now)
    for key in ("model_wx_today", "model_wx_tmr"):
        w[key] = {h: {"T": 30.0, "RH": 70.0, "CC": 50.0, "R": 500.0, "P": 0.0} for h in range(24)}
    return w


class CalibratedForecastTests(unittest.TestCase):
    def setUp(self):
        self.model = dm.fit(synth_rows(), date(2026, 8, 9))
        self.model["resid_q"] = {"work": {"semi": {"q90": 30.0, "q95": 50.0}, "peak": {"q90": 10.0, "q95": 20.0}}}
        self.now = datetime(2026, 9, 29, 18, 0, tzinfo=TZ)   # 明日 9/30 週三，夏月

    def test_hourly_values_come_from_model(self):
        w = model_weather(self.now)
        fc = compute_forecast(w, ForecastInputs(), self.now, False, False, model=self.model)
        self.assertTrue(fc["model_used"])
        exp = dm.predict_hour(self.model, "work", 10, w["model_wx_tmr"][10])
        self.assertAlmostEqual(fc["calc_tmr"]["10:00"]["h_net"], exp, places=6)
        self.assertEqual(len(fc["calc_tmr"]), 6)   # fake_weather 只給 6 個時段
        semi = fc["risk"]["semi"]
        self.assertEqual(semi["limit"], 516.0)
        self.assertEqual(semi["action"], 516.0 - forecast.DL_ACTION_MARGIN)
        self.assertAlmostEqual(semi["alert"], 516.0 * 0.9)
        self.assertAlmostEqual(semi["p95"] - semi["pred"], 50.0)

    def test_sidebar_adjustments_stack_on_model(self):
        w = model_weather(self.now)
        base = compute_forecast(w, ForecastInputs(), self.now, False, False, model=self.model)
        busy = compute_forecast(w, ForecastInputs(occupancy_rate=50), self.now, False, False, model=self.model)
        p_base = compute_forecast(w, ForecastInputs(), self.now, False, False, model=None)
        p_busy = compute_forecast(w, ForecastInputs(occupancy_rate=50), self.now, False, False, model=None)
        diff = p_base["calc_tmr"]["10:00"]["h_net"] - p_busy["calc_tmr"]["10:00"]["h_net"]
        self.assertGreater(diff, 30)
        self.assertAlmostEqual(base["calc_tmr"]["10:00"]["h_net"] - busy["calc_tmr"]["10:00"]["h_net"], diff, places=6)

    def test_reserve_ice_when_close_to_limit(self):
        w = model_weather(self.now)
        fc = compute_forecast(w, ForecastInputs(), self.now, False, False, model=self.model)
        close = fc["risk"]["semi"]["margin"] < forecast.RISK_TRIGGER_MARGIN
        self.assertEqual(fc["reserve_rthr"] > 0, close)
        self.assertTrue(any("480" in p or "降至 50%" in p for p in fc["mag_plan"]))

    def test_solar_tile_uses_pv_model(self):
        self.model["pv"] = dm.fit_pv(synth_rows(), date(2026, 8, 9))
        w = model_weather(self.now)
        fc = compute_forecast(w, ForecastInputs(), self.now, False, False, model=self.model)
        self.assertAlmostEqual(fc["est_solar"], dm.day_pv(self.model, w["model_wx_tmr"]))

    def test_without_model_weather_uses_physics(self):
        fc = compute_forecast(fake_weather(self.now), ForecastInputs(), self.now, False, False, model=self.model)
        self.assertFalse(fc["model_used"])
        self.assertEqual(fc["risk"], {})


class RewarmTests(unittest.TestCase):
    def test_off_days_before(self):
        hol = lambda d: d.weekday() >= 5 or d == date(2026, 10, 9)   # 週五 10/9 補假
        self.assertEqual(forecast.off_days_before(date(2026, 10, 12), hol), 3)   # 週一，前面五六日
        self.assertEqual(forecast.off_days_before(date(2026, 10, 6), hol), 0)    # 週二

    def test_return_from_weekend_stores_more_ice(self):
        now = datetime(2026, 9, 27, 18, 0, tzinfo=TZ)   # 週日晚上，明天週一
        w = fake_weather(now)
        base = compute_forecast(w, ForecastInputs(), now, True, False, model=None)
        back = compute_forecast(w, ForecastInputs(), now, True, False, model=None, prev_off_days=2)
        self.assertEqual(base["rewarm_rthr"], 0.0)
        self.assertAlmostEqual(back["suggested_ice_hrs"] - base["suggested_ice_hrs"], back["rewarm_hrs"], places=6)
        self.assertAlmostEqual(back["rewarm_hrs"], 2 * forecast.REWARM_RTHR_PER_DAY * 1.2 / forecast.ICE_CHILLER_CAP_RT)
        self.assertLess(back["start_time_str"], base["start_time_str"])

    def test_no_rewarm_when_tomorrow_is_off(self):
        now = datetime(2026, 9, 25, 18, 0, tzinfo=TZ)
        fc = compute_forecast(fake_weather(now), ForecastInputs(), now, False, True, model=None, prev_off_days=3)
        self.assertEqual(fc["rewarm_rthr"], 0.0)


if __name__ == "__main__":
    unittest.main()
