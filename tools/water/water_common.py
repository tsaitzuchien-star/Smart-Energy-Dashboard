"""台水「水表e管家」智慧水表：登入、抓首頁狀態與用水指針、解析、存檔的共用程式。

帳號密碼不寫在程式裡，存在 Windows 認證管理員（keyring 服務名稱 CTIC_Water）：
    python water_common.py --set-login      # 第一次安裝時輸入一次
也可用環境變數 WATER_ACCOUNT / WATER_PASSWORD（測試用）。

存檔（C:\\BMS_Export\\water）：
    water_readings.csv   date,hour,reading         每天各整點的累計指針（整數度＝m³），台水頁面有哪些時刻就存哪些
    water_estimates.csv  date,est                  台水頁面的「估算量」（只供對照；實際日用量用隔天 00 時指針相減）
    water_status.json    首頁現行指針、本期度數、試算水費、停水公告、異常通報
    raw\\pointer_*.txt    查詢頁原始文字（前幾次保留，供核對解析是否正確）
"""
import argparse
import getpass
import json
import logging
import os
import re
import sys
import time
from datetime import datetime

import pandas as pd

TARGET_WATER_NO = "B1801146550"     # 中創園區水號（防呆：登入後頁面不是這個水號就不存）
KEYRING_SERVICE = "CTIC_Water"
LOGIN_URL = "https://web.water.gov.tw/swns/twn_public/Home/Login"
READ_COLS = ["date", "hour", "reading"]
EST_COLS = ["date", "est"]

DATA_DIR = os.environ.get("WATER_DIR", r"C:\BMS_Export\water" if os.name == "nt" else os.path.expanduser("~/water"))
READINGS_CSV = os.path.join(DATA_DIR, "water_readings.csv")
EST_CSV = os.path.join(DATA_DIR, "water_estimates.csv")
STATUS_FILE = os.path.join(DATA_DIR, "water_status.json")
LOG_FILE = os.path.join(DATA_DIR, "water.log")
RAW_DIR = os.path.join(DATA_DIR, "raw")
RAW_KEEP = 60

log = logging.getLogger("water")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")


# 台水「顯示時段」一次最多勾 6 個 → 分 4 次查完 24 小時；第一組是台水預設（估算量＝15 時 − 00 時）
DEFAULT_HOURS = (0, 3, 6, 9, 12, 15)
HOUR_GROUPS = (DEFAULT_HOURS, (1, 2, 4, 5, 7, 8), (10, 11, 13, 14, 16, 17), (18, 19, 20, 21, 22, 23))


class LoginError(RuntimeError):
    pass


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
    acc, pwd = os.environ.get("WATER_ACCOUNT"), os.environ.get("WATER_PASSWORD")
    if acc and pwd:
        return acc, pwd
    import keyring
    acc = keyring.get_password(KEYRING_SERVICE, "account")
    pwd = keyring.get_password(KEYRING_SERVICE, "password")
    if not acc or not pwd:
        raise RuntimeError("找不到台水帳號密碼，請先執行：python water_common.py --set-login")
    return acc, pwd


def set_login():
    import keyring
    acc = input(f"台水水表e管家 帳號（水號，例如 {TARGET_WATER_NO}）：").strip()
    pwd = getpass.getpass("密碼（輸入時不顯示）：").strip()
    keyring.set_password(KEYRING_SERVICE, "account", acc)
    keyring.set_password(KEYRING_SERVICE, "password", pwd)
    print("已存入 Windows 認證管理員（CTIC_Water）。")


# ---------------- 解析 ----------------

def _num(s):
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_home_status(body_text):
    """首頁純文字 → 核心 KPI、停水公告、異常通報（沿用 Cliff 原版 scraper.py 的規則）。"""
    def extract(pattern, default=""):
        m = re.search(pattern, body_text, re.DOTALL)
        return m.group(1).strip() if m else default

    def num(pattern):
        return _num(extract(pattern))

    anomaly = "尚無資料" if "尚無資料" in body_text else extract(r"異常[^\n]*?[:：]\s*([^\n]+)", "偵測到異常狀態，請登入系統確認")
    return {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "water_no": TARGET_WATER_NO,
        "meter_update_time": extract(r"水表更新時間\s*([\d/]+)"),
        "current_reading": num(r"現行指針\s*([\d,]+)"),
        "current_period_usage": num(r"本期累積度數\s*([\d,]+)"),
        "next_period_usage": num(r"下期累積度數\s*([\d,]+)"),
        "current_fee": num(r"本期試算水費.*?([\d,]+)\s*元"),
        "last_fee": num(r"上期水費.*?([\d,]+)\s*元"),
        "next_fee": num(r"下期試算水費.*?([\d,]+)\s*元"),
        "outage_notice": extract(r"停水公告[:：]\s*([^\n]+)", "目前無停水公告"),
        "anomaly_status": anomaly,
    }


_DATE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_HOUR = re.compile(r"(?<!\d)(\d{1,2})\s*時\s*[:：]?\s*([\d,]+(?:\.\d+)?)")


def parse_pointer_text(body_text):
    """「用水指針查詢」頁面文字 → (readings, estimates)。

    頁面每天一張卡片：日期、00:00~24:00 估算量、各時刻指針（例如 00時 7274）。
    台水頁面列出哪些時刻就收哪些（目前看到 00、03、06、15 時；若之後有逐時也會自動收）。
    沒有任何時刻指針的區塊（例如查詢條件列）不收。
    """
    readings, estimates = [], []
    starts = [m.start() for m in _DATE.finditer(body_text)]
    for i, s in enumerate(starts):
        block = body_text[s: starts[i + 1] if i + 1 < len(starts) else len(body_text)]
        m = _DATE.match(block)
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            continue
        hours = {}
        for h, v in _HOUR.findall(block):
            h, v = int(h), _num(v)
            if 0 <= h <= 23 and v is not None:
                hours[h] = v
        if not hours:
            continue
        for h, v in sorted(hours.items()):
            readings.append({"date": d, "hour": h, "reading": v})
        est = re.search(r"估算量\s*[:：]?\s*([\d,]+(?:\.\d+)?)", block)
        if est and _num(est.group(1)) is not None:
            estimates.append({"date": d, "est": _num(est.group(1))})
    return readings, estimates


# ---------------- 瀏覽器操作 ----------------

class Session:
    """登入一次，之後可查多段日期。用法：with Session() as s: s.home(); s.pointer('2026/11/01', '2026/11/23')"""

    def __init__(self, headless=True, max_login_tries=6):
        self.headless = headless
        self.max_login_tries = max_login_tries
        self.home_text = ""

    def __enter__(self):
        import ddddocr
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._ocr = ddddocr.DdddOcr(show_ad=False)
        self.dialogs = []
        try:
            self._open(self.headless)
            self._login()
        except LoginError:
            if not self.headless:
                self._pw.stop()
                raise
            # 背景模式（headless）登入不了 → 改開視窗再試一輪（Cliff 原版程式是開視窗跑的）
            log.warning("背景模式登入失敗，改用開視窗模式再試")
            self.browser.close()
            self._open(False)
            try:
                self._login()
            except Exception:
                self.browser.close()
                self._pw.stop()
                raise
        return self

    def _open(self, headless):
        self.browser = self._pw.chromium.launch(headless=headless)
        # 背景模式的預設 User-Agent 含 HeadlessChrome，有些政府網站會擋，改成一般 Chrome
        ctx = self.browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-TW", user_agent=UA)
        self.page = ctx.new_page()
        self.page.set_default_timeout(60000)

        def on_dialog(dlg):
            self.dialogs.append(dlg.message)
            log.info("網頁訊息：%s", dlg.message)
            dlg.accept()
        self.page.on("dialog", on_dialog)

    def __exit__(self, *exc):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def _login(self):
        acc, pwd = get_login()
        page = self.page
        page.goto(LOGIN_URL, wait_until="networkidle")
        for attempt in range(1, self.max_login_tries + 1):
            img = page.locator("img[src*='Captcha'], img[src*='Validate'], img[id*='captcha']").first
            code = re.sub(r"[^0-9]", "", self._ocr.classification(img.screenshot()))
            inputs = page.locator("input[type='text'], input[type='password']")
            inputs.nth(0).fill(acc)
            inputs.nth(1).fill(pwd)
            inputs.nth(2).fill(code)
            n_dlg = len(self.dialogs)
            page.get_by_text("送出").first.click()
            try:
                page.wait_for_selector("text=現行指針", timeout=8000)
                log.info("登入成功（第 %d 次）", attempt)
                break
            except Exception:   # noqa: BLE001
                # 台水錯誤訊息有時是跳出視窗、有時是登入框上方的紅字（例如「登入失敗，請確定您的帳號密碼」）
                try:
                    body = page.inner_text("body")
                except Exception:   # noqa: BLE001
                    body = ""
                red = re.search(r"(登入失敗[^\n]*|驗證碼[^\n]*錯誤[^\n]*|[^\n]*鎖定[^\n]*)", body)
                msg = "；".join(self.dialogs[n_dlg:] + ([red.group(1).strip()] if red else [])) or "（沒有跳出訊息）"
                log.info("登入未成功（第 %d 次，驗證碼辨識為 %s）：%s", attempt, code or "空白", msg)
                self._snap(f"login_fail_{attempt}")
                if any(k in msg for k in ("密碼", "帳號", "鎖定", "不存在")):   # 帳密錯就立刻停，避免連錯被鎖
                    raise LoginError(f"台水網站回覆：{msg}（請確認帳密：python water_common.py --set-login）")
                page.goto(LOGIN_URL, wait_until="networkidle")
                time.sleep(1)
        else:
            raise LoginError(f"登入失敗 {self.max_login_tries} 次（最後訊息：{'；'.join(self.dialogs[-2:]) or '無'}），"
                             f"截圖在 {RAW_DIR}")
        time.sleep(1.5)
        self.home_text = page.inner_text("body")
        if TARGET_WATER_NO not in self.home_text and TARGET_WATER_NO[1:] not in self.home_text:
            log.warning("首頁找不到水號 %s（可能只是頁面沒顯示水號），請核對 raw\\home_*.txt", TARGET_WATER_NO)
        save_raw("home", self.home_text)

    def _snap(self, name):
        try:
            os.makedirs(RAW_DIR, exist_ok=True)
            self.page.screenshot(path=os.path.join(RAW_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_{name}.png"))
            save_raw(name, self.page.inner_text("body"))
        except Exception as e:   # noqa: BLE001
            log.warning("截圖失敗：%s", e)

    def home(self):
        return parse_home_status(self.home_text)

    def _open_pointer_page(self):
        page = self.page
        if "顯示時段" in page.inner_text("body"):
            return
        page.get_by_text("用水指針查詢").first.click()
        page.wait_for_selector("text=顯示時段", timeout=15000)
        time.sleep(1.5)
        save_raw("pointer_form", page.inner_text("body") + "\n\n=== 表單選項 ===\n" + self._form_options())

    def _form_options(self):
        """記下頁面上的下拉選單與選項（例如「顯示時段」能不能選逐時），寫進 raw 檔給人核對。"""
        try:
            return self.page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('select').forEach((s, i) => out.push(
                    'select#' + i + ' ' + (s.name || s.id) + ': ' +
                    Array.from(s.options).map(o => (o.selected ? '*' : '') + o.value + '=' + o.text.trim()).join(' | ')));
                document.querySelectorAll('input[type=radio],input[type=checkbox]').forEach(r => out.push(
                    r.type + ' ' + (r.name || r.id) + '=' + r.value + (r.checked ? ' (checked)' : '')));
                return out.join('\\n');
            }""")
        except Exception as e:   # noqa: BLE001
            return f"(讀不到表單選項：{e})"

    def _set_hours(self, hours):
        """勾選「顯示時段」（台水限最多 6 個）。checkbox id 為 checkbox-00 … checkbox-24。"""
        want = {f"{h:02d}" for h in hours}
        self.page.evaluate("""(want) => {
            document.querySelectorAll('input[type=checkbox][id^="checkbox-"]').forEach(cb => {
                const on = want.includes(cb.value);
                if (cb.checked !== on) cb.click();
            });
        }""", sorted(want))

    def pointer(self, start, end, hours=DEFAULT_HOURS, timeout=45):
        """查詢 start~end（YYYY/MM/DD），回傳頁面文字。

        台水頁面按「查詢」後是非同步更新，舊結果會留在畫面上好幾秒（2026-11-24 實測：馬上讀會讀到前一兩次查詢的結果），
        所以要等到結果第一張卡片的日期落在查詢區間、時段也和勾選的一樣才算數。
        """
        self._open_pointer_page()
        page = self.page
        self._set_hours(hours)
        page.evaluate(
            """([s, e]) => {
                const inputs = Array.from(document.querySelectorAll('input[type="text"]')).filter(el => el.offsetParent !== null);
                if (inputs.length >= 2) {
                    inputs[0].value = s; inputs[0].dispatchEvent(new Event('change', {bubbles: true}));
                    inputs[1].value = e; inputs[1].dispatchEvent(new Event('change', {bubbles: true}));
                }
            }""",
            [start, end],
        )
        before = page.inner_text("body")
        page.get_by_text("查詢", exact=True).first.click()
        s0, e0 = start.replace("/", "-"), end.replace("/", "-")
        want = sorted(hours)
        t_end = time.time() + timeout
        text = before
        while time.time() < t_end:
            time.sleep(1)
            text = page.inner_text("body")
            if text == before:
                continue
            rd, _ = parse_pointer_text(results_part(text))
            if not rd:
                if re.search(r"查無|無資料|沒有資料", results_part(text)):
                    break
                continue
            first = rd[0]["date"]
            hrs = {r["hour"] for r in rd}
            if s0 <= first <= e0 and hrs <= set(want):     # 個別時段可能沒回傳，所以用「包含於」
                time.sleep(1)
                text = page.inner_text("body")
                break
        else:
            log.warning("%s~%s（%s 時）等 %d 秒結果還沒更新", start, end, ",".join(f"{h:02d}" for h in hours), timeout)
            text = ""
        save_raw(f"pointer_{start.replace('/', '')}_{end.replace('/', '')}_h{hours[0]:02d}", text)
        return text

    def pointer_all(self, start, end, groups=HOUR_GROUPS):
        """分 4 次（每次 6 個時段）查完 00–23 時，回傳 (readings, estimates)，只留查詢區間內的日子。
        估算量只取預設時段那一次（台水定義：第六個時段減第一個時段，也就是 00→15 時）。"""
        s0, e0 = start.replace("/", "-"), end.replace("/", "-")
        readings, estimates = [], []
        for k, g in enumerate(groups):
            text = self.pointer(start, end, g)
            if not text and k == 0:
                break                       # 第一組就等不到結果（多半是這段期間沒資料）→ 其他時段不用再等
            rd, es = parse_pointer_text(results_part(text))
            readings += [r for r in rd if s0 <= r["date"] <= e0 and r["hour"] in g]
            if list(g) == list(DEFAULT_HOURS):
                estimates += [x for x in es if s0 <= x["date"] <= e0]
        return clean_readings(readings), estimates


def results_part(text):
    """頁面文字只取查詢結果那段（「資料回寫」之後），避免把查詢條件或公告的日期當成資料。"""
    i = text.find("資料回寫")
    return text[i:] if i >= 0 else text


def clean_readings(readings):
    """去掉明顯是缺值的 0（例如 2026-07-05 00 時 0，其他時刻 3564）。"""
    by = {}
    for r in readings:
        by.setdefault(r["date"], []).append(r)
    out = []
    for d, rs in by.items():
        big = max(r["reading"] for r in rs)
        out += [r for r in rs if not (r["reading"] == 0 and big > 100)]
    return out


# ---------------- 存檔 ----------------

def save_raw(name, text):
    try:
        os.makedirs(RAW_DIR, exist_ok=True)
        with open(os.path.join(RAW_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_{name}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        files = sorted(os.listdir(RAW_DIR))
        for old in files[:-RAW_KEEP]:
            os.remove(os.path.join(RAW_DIR, old))
    except OSError as e:
        log.warning("raw 檔存不了：%s", e)


def _load(path, cols):
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    return pd.read_csv(path, encoding="utf-8-sig")


def load_readings(path=None):
    return _load(path or READINGS_CSV, READ_COLS)


def load_estimates(path=None):
    return _load(path or EST_CSV, EST_COLS)


def _merge(path, cols, keys, records):
    if not records:
        return 0
    old = _load(path, cols)
    new = pd.DataFrame(records, columns=cols)
    df = pd.concat([x for x in (old, new) if not x.empty], ignore_index=True)
    df = df.drop_duplicates(subset=keys, keep="last").sort_values(keys)
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)
    return len(new)


def merge_save(readings, estimates):
    """新資料併入總檔（同日同時刻以新的為準），先寫暫存檔再取代。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    return (_merge(READINGS_CSV, READ_COLS, ["date", "hour"], readings),
            _merge(EST_CSV, EST_COLS, ["date"], estimates))


def import_old_csv(path):
    """Cliff 原版 water_data.csv（date,daily_usage,reading_00,reading_03,reading_06,reading_15,night_usage）→ 新格式。"""
    df = pd.read_csv(path, encoding="utf-8-sig")
    readings, estimates = [], []
    for _, r in df.iterrows():
        for c in df.columns:
            m = re.fullmatch(r"reading_(\d{2})", c)
            if m and pd.notna(r[c]):
                readings.append({"date": r["date"], "hour": int(m.group(1)), "reading": float(r[c])})
        if "daily_usage" in df.columns and pd.notna(r["daily_usage"]):
            estimates.append({"date": r["date"], "est": float(r["daily_usage"])})
    return readings, estimates


def write_status(status):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATUS_FILE)


def load_status():
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set-login", action="store_true", help="把台水帳號密碼存進 Windows 認證管理員")
    a = ap.parse_args()
    if a.set_login:
        set_login()
    else:
        ap.print_help()
