"""每天執行（工作排程 CTIC_Water_Live，08:10 與 14:10）：登入台水水表e管家，抓首頁狀態與最近 14 天逐時用水指針（分 4 次查完 00–23 時），
存到 C:\\BMS_Export\\water，判讀漏水告警（有新告警就推 LINE＋GitHub ems-feed），
再把資料複製到 Google 雲端「BMS自動匯出\\自來水」給戰情室網頁用。

離線測試（不登入）：python water_live.py --export-only
"""
import argparse
import glob
import json
import logging
import os
import shutil
import sys
from datetime import date, datetime, timedelta

import water_analysis as wa
import water_common as wc

log = logging.getLogger("water")
LOCK = os.path.join(wc.DATA_DIR, "water_live.lock")
ALERT_STATE = os.path.join(wc.DATA_DIR, "water_alert_state.json")
FETCH_DAYS = 14
BMS_DIR = r"C:\BMS_Export"          # line_push.py、line_token.txt、github_token.txt 所在（監控電腦）


def find_drive_folder():
    if os.environ.get("WATER_DRIVE_DIR"):
        return os.environ["WATER_DRIVE_DIR"]
    if os.name != "nt":
        return None
    for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
        for hit in glob.glob(f"{letter}:\\*\\BMS*"):
            if os.path.isdir(hit):
                return hit
    return None


def export_to_drive():
    root = find_drive_folder()
    if not root:
        log.warning("找不到 Google 雲端 BMS 資料夾，略過上傳")
        return None
    out = os.path.join(root, "自來水")
    os.makedirs(out, exist_ok=True)
    for src in (wc.READINGS_CSV, wc.EST_CSV, wc.STATUS_FILE):
        if os.path.exists(src):
            tmp = os.path.join(out, os.path.basename(src) + ".tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, os.path.join(out, os.path.basename(src)))
    # 最新幾份原始頁面文字（核對解析用）
    raw_out = os.path.join(out, "raw")
    os.makedirs(raw_out, exist_ok=True)
    if os.path.isdir(wc.RAW_DIR):
        for f in sorted(os.listdir(wc.RAW_DIR))[-6:]:
            shutil.copyfile(os.path.join(wc.RAW_DIR, f), os.path.join(raw_out, f))
    return out


def _calendar():
    """上班日／假日：有 calendar_tw.py（含國定假日與補班）就用，沒有就只看週末。"""
    try:
        from calendar_tw import load_calendar
        y = date.today().year
        return load_calendar({y - 1, y}).is_holiday
    except Exception as e:   # noqa: BLE001
        log.warning("讀不到行事曆，只用週末判斷假日：%s", e)
        return None


def notify(alerts, test=False):
    """新告警（或等級升高）才送；同一件事不重複推。LINE 送不出去（例如月額度用完）時至少寫進 GitHub ems-feed。"""
    try:
        with open(ALERT_STATE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    rank = {"info": 0, "warn": 1, "alarm": 2}
    # 同一種告警（code）持續期間只推一次，等級升高（注意→告警）再推一次；告警消失後才重新計算。
    # 例如 2026-10 那次連續 7 天「近 7 天偏高」，只會推「注意」與「告警」各一則，不會天天推。
    active = {a["code"]: a for a in alerts if a["level"] != "info" or a["code"] == "stale"}
    new = []
    for code, a in active.items():
        if test or rank[a["level"]] > state.get(code, -1):
            new.append(a)
    state = {code: max(rank[a["level"]], state.get(code, -1)) for code, a in active.items()}
    if not new:
        with open(ALERT_STATE, "w", encoding="utf-8") as f:     # 告警消失也要記下來，下次再出現才會重推
            json.dump(state, f, ensure_ascii=False, indent=1)
        return 0
    text = "【中創用水】\n" + "\n\n".join(wa.alert_text(a) for a in new) + "\n\n戰情室：用水戰情室頁面"
    sent_line = sent_feed = False
    try:
        sys.path.insert(0, BMS_DIR)
        import line_push as lp
        if os.path.exists(lp.TOKEN_FILE):
            with open(lp.TOKEN_FILE, encoding="utf-8") as f:
                sent_line = lp.send(text, f.read().strip())
        if os.path.exists(lp.GH_TOKEN_FILE):
            with open(lp.GH_TOKEN_FILE, encoding="utf-8") as f:
                item = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": "line" if sent_line else "info",
                        "src": "water", "text": text}
                sent_feed = lp.gh_dispatch([item], f.read().strip())
    except Exception as e:   # noqa: BLE001
        log.error("告警送出失敗：%s", e)
    log.info("告警 %d 則：LINE %s、GitHub %s", len(new), "成功" if sent_line else "失敗", "成功" if sent_feed else "失敗")
    if sent_line or sent_feed:
        with open(ALERT_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    return len(new)


def main(argv=None):
    wc.setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--show", action="store_true", help="顯示瀏覽器（除錯用）")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--test-notify", action="store_true", help="把目前判讀結果強制推一次（測試 LINE／GitHub）")
    a = ap.parse_args(argv)
    if os.path.exists(LOCK) and (datetime.now().timestamp() - os.path.getmtime(LOCK)) < 1500:
        log.warning("上一次還在執行，略過")
        return 0
    os.makedirs(wc.DATA_DIR, exist_ok=True)
    open(LOCK, "w").close()
    try:
        return run(a)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def run(a):
    ok, err = True, ""
    status = wc.load_status()
    if not a.export_only:
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=FETCH_DAYS - 1)
        try:
            with wc.Session(headless=not a.show) as s:
                status = s.home()
                rd, es = s.pointer_all(start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d"))
            if not rd:
                raise RuntimeError("查詢頁解析不到任何指針（台水頁面可能改版），請看 raw 資料夾")
            n_r, n_e = wc.merge_save(rd, es)
            log.info("%s~%s：指針 %d 筆、估算量 %d 天；現行指針 %s", start, end, n_r, n_e, status.get("current_reading"))
        except Exception as e:   # noqa: BLE001
            ok, err = False, str(e)
            log.error("抓取失敗：%s", e)
    days = wa.daily_table(wc.load_readings().to_dict("records"), wc.load_estimates().to_dict("records"), _calendar())
    alerts = wa.evaluate(days, status)
    status.update(scrape_ok=ok, scrape_error=err, scraped_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  last_reading_date=days[-1]["date"] if days else None,
                  alerts=[{k: x[k] for k in ("level", "code", "title", "text", "date")} for x in alerts])
    wc.write_status(status)
    for x in alerts:
        log.info("判讀：[%s] %s — %s", x["level"], x["title"], x["text"])
    if not a.no_notify:
        notify(alerts, test=a.test_notify)
    try:
        export_to_drive()
    except Exception as e:   # noqa: BLE001
        log.error("上傳雲端失敗：%s", e)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
