"""台電高壓用戶服務網（HVCS）15 分鐘需量：登入、抓取、解析、存檔的共用程式。

帳號密碼不寫在程式裡，存在 Windows 認證管理員（keyring 服務名稱 CTIC_Taipower）：
    python taipower_common.py --set-login      # 第一次安裝時輸入一次
也可用環境變數 TAIPOWER_ACCOUNT / TAIPOWER_PASSWORD（測試用）。

資料格式與舊版 taipower_data.csv 相同：時間（15 分鐘區間「結束」時間）,時段,需量_kW,契約容量_kW,使用率_%
"""
import argparse
import getpass
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

TARGET_METER = "17242350005"          # 中創園區電號（防呆：抓到別的電號就不存）
KEYRING_SERVICE = "CTIC_Taipower"
LOGIN_URL = "https://service.taipower.com.tw/hvcs/Accessibility/Login"
METER_LIST_URL = "https://service.taipower.com.tw/hvcs/Customer/Module/UIDMeterNoList"
DASHBOARD_URL = "https://service.taipower.com.tw/hvcs/Electricity/Module/DashBoard"
PROCESS_URL = "https://service.taipower.com.tw/hvcs/Electricity/DashBoard/ProcessSelection"
PERIODS = ["尖峰", "半尖峰", "週六半尖峰", "離峰"]
COLUMNS = ["時間", "時段", "需量_kW", "契約容量_kW", "使用率_%"]

# 資料夾：預設 C:\BMS_Export\taipower（監控電腦），可用環境變數 TAIPOWER_DIR 改
DATA_DIR = os.environ.get("TAIPOWER_DIR", r"C:\BMS_Export\taipower" if os.name == "nt" else os.path.expanduser("~/taipower"))
CSV_FILE = os.path.join(DATA_DIR, "taipower_data.csv")
LOG_FILE = os.path.join(DATA_DIR, "taipower.log")
STATUS_FILE = os.path.join(DATA_DIR, "taipower_status.json")

log = logging.getLogger("taipower")


def setup_logging():
    os.makedirs(DATA_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(sh)


# ---------------- 帳號密碼 ----------------

def get_login():
    acc, pwd = os.environ.get("TAIPOWER_ACCOUNT"), os.environ.get("TAIPOWER_PASSWORD")
    if acc and pwd:
        return acc, pwd
    import keyring
    acc = keyring.get_password(KEYRING_SERVICE, "account")
    pwd = keyring.get_password(KEYRING_SERVICE, "password")
    if not acc or not pwd:
        raise RuntimeError("找不到台電帳號密碼，請先執行：python taipower_common.py --set-login")
    return acc, pwd


def set_login():
    import keyring
    acc = input("台電 HVCS 帳號：").strip()
    pwd = getpass.getpass("台電 HVCS 密碼（輸入時不顯示）：").strip()
    keyring.set_password(KEYRING_SERVICE, "account", acc)
    keyring.set_password(KEYRING_SERVICE, "password", pwd)
    print("已存入 Windows 認證管理員（CTIC_Taipower）。")


# ---------------- 解析 ----------------

def parse_day_html(html, date_str):
    """頁面裡的 JS 陣列（經常＝契約容量、各時段需量）→ 當天最多 96 筆。找不到陣列回傳 None（代表頁面異常）。"""
    base = datetime.strptime(date_str, "%Y-%m-%d")
    arrays = {}
    for name in ["經常"] + PERIODS:
        m = re.search(rf"var\s+{name}\s*=\s*(\[.*?\]);", html)
        if not m:
            return None
        arrays[name] = json.loads(m.group(1))
    recs = []
    for i in range(min(96, len(arrays["經常"]))):
        contract = arrays["經常"][i]
        for p in PERIODS:
            v = arrays[p][i] if i < len(arrays[p]) else None
            if v is not None:
                if v > 0:
                    recs.append({
                        "時間": (base + timedelta(minutes=15 * (i + 1))).strftime("%Y-%m-%d %H:%M"),
                        "時段": p, "需量_kW": v, "契約容量_kW": contract,
                        "使用率_%": round(v / contract * 100, 1) if contract else 0,
                    })
                break
    return recs


# ---------------- 瀏覽器操作 ----------------

class Session:
    """登入一次，之後可抓多天。用法：with Session() as s: s.fetch_day('2026-11-23')"""

    def __init__(self, headless=True, max_login_tries=6):
        self.headless = headless
        self.max_login_tries = max_login_tries

    def __enter__(self):
        import ddddocr
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=self.headless)
        self.page = self.browser.new_page()
        self.page.set_default_timeout(60000)
        self._ocr = ddddocr.DdddOcr(show_ad=False)
        self._login()
        return self

    def __exit__(self, *exc):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def _login(self):
        acc, pwd = get_login()
        page = self.page
        page.goto(LOGIN_URL)
        page.wait_for_load_state("networkidle")
        for attempt in range(1, self.max_login_tries + 1):
            page.get_by_placeholder("請輸入帳號").fill(acc)
            page.get_by_placeholder("請輸入8碼以上").fill(pwd)
            img = page.locator("xpath=//input[@placeholder='請輸入以下驗證碼']/following::img[1]")
            code = self._ocr.classification(img.screenshot()).strip()
            page.get_by_placeholder("請輸入以下驗證碼").fill(code)
            page.get_by_role("button", name="登入").click()
            page.wait_for_timeout(3000)
            if "Login" not in page.url:
                log.info("登入成功（第 %d 次）", attempt)
                break
            log.info("驗證碼可能辨識錯誤，重試（第 %d 次）", attempt)
            page.reload()
            page.wait_for_load_state("networkidle")
        else:
            raise RuntimeError(f"登入失敗 {self.max_login_tries} 次（帳密錯誤或驗證碼一直辨識錯）")

        # 綁定中創園區電號，並確認是正確電號
        page.goto(METER_LIST_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        if "DashBoard" not in page.url:
            el = page.get_by_text(TARGET_METER).first
            if el.count() and el.is_visible():
                el.click()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000)
            if "DashBoard" not in page.url:
                page.goto(DASHBOARD_URL)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000)
        self.dashboard_html = page.content()
        if TARGET_METER not in self.dashboard_html:
            raise RuntimeError(f"登入後的頁面不是中創園區電號 {TARGET_METER}，取消")

    def fetch_day(self, date_str, retries=3):
        """回傳該日紀錄 list；頁面異常回傳 None。"""
        for k in range(retries):
            try:
                r = self.page.request.post(PROCESS_URL, form={"dateTime": date_str, "meterGroup": "全部用電"})
                if r.status == 200:
                    recs = parse_day_html(r.text(), date_str)
                    if recs is not None:
                        return recs
                log.warning("%s 回應異常（HTTP %s），重試", date_str, r.status)
            except Exception as e:   # noqa: BLE001
                log.warning("%s 抓取錯誤：%s", date_str, e)
            time.sleep(2 * (k + 1))
        return None


# ---------------- 存檔 ----------------

def load_csv(path=CSV_FILE):
    if not os.path.exists(path):
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(path, encoding="utf-8-sig")


def merge_save(records, path=CSV_FILE):
    """新資料併入總檔（同時間以新的為準），先寫暫存檔再取代，避免寫到一半斷電把總檔弄壞。"""
    if not records:
        return 0
    old = load_csv(path)
    new = pd.DataFrame(records, columns=COLUMNS)
    parts = [x for x in (old, new) if not x.empty]
    df = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["時間"], keep="last").sort_values("時間")
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)
    return len(new)


def day_counts(df):
    """每個「用電日」的筆數（00:00 那筆屬於前一天）。"""
    if df.empty:
        return pd.Series(dtype=int)
    t = pd.to_datetime(df["時間"]) - pd.Timedelta(minutes=15)
    return t.dt.strftime("%Y-%m-%d").value_counts().sort_index()


def write_status(**kw):
    kw["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(kw, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set-login", action="store_true", help="把台電帳號密碼存進 Windows 認證管理員")
    a = ap.parse_args()
    if a.set_login:
        set_login()
    else:
        ap.print_help()
