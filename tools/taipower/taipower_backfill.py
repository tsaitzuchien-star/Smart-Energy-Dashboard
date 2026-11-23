"""補抓歷史：近 N 年中「不滿 96 筆」的過去日子都重抓（今天除外）。可中斷，下次接著補。

    python taipower_backfill.py            # 預設近 3 年
    python taipower_backfill.py --years 5
    python taipower_backfill.py --import-old C:\\Users\\User\\Taipower_Dashboard\\taipower_data.csv   # 先把舊檔併進來
"""
import argparse
import logging
import sys
import time
from datetime import date, timedelta

import taipower_common as tc

log = logging.getLogger("taipower")


def main(argv=None):
    tc.setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=3)
    ap.add_argument("--import-old", help="舊版 taipower_data.csv 路徑，先併入")
    a = ap.parse_args(argv)

    if a.import_old:
        old = tc.load_csv(a.import_old)
        n = tc.merge_save(old.to_dict("records"))
        log.info("已併入舊檔 %d 筆", n)

    today = date.today()
    start = today - timedelta(days=int(365 * a.years))
    counts = tc.day_counts(tc.load_csv())
    todo = [start + timedelta(days=i) for i in range((today - start).days)]
    todo = [d for d in todo if counts.get(d.isoformat(), 0) < 96]
    log.info("近 %.1f 年待補 %d 天", a.years, len(todo))
    if not todo:
        return 0
    buf, still_short = [], []
    with tc.Session() as s:
        for i, d in enumerate(todo, 1):
            recs = s.fetch_day(d.isoformat())
            if recs is None:
                still_short.append(d.isoformat())
            else:
                buf.extend(recs)
                if len(recs) < 96:
                    still_short.append(f"{d.isoformat()}({len(recs)})")
            if i % 30 == 0 or i == len(todo):
                tc.merge_save(buf)
                buf = []
                log.info("進度 %d/%d", i, len(todo))
            time.sleep(0.3)
    if still_short:
        log.warning("台電網頁本身就不完整的日子（停電、換表或台電缺資料）：%s", ", ".join(still_short))
    return 0


if __name__ == "__main__":
    sys.exit(main())
