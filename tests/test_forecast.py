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
        self.assertEqual((fc["melt_start"], fc["melt_end"]), ("16:00", "19:30"))

    def test_non_summer_keeps_partial_limit(self):
        now = datetime(2026, 12, 1, 18, 0, tzinfo=TZ)  # 明日 12/2 週三，非夏月
        fc = compute_forecast(fake_weather(now), ForecastInputs(), now, False, False)
        self.assertLess(fc["calc_tmr"]["16:00"]["h_shaved"], MAG_PEAK_OFF_KW)
        self.assertFalse(fc["tmr_has_peak"])


if __name__ == "__main__":
    unittest.main()
