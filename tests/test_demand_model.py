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
            rows.append({"date": d, "hour": h, "daytype": dm.day_type(hol), "y": y, "mag_off": off,
                         "T": T, "RH": 70 + (i % 4), "CC": 50 + (i % 7), "R": R, "P": 0.0})
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

    def test_calibrate_rolling_metrics(self):
        m = calibrate.calibrate(synth_rows(days=35))
        self.assertLess(m["metrics"]["work"]["mae"], 5)
        self.assertIn("semi", m["resid_q"]["work"])
        self.assertEqual(m["trained_through"], "2026-08-04")


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

    def test_without_model_weather_uses_physics(self):
        fc = compute_forecast(fake_weather(self.now), ForecastInputs(), self.now, False, False, model=self.model)
        self.assertFalse(fc["model_used"])
        self.assertEqual(fc["risk"], {})


if __name__ == "__main__":
    unittest.main()
