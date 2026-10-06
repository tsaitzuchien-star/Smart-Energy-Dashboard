import os, sys, unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backcast
import calendar_tw as c
from test_calendar_tw import make_year, HOLS, NOOV


def fake_hourly(start, days):
    t0 = datetime(start.year, start.month, start.day)
    times = [(t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(24 * days)]
    hour = [int(t[11:13]) for t in times]
    return {
        "time": times,
        "temperature_2m": [26 + 8 * (6 <= h <= 15) for h in hour],
        "shortwave_radiation": [max(0, 800 - 100 * abs(h - 12)) for h in hour],
        "cloud_cover": [20] * len(times), "cloud_cover_low": [10] * len(times),
        "cloud_cover_mid": [5] * len(times), "cloud_cover_high": [0] * len(times), "weather_code": [1] * len(times),
    }


class BackcastTests(unittest.TestCase):
    def setUp(self):
        self.cal = c.load_calendar([2026], getter=lambda u: make_year(2026, HOLS), overrides_path=NOOV)
        self.hourly = fake_hourly(date(2026, 8, 26), 4)

    def test_weather_shape_matches_live_parser(self):
        now = datetime(2026, 8, 27, 18, tzinfo=backcast.TW_TZ)
        w = backcast.weather_at(self.hourly, now)
        self.assertEqual(w["status_code"], 1)
        self.assertEqual(w["temp"], 26)                      # current＝18:00
        self.assertEqual(w["hourly"]["12:00"]["rad"], 800)   # 明日 12:00
        self.assertEqual(w["tmr_temp"], 34)                  # 明日 12–15 時最高

    def test_rows_one_per_forecast_hour_with_limits(self):
        rows = backcast.backcast_rows(date(2026, 8, 27), date(2026, 8, 29), self.hourly, "ecmwf_ifs", self.cal)
        days = {r[0] for r in rows}
        self.assertEqual(days, {"2026-08-27", "2026-08-28", "2026-08-29"})
        fri_16 = [r for r in rows if r[0] == "2026-08-28" and r[1] == "16:00"][0]
        self.assertEqual((fri_16[2], fri_16[3]), ("尖峰", 452))
        sat = [r for r in rows if r[0] == "2026-08-29"]
        self.assertTrue(all(r[3] == 616 for r in sat))
        for r in rows:
            self.assertEqual(len(r), len(backcast.HEADERS))


if __name__ == "__main__":
    unittest.main()
