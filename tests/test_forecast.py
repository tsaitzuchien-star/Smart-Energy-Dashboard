import os, sys, unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from forecast import contract_limit_at, day_min_limit, tou_period


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


if __name__ == "__main__":
    unittest.main()
