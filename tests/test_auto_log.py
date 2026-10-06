import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import auto_log
import calendar_tw as c
from test_calendar_tw import make_year, HOLS, NOOV

TZ = timezone(timedelta(hours=8))


def fake_weather(now):
    today, tmr = now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")
    hh = {h: {"temp": 30.0 + i, "rad": 200.0 * (i + 1), "c_low": 10, "c_mid": 5, "c_high": 0, "wx": "晴朗"}
          for i, h in enumerate(["08:00", "10:00", "12:00", "14:00", "16:00", "18:00"])}
    return {"fetch_time": now.strftime("%Y-%m-%d %H:%M:%S"), "status_code": 1, "source": "ECMWF", "wx": "晴朗",
            "cloud": 0, "rad": 500, "temp": 29.0, "tmr_temp": 33.5, "tmr_rad": 600, "cloud_low": 0, "cloud_mid": 0,
            "cloud_high": 0, "today_hourly": hh, "hourly": hh,
            "all_temps_today": {f"{h:02d}:00": 25.0 + h / 3 for h in range(24)},
            "all_temps_tmr": {f"{h:02d}:00": 25.0 + h / 3 for h in range(24)}, "temp_is_calibrated": False}


class AutoLogTests(unittest.TestCase):
    def setUp(self):
        self.cal = c.load_calendar([2026], getter=lambda u: make_year(2026, HOLS), overrides_path=NOOV)

    def test_row_matches_headers_and_is_real(self):
        now = datetime(2026, 9, 30, 18, 0, tzinfo=TZ)
        row = auto_log.build_row(now, fake_weather(now), self.cal)
        self.assertEqual(len(row), len(auto_log.HEADERS))
        d = dict(zip(auto_log.HEADERS, row))
        self.assertEqual(d["氣象來源"], "ECMWF")
        self.assertEqual(d["今日最高氣溫(°C)"], round(25.0 + 23 / 3, 1))
        self.assertEqual(d["今日最高輻射(W/m²)"], 1200.0)
        self.assertEqual(d["明日預估高溫(°C)"], 33.5)
        self.assertEqual(d["明日是否假日"], "否")
        self.assertEqual(d["資料版本"], auto_log.DATA_VERSION)

    def test_holiday_tomorrow_zero_ice(self):
        now = datetime(2026, 9, 24, 18, 0, tzinfo=TZ)        # 明日 9/25 放假
        d = dict(zip(auto_log.HEADERS, auto_log.build_row(now, fake_weather(now), self.cal)))
        self.assertEqual(d["明日是否假日"], "是")
        self.assertEqual(d["建議今晚儲冰(小時)"], 0.0)

    def test_compare_rows(self):
        now = datetime(2026, 9, 30, 18, 0, tzinfo=TZ)            # 明日 10/1 週四，夏月
        rows = auto_log.build_compare_rows(now, fake_weather(now), self.cal)
        self.assertEqual(len(rows), 6)
        for r in rows:
            self.assertEqual(len(r), len(auto_log.COMPARE_HEADERS))
        d = {r[1]: dict(zip(auto_log.COMPARE_HEADERS, r)) for r in rows}
        self.assertEqual(d["08:00"]["日期"], "2026-10-01")
        self.assertEqual(d["08:00"]["星期"], "四")
        self.assertEqual(d["08:00"]["契約上限(kW)"], 616.0)
        self.assertEqual(d["10:00"]["台電時段"], "半尖峰")
        self.assertEqual(d["16:00"]["契約上限(kW)"], 452.0)
        self.assertIn(auto_log.ACTUAL_TAB, d["16:00"]["實測需量(kW)"])
        self.assertTrue(d["16:00"]["誤差(實測−預測 kW)"].startswith("="))

    def test_compare_rows_offline_empty(self):
        now = datetime(2026, 9, 30, 18, 0, tzinfo=TZ)
        w = fake_weather(now); w["status_code"] = 0
        self.assertEqual(auto_log.build_compare_rows(now, w, self.cal), [])

    def test_write_compare_rows_creates_tab_and_skips_duplicates(self):
        try:
            import gspread
        except ImportError:
            self.skipTest("gspread 未安裝")

        class FakeWS:
            def __init__(self): self.data = []
            def row_values(self, i): return self.data[i - 1] if len(self.data) >= i else []
            def col_values(self, i): return [r[i - 1] for r in self.data]
            def update(self, range_name, values):
                if self.data: self.data[0] = values[0]
                else: self.data.append(values[0])
            def append_rows(self, rows, value_input_option=None): self.data.extend(rows)

        class FakeBook:
            def __init__(self): self.tabs = {}
            def worksheet(self, name):
                if name not in self.tabs: raise gspread.WorksheetNotFound(name)
                return self.tabs[name]
            def add_worksheet(self, title, rows, cols):
                self.tabs[title] = FakeWS(); return self.tabs[title]

        now = datetime(2026, 9, 30, 18, 0, tzinfo=TZ)
        rows = auto_log.build_compare_rows(now, fake_weather(now), self.cal)
        book = FakeBook()
        auto_log.write_compare_rows(book, rows)
        auto_log.write_compare_rows(book, rows)
        ws = book.tabs[auto_log.COMPARE_TAB]
        self.assertEqual(ws.data[0], auto_log.COMPARE_HEADERS)
        self.assertEqual(len(ws.data), 1 + len(rows))
        self.assertEqual(book.tabs[auto_log.ACTUAL_TAB].data, [auto_log.ACTUAL_HEADERS])

    def test_offline_returns_none(self):
        now = datetime(2026, 9, 30, 18, 0, tzinfo=TZ)
        w = fake_weather(now); w["status_code"] = 0
        self.assertIsNone(auto_log.build_row(now, w, self.cal))


if __name__ == "__main__":
    unittest.main()
