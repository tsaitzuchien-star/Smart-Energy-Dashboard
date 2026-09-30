"""台灣上班日／假日判斷（含補班日），每年自動取得新行事曆。

資料優先順序（高→低）：
  1. data/calendar_overrides.json  人工覆寫（臨時颱風假、公告變更時手動加）
  2. 線上行事曆  ruyut/TaiwanCalendar（政府辦公日曆表整理版，含補班）
                 先試 raw.githubusercontent.com，失敗改 jsDelivr
  3. 備援：純週末判斷（此時 warnings 會提醒「平日國定假日與補班日可能不準」）

線上資料會做完整性檢查（整年資料、且有足夠的平日假日），
避免拿到「尚未公布／只有週末」的空殼資料卻以為是對的。
"""
import json
import logging
import os
from datetime import date, datetime

import requests

log = logging.getLogger(__name__)

SOURCES = [
    "https://raw.githubusercontent.com/ruyut/TaiwanCalendar/master/data/{year}.json",
    "https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json",
]
DEFAULT_OVERRIDES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "calendar_overrides.json")
MIN_ENTRIES = 360          # 一整年約 365 筆
MIN_WEEKDAY_HOLIDAYS = 8   # 台灣每年平日放假至少十來天；低於此值視為資料不完整


def _http_get_json(url):
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    return r.json()


def _to_date(s):
    s = str(s).strip().replace("-", "").replace("/", "")
    return datetime.strptime(s, "%Y%m%d").date()


def validate_year_data(data, year):
    """回傳 (是否可用, 原因)。"""
    if not isinstance(data, list) or len(data) < MIN_ENTRIES:
        return False, f"{year} 年資料筆數不足（{len(data) if isinstance(data, list) else 0}）"
    weekday_holidays = 0
    for item in data:
        try:
            d = _to_date(item["date"])
        except Exception:
            return False, f"{year} 年資料格式異常"
        if d.year != year:
            return False, f"{year} 年資料含其他年份日期"
        if item.get("isHoliday") and d.weekday() < 5:
            weekday_holidays += 1
    if weekday_holidays < MIN_WEEKDAY_HOLIDAYS:
        return False, f"{year} 年資料的平日假日只有 {weekday_holidays} 天，疑似尚未公布完整"
    return True, ""


def _load_overrides(path):
    out = {"holidays": set(), "workdays": set()}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for key in ("holidays", "workdays"):
            for s in raw.get(key, []):
                if isinstance(s, str) and s.strip() and not s.startswith("_"):
                    out[key].add(_to_date(s))
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("calendar_overrides.json 讀取失敗：%s", e)
    return out


class TaiwanCalendar:
    def __init__(self, year_maps, overrides, warnings):
        self._maps = year_maps          # {year: {date: is_holiday}}；缺的年份走備援
        self._ov = overrides
        self.warnings = warnings
        self.status = "ok" if not warnings else "fallback"

    def is_holiday(self, d):
        if isinstance(d, datetime):
            d = d.date()
        if d in self._ov["workdays"]:
            return False
        if d in self._ov["holidays"]:
            return True
        m = self._maps.get(d.year)
        if m is not None and d in m:
            return m[d]             # 已涵蓋補班：週末且 isHoliday=false → 上班
        return d.weekday() >= 5     # 備援


def load_calendar(years, getter=None, overrides_path=DEFAULT_OVERRIDES):
    getter = getter or _http_get_json
    warnings, year_maps = [], {}
    for year in sorted(set(years)):
        last_err = ""
        for tpl in SOURCES:
            try:
                data = getter(tpl.format(year=year))
            except Exception as e:
                last_err = f"連線失敗（{type(e).__name__}）"
                continue
            ok, why = validate_year_data(data, year)
            if ok:
                year_maps[year] = {_to_date(i["date"]): bool(i["isHoliday"]) for i in data}
                break
            last_err = why
        else:
            warnings.append(f"⚠️ 無法取得 {year} 年行事曆（{last_err}），暫以「僅週末休假」判斷，"
                            f"平日國定假日與補班日可能不準，請到 data/calendar_overrides.json 手動補充。")
    return TaiwanCalendar(year_maps, _load_overrides(overrides_path), warnings)
