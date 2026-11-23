"""每 15 分鐘執行一次（工作排程 CTIC_Taipower_Live）：抓今天＋昨天的台電 15 分鐘需量，補最近 7 天不完整的日子，
存到 C:\\BMS_Export\\taipower\\taipower_data.csv，並把最近 35 天複製到 Google 雲端「BMS自動匯出\\台電15分鐘」給戰情室用。

離線測試（不登入）：python taipower_live.py --export-only
"""
import argparse
import glob
import logging
import os
import shutil
import sys
from datetime import date, datetime, timedelta

import pandas as pd

import taipower_common as tc

log = logging.getLogger("taipower")
LOCK = os.path.join(tc.DATA_DIR, "taipower_live.lock")
RECENT_DAYS = 35


def find_drive_folder():
    """找 Google 雲端硬碟裡名稱以 BMS 開頭的資料夾（同 bms_export.ps1 的找法）。可用 TAIPOWER_DRIVE_DIR 指定。"""
    if os.environ.get("TAIPOWER_DRIVE_DIR"):
        return os.environ["TAIPOWER_DRIVE_DIR"]
    if os.name != "nt":
        return None
    for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
        for hit in glob.glob(f"{letter}:\\*\\BMS*"):
            if os.path.isdir(hit):
                return hit
    return None


def export_to_drive(full_daily_copy):
    root = find_drive_folder()
    if not root:
        log.warning("找不到 Google 雲端 BMS 資料夾，略過上傳")
        return None
    out = os.path.join(root, "台電15分鐘")
    os.makedirs(out, exist_ok=True)
    df = tc.load_csv()
    if df.empty:
        return out
    cut = (datetime.now() - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    rec = df[df["時間"] >= cut]
    tmp = os.path.join(out, "taipower_recent.csv.tmp")
    rec.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, os.path.join(out, "taipower_recent.csv"))
    shutil.copyfile(tc.STATUS_FILE, os.path.join(out, "taipower_status.json"))
    if full_daily_copy:
        shutil.copyfile(tc.CSV_FILE, os.path.join(out, "taipower_data.csv"))
        log.info("已複製完整歷史檔到雲端")
    return out


def main(argv=None):
    tc.setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--show", action="store_true", help="顯示瀏覽器（除錯用）")
    a = ap.parse_args(argv)

    # 防止前一次還沒跑完又啟動（超過 20 分鐘的鎖視為殘留）
    if os.path.exists(LOCK) and (datetime.now().timestamp() - os.path.getmtime(LOCK)) < 1200:
        log.warning("上一次還在執行，略過")
        return 0
    open(LOCK, "w").close()
    try:
        return run(a)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def run(a):
    today = date.today()
    ok, err, n_new = True, "", 0
    if not a.export_only:
        counts = tc.day_counts(tc.load_csv())
        days = [today, today - timedelta(days=1)]
        for k in range(2, 8):
            d = today - timedelta(days=k)
            if counts.get(d.isoformat(), 0) < 96:
                days.append(d)
        try:
            got = []
            with tc.Session(headless=not a.show) as s:
                for d in days:
                    recs = s.fetch_day(d.isoformat())
                    if recs is None:
                        log.warning("%s 頁面異常，下次再補", d)
                        continue
                    got.extend(recs)
                    log.info("%s 抓到 %d 筆", d, len(recs))
            n_new = tc.merge_save(got)
        except Exception as e:   # noqa: BLE001
            ok, err = False, str(e)
            log.error("抓取失敗：%s", e)

    df = tc.load_csv()
    last = df.iloc[-1].to_dict() if not df.empty else {}
    if not df.empty:
        uday = (pd.to_datetime(df["時間"]) - pd.Timedelta(minutes=15)).dt.date   # 00:00 那筆屬前一天
        td = df[uday == today]
    else:
        td = df
    peak = td.loc[td["需量_kW"].idxmax()].to_dict() if not td.empty else {}
    tc.write_status(ok=ok, error=err, fetched=n_new, last_time=last.get("時間"), last_kw=last.get("需量_kW"),
                    last_period=last.get("時段"), last_contract=last.get("契約容量_kW"),
                    today_peak_kw=peak.get("需量_kW"), today_peak_time=peak.get("時間"))
    first_run_of_day = datetime.now().hour == 1 and datetime.now().minute < 15
    try:
        export_to_drive(full_daily_copy=first_run_of_day or a.export_only)
    except Exception as e:   # noqa: BLE001
        log.error("上傳雲端失敗：%s", e)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
