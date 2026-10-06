import os, sys, unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import trend

ACTUAL = [
    ["日期", "時間", "需量(kW)"],
    ["2026-08-28", "03:00", "454.6"],     # 製冰，離峰 616，不算日間最高
    ["2026-08-28", "11:00", "515.8"],     # 半尖峰 516，餘裕 0.2
    ["2026-08-28", "17:00", "313"],       # 尖峰 452
    ["2026/8/29", "11:00:00", "48.2"],    # 週六，斜線日期與秒數也要能讀
    ["2026-08-31", "10:00", ""],          # 監控停機：空白不列入
]
LOG = [
    ["紀錄時間", "明日預估最高需量(kW)", "資料版本"],
    ["2026-08-27 18:00:00", "400", ""],                 # 舊版假資料，略過
    ["2026-08-27 18:00:00", "480.5", "v2-實算預測"],
    ["2026-08-27 20:00:00", "490", "v2-實算預測"],      # 同日後寫的蓋前面
]
COMPARE = [
    ["日期", "時間", "星期", "台電時段", "契約上限(kW)", "預測需量(kW)", "實測需量(kW)"],
    ["2026-08-28", "10:00", "五", "半尖峰", "516", "470.2", ""],
]


class TrendTests(unittest.TestCase):
    def test_parse_actual_skips_blanks_and_reads_formats(self):
        a = trend.parse_actual(ACTUAL)
        self.assertEqual(a[(date(2026, 8, 28), 11)], 515.8)
        self.assertEqual(a[(date(2026, 8, 29), 11)], 48.2)
        self.assertNotIn((date(2026, 8, 31), 10), a)

    def test_forecast_log_uses_v2_and_targets_next_day(self):
        self.assertEqual(trend.parse_forecast_log(LOG), {date(2026, 8, 28): 490.0})
        self.assertEqual(trend.parse_forecast_log([["別的標題"]]), {})

    def test_daily_table_daytime_max_and_margin(self):
        df = trend.daily_table(trend.parse_actual(ACTUAL), trend.parse_forecast_log(LOG))
        r = df[df["日期"] == "2026-08-28"].iloc[0]
        self.assertEqual(r["實測日間最高(kW)"], 515.8)
        self.assertEqual(r["預測最高(kW)"], 490.0)
        self.assertEqual(r["誤差(kW)"], 25.8)
        self.assertEqual(r["距契約最近(kW)"], 0.2)
        self.assertEqual(r["距契約最近時段"], "11:00 半尖峰")

    def test_daily_from_hourly_takes_max_and_log_wins(self):
        fc = trend.parse_compare(COMPARE + [["2026-08-28", "12:00", "五", "半尖峰", "516", "488.4", ""],
                                            ["2026-08-29", "12:00", "六", "半尖峰", "616", "50", ""]])
        daily = trend.daily_from_hourly(fc)
        self.assertEqual(daily, {date(2026, 8, 28): 488.4, date(2026, 8, 29): 50.0})
        merged = {**daily, **trend.parse_forecast_log(LOG)}
        self.assertEqual(merged[date(2026, 8, 28)], 490.0)
        self.assertEqual(merged[date(2026, 8, 29)], 50.0)

    def test_hourly_table_limits_follow_periods(self):
        h = trend.hourly_table(date(2026, 8, 28), trend.parse_actual(ACTUAL), trend.parse_compare(COMPARE))
        self.assertEqual(len(h), 24)
        self.assertEqual(h.loc[17, "契約上限(kW)"], 452)
        self.assertEqual(h.loc[11, "契約上限(kW)"], 516)
        self.assertEqual(h.loc[3, "契約上限(kW)"], 616)
        self.assertEqual(h.loc[10, "預測(kW)"], 470.2)


if __name__ == "__main__":
    unittest.main()
