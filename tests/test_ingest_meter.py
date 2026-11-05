import os, sys, unittest
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ingest_meter as im

CSV = "﻿DateTime,Date,KmTagx019,KmTagx020\n" + "\n".join([
    "2026/10/16 10:00:00,2026/10/16,1,3792125.2",
    "2026/10/16 10:15:00,2026/10/16,1,3792215.3",   # 90.1 kWh → 360.4 kW
    "2026/10/16 10:30:00,2026/10/16,1,0",           # 通訊中斷記 0：略過
    "2026/10/16 10:45:00,2026/10/16,1,3792402.6",   # 跨 30 分鐘：不算
    "2026/10/16 11:00:00,2026/10/16,1,3792505.3",   # 102.7 → 410.8 kW
    "2026/10/16 11:14:59,2026/10/16,1,3792622.2",   # 116.9 → 約 467.6 kW，區間 11:00
    "2026/10/16 11:30:00,2026/10/16,1,3792745.7",   # 標記 11:15
    "2026/10/16 11:45:00,2026/10/16,1,3792867.7",
    "2026/10/16 12:00:00,2026/10/16,1,3792984.7",
])


class TestIngestMeter(unittest.TestCase):
    def setUp(self):
        self.r = im.parse_readings(CSV)
        self.iv = im.intervals(self.r, offset=10.0)

    def test_parse_skips_zero(self):
        self.assertEqual(len(self.r), 8)

    def test_intervals(self):
        self.assertAlmostEqual(self.iv[datetime(2026, 10, 16, 10, 0)], 350.4)
        self.assertNotIn(datetime(2026, 10, 16, 10, 15), self.iv)      # 缺值後的長間隔
        self.assertAlmostEqual(self.iv[datetime(2026, 10, 16, 10, 45)], 400.8)
        self.assertIn(datetime(2026, 10, 16, 11, 0), self.iv)           # 11:00 → 11:14:59
        self.assertIn(datetime(2026, 10, 16, 11, 15), self.iv)          # 11:14:59 視為 11:15 起
        self.assertEqual(len(self.iv), 6)

    def test_hourly_only_complete_hours(self):
        h = im.hourly_max(self.iv, self.r[-1][0])
        self.assertEqual(sorted(h), [datetime(2026, 10, 16, 10), datetime(2026, 10, 16, 11)])
        self.assertAlmostEqual(h[datetime(2026, 10, 16, 11)], max(
            v for k, v in self.iv.items() if k.hour == 11))

    def test_holiday_weekday_is_off_peak(self):
        self.assertEqual(im.period_of(date(2026, 10, 9), 11, lambda d: True), "離峰")
        self.assertEqual(im.period_of(date(2026, 10, 16), 10, lambda d: False), "半尖峰")
        self.assertEqual(im.period_of(date(2026, 10, 16), 11, lambda d: False), "離峰")  # 非夏月 11–14 離峰

    def test_daily_row(self):
        rows = im.daily_rows(self.iv, lambda d: False, date(2026, 10, 17))
        row = rows[date(2026, 10, 16)]
        self.assertEqual(row[1], "五")
        self.assertEqual(row[4], "離峰")        # 最高在 11 點，非夏月離峰
        self.assertEqual(row[5], 616)
        self.assertEqual(row[7], "10:45 半尖峰")   # 距契約最近的是 10:45 半尖峰
        self.assertEqual(row[-1], round(100 * 6 / 96))
        self.assertEqual(im.daily_rows(self.iv, lambda d: False, date(2026, 10, 16)), {})  # 當天未結束


if __name__ == "__main__":
    unittest.main()
