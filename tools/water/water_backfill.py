"""補抓智慧水表歷史指針（預設 2026-01-01 到昨天），每次查 1 個月，登入一次查完。

    python water_backfill.py                          # 2026-01-01 起
    python water_backfill.py --start 2026-03-01
    python water_backfill.py --import-old C:\\Users\\User\\Water_Dashboard\\water_data.csv   # 先併入 Cliff 原版 CSV

台水網站若限制可查的最早日期，查不到的月份會記在 water.log，不影響已抓到的部分。
"""
import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta

import water_common as wc

log = logging.getLogger("water")


def month_chunks(start, end):
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt - timedelta(days=1), end)
        cur = nxt


def main(argv=None):
    wc.setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default=None, help="預設昨天")
    ap.add_argument("--import-old", default=None)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args(argv)

    if a.import_old:
        rd, es = wc.import_old_csv(a.import_old)
        n = wc.merge_save(rd, es)
        log.info("已併入舊版 CSV：指針 %d 筆、估算量 %d 天", *n)

    start = datetime.strptime(a.start, "%Y-%m-%d").date()
    end = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else date.today() - timedelta(days=1)
    summary = []
    with wc.Session(headless=not a.show) as s:
        status = s.home()
        wc.write_status({**wc.load_status(), **status})
        for c0, c1 in month_chunks(start, end):
            got = None
            for k in range(3):
                try:
                    text = s.pointer(c0.strftime("%Y/%m/%d"), c1.strftime("%Y/%m/%d"))
                    rd, es = wc.parse_pointer_text(text)
                    rd = [r for r in rd if c0.isoformat() <= r["date"] <= c1.isoformat()]
                    es = [e for e in es if c0.isoformat() <= e["date"] <= c1.isoformat()]
                    got = (rd, es)
                    break
                except Exception as e:   # noqa: BLE001
                    log.warning("%s~%s 第 %d 次失敗：%s", c0, c1, k + 1, e)
                    time.sleep(3 * (k + 1))
            if not got or not got[0]:
                log.warning("%s~%s 查不到資料（可能超出台水可查範圍）", c0, c1)
                summary.append((c0, 0))
                continue
            wc.merge_save(*got)
            ndays = len({r["date"] for r in got[0]})
            log.info("%s~%s：%d 天、指針 %d 筆", c0, c1, ndays, len(got[0]))
            summary.append((c0, ndays))
            time.sleep(2)
    log.info("補抓完成：" + "、".join(f"{c:%Y-%m} {n} 天" for c, n in summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
