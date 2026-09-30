import json, os, sys, tempfile, unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import calendar_tw as c


def make_year(year, holidays=(), workdays=()):
    """模擬線上資料：週末放假，holidays 為平日假，workdays 為補班的週六日。"""
    out, d = [], date(year, 1, 1)
    while d.year == year:
        iso = d.isoformat()
        hol = (d.weekday() >= 5 and iso not in workdays) or iso in holidays
        out.append({"date": d.strftime("%Y%m%d"), "week": "x", "isHoliday": hol, "description": ""})
        d = date.fromordinal(d.toordinal() + 1)
    return out


HOLS = [f"2026-{m:02d}-{d:02d}" for m, d in
        [(1, 1), (2, 16), (2, 17), (2, 18), (2, 19), (2, 20), (4, 3), (4, 6), (5, 1), (6, 19), (9, 25), (9, 28), (10, 9)]]
NOOV = os.path.join(tempfile.gettempdir(), "no_such_overrides.json")


class CalendarTests(unittest.TestCase):
    def test_makeup_workday_is_workday(self):
        data = make_year(2026, HOLS, workdays=["2026-02-14"])   # 假設 2/14(六) 補班
        cal = c.load_calendar([2026], getter=lambda u: data, overrides_path=NOOV)
        self.assertFalse(cal.is_holiday(date(2026, 2, 14)))     # 補班日 → 上班
        self.assertTrue(cal.is_holiday(date(2026, 2, 15)))      # 一般週日
        self.assertTrue(cal.is_holiday(date(2026, 9, 25)))      # 平日國定假日
        self.assertFalse(cal.is_holiday(date(2026, 9, 24)))
        self.assertEqual(cal.warnings, [])

    def test_incomplete_year_falls_back_with_warning(self):
        data = make_year(2027)                                   # 只有週末＝尚未公布
        cal = c.load_calendar([2027], getter=lambda u: data, overrides_path=NOOV)
        self.assertEqual(len(cal.warnings), 1)
        self.assertTrue(cal.is_holiday(date(2027, 1, 2)))        # 週六
        self.assertFalse(cal.is_holiday(date(2027, 1, 4)))       # 週一

    def test_short_data_rejected(self):
        cal = c.load_calendar([2026], getter=lambda u: make_year(2026, HOLS)[:100], overrides_path=NOOV)
        self.assertTrue(cal.warnings)

    def test_network_failure_falls_back(self):
        def boom(u): raise OSError("offline")
        cal = c.load_calendar([2026], getter=boom, overrides_path=NOOV)
        self.assertTrue(cal.warnings)
        self.assertTrue(cal.is_holiday(date(2026, 10, 3)))       # 週六

    def test_second_source_used_when_first_fails(self):
        data = make_year(2026, HOLS)
        calls = []
        def g(u):
            calls.append(u)
            if "raw.githubusercontent" in u: raise OSError("blocked")
            return data
        cal = c.load_calendar([2026], getter=g, overrides_path=NOOV)
        self.assertEqual(cal.warnings, [])
        self.assertEqual(len(calls), 2)
        self.assertTrue(cal.is_holiday(date(2026, 10, 9)))

    def test_overrides_win(self):
        data = make_year(2026, HOLS)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"_說明": "x", "holidays": ["2026-09-30"], "workdays": ["2026-09-25"]}, f)
        try:
            cal = c.load_calendar([2026], getter=lambda u: data, overrides_path=f.name)
            self.assertTrue(cal.is_holiday(date(2026, 9, 30)))   # 颱風假
            self.assertFalse(cal.is_holiday(date(2026, 9, 25)))  # 強制上班
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
