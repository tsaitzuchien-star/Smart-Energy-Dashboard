import os, sys, unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime
from forecast import (ForecastInputs, MAG_PEAK_OFF_KW, compute_forecast, contract_limit_at,
                      day_min_limit, tou_period)
from test_auto_log import TZ, fake_weather


class ContractLimitTests(unittest.TestCase):
    def test_summer_weekday(self):
        d = date(2026, 7, 8)  # 週三
        self.assertEqual(tou_period(d, 8), "離峰")
        self.assertEqual(contract_limit_at(d, 8), 616.0)
        self.assertEqual(contract_limit_at(d, 9), 516.0)
        self.assertEqual(contract_limit_at(d, 15), 516.0)
        self.assertEqual(contract_limit_at(d, 16), 452.0)
        self.assertEqual(contract_limit_at(d, 21), 452.0)
        self.assertEqual(contract_limit_at(d, 22), 516.0)
        self.assertEqual(day_min_limit(d), 452.0)

    def test_summer_saturday_and_sunday(self):
        sat, sun = date(2026, 7, 11), date(2026, 7, 12)
        self.assertEqual(tou_period(sat, 18), "週六半尖峰")
        self.assertEqual(tou_period(sat, 8), "離峰")
        self.assertEqual(day_min_limit(sat), 616.0)
        self.assertEqual(tou_period(sun, 18), "離峰")
        self.assertEqual(day_min_limit(sun), 616.0)

    def test_non_summer_weekday(self):
        d = date(2026, 12, 2)  # 週三
        self.assertEqual(contract_limit_at(d, 5), 616.0)
        self.assertEqual(contract_limit_at(d, 6), 516.0)
        self.assertEqual(contract_limit_at(d, 12), 616.0)
        self.assertEqual(contract_limit_at(d, 14), 516.0)
        self.assertEqual(contract_limit_at(d, 18), 516.0)
        self.assertEqual(day_min_limit(d), 516.0)

    def test_season_boundaries(self):
        self.assertEqual(contract_limit_at(date(2026, 5, 15), 17), 516.0)  # 週五，非夏月
        self.assertEqual(contract_limit_at(date(2026, 10, 15), 17), 452.0)  # 週四，夏月最後一天
        self.assertEqual(contract_limit_at(date(2026, 10, 16), 17), 516.0)



class PeakMagOffTests(unittest.TestCase):
    def test_summer_weekday_peak_mag_off(self):
        now = datetime(2026, 9, 29, 18, 0, tzinfo=TZ)  # 明日 9/30 週三，夏月
        fc = compute_forecast(fake_weather(now), ForecastInputs(), now, False, False)
        self.assertEqual(fc["calc_tmr"]["16:00"]["h_shaved"], MAG_PEAK_OFF_KW)
        self.assertLess(fc["calc_tmr"]["14:00"]["h_shaved"], MAG_PEAK_OFF_KW)
        self.assertTrue(fc["tmr_has_peak"])
        self.assertEqual((fc["melt_start"], fc["melt_end"]), ("10:50", "19:00"))

    def test_non_summer_keeps_partial_limit(self):
        now = datetime(2026, 12, 1, 18, 0, tzinfo=TZ)  # 明日 12/2 週三，非夏月
        fc = compute_forecast(fake_weather(now), ForecastInputs(), now, False, False)
        self.assertLess(fc["calc_tmr"]["16:00"]["h_shaved"], MAG_PEAK_OFF_KW)
        self.assertFalse(fc["tmr_has_peak"])

    def test_non_summer_workday_no_ice(self):
        now = datetime(2026, 10, 19, 18, 0, tzinfo=TZ)  # 明日 10/20 週二，非夏月：現場不儲冰
        fc = compute_forecast(fake_weather(now), ForecastInputs(), now, False, False)
        if fc["max_net_grid_demand"] < fc["worst_limit_tmr"] - 15:
            self.assertEqual(fc["suggested_ice_hrs"], 0.0)
            self.assertEqual(fc["start_time_str"], "不儲冰")


if __name__ == "__main__":
    unittest.main()


def test_apply_peak_bias_uses_only_past_days():
    from datetime import date, timedelta
    import calibrate as c
    ev = []
    for i in range(30):
        d = date(2026, 1, 1) + timedelta(days=i)
        ev += [{"date": d, "hour": 10, "daytype": "work", "y": 310.0, "pred": 300.0},
               {"date": d, "hour": 16, "daytype": "work", "y": 300.0, "pred": 300.0},
               {"date": d, "hour": 10, "daytype": "off", "y": 150.0, "pred": 100.0}]
    out, final = c.apply_peak_bias(ev)
    first = [e for e in out if e["date"] == date(2026, 1, 1) and e["daytype"] == "work" and e["hour"] == 10][0]
    last = [e for e in out if e["date"] == date(2026, 1, 30) and e["daytype"] == "work" and e["hour"] == 10][0]
    assert first["pred"] == 300.0               # 前幾天資料不足不修正
    assert last["pred"] == 310.0                # 之後加回中位數 +10
    assert all(e["pred"] == 300.0 for e in out if e["hour"] == 16)
    assert all(e["pred"] == 100.0 for e in out if e["daytype"] == "off")
    assert final == {"work": {"semi": 10.0}}


def test_parse_taipower_hourly_max_complete_hours_only():
    import calibrate as c
    from datetime import date
    text = ("時間,時段,需量_kW,契約容量_kW,使用率_%\n"
            "2026-11-20 09:15,半尖峰,400,516,0\n2026-11-20 09:30,半尖峰,420,516,0\n"
            "2026-11-20 09:45,半尖峰,410,516,0\n2026-11-20 10:00,半尖峰,405,516,0\n"   # 09 時：4 筆齊
            "2026-11-20 10:15,半尖峰,430,516,0\n")                                       # 10 時只有 1 筆 → 不用
    tp = c.parse_taipower(text, 14.0)
    assert tp == {(date(2026, 11, 20), 9): 406.0}
    assert c.parse_taipower(text, 14.0, since=date(2026, 11, 21)) == {}
    assert c.merge_actual({(date(2026, 11, 20), 9): 1.0, (date(2026, 11, 20), 8): 2.0}, tp) == \
        {(date(2026, 11, 20), 9): 406.0, (date(2026, 11, 20), 8): 2.0}
