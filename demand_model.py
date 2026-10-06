"""實測校正需量模型（純 Python，不需 numpy）。

每小時的「該小時最大 15 分鐘需量」＝ 該小時基準 ＋ 氣象修正：
    y = a[日型][小時] + Σ b[日型][f] × (x_f − 平均_f) / 標準差_f
日型分「上班日」與「非上班日」；氣象用前一天 18:00 拿得到的明日預報（溫度、濕度、雲量、日射、降雨）。

係數由 calibrate.py 每週用最新實測重新擬合（近期資料權重較高，半衰期 21 天），存在 model/demand_model.json。
另存各時段「實測最高 − 預測最高」的歷史分位數，用來估明日最壞情況（P90／P95）。

太陽能（T-REC 四組 PV，自發自用、在總表後面）另擬合「明日 PV ≈ 小時基準 + 日射 + 雲量」，供畫面顯示。
需量模型本身不另扣 PV：總表需量已是扣掉 PV 後的值，日射與雲量的效果已含在氣象斜率裡
（6–9 月回測：拆成「真實用電 − PV」兩段預測並沒有比較準）。

夏月平日 16:00 起磁浮全關（現場操作），這段負載會掉約 75 kW。擬合時先把這兩小時加回去（等於「磁浮照常運轉」），
預測時再依明日是否尖峰扣掉；這樣到了非夏月（16 點後磁浮照常開）也不會沿用夏天的低值。
"""
import json
import math
import os

FEATURES = ["T", "RH", "CC", "R", "P"]        # 溫度、相對濕度、總雲量、短波日射、降雨
FEATURE_NAMES = {"T": "氣溫", "RH": "濕度", "CC": "雲量", "R": "日射", "P": "降雨"}
HOURS = list(range(7, 19))                    # 07:00–18:00，空調 07:30–18:00 加前後各一小時
BLOCKS = {"semi": range(9, 16), "peak": range(16, 18)}   # 夏月平日：半尖峰日間 09–16、尖峰空調時段 16–18
MAG_OFF_KW = 75.0     # 實測：夏月平日 15:30 與 16:15 兩個 15 分鐘平均的差，6–9 月平均 73.8 kW
PV_MAX_KW = 145.0     # 四組 PV 合計裝置容量
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model", "demand_model.json")


def day_type(is_holiday):
    return "off" if is_holiday else "work"


# ---------------- 擬合 ----------------

def _solve(a, b):
    """高斯消去法解 a·x = b（a 為 n×n list）。"""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            raise ValueError("矩陣奇異，資料不足")
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _fit_daytype(rows, weights, alpha):
    """加權 ridge：小時截距不懲罰，氣象斜率（標準化後）懲罰 alpha。"""
    hours = sorted({r["hour"] for r in rows})
    wsum = sum(weights)
    mu = {f: sum(w * r[f] for r, w in zip(rows, weights)) / wsum for f in FEATURES}
    sd = {f: math.sqrt(sum(w * (r[f] - mu[f]) ** 2 for r, w in zip(rows, weights)) / wsum) or 1.0 for f in FEATURES}
    n = len(hours) + len(FEATURES)
    xtx = [[0.0] * n for _ in range(n)]
    xty = [0.0] * n
    for r, w in zip(rows, weights):
        x = [1.0 if r["hour"] == h else 0.0 for h in hours] + [(r[f] - mu[f]) / sd[f] for f in FEATURES]
        y = r["y"] + (MAG_OFF_KW if r.get("mag_off") else 0.0)
        for i in range(n):
            if x[i] == 0.0:
                continue
            xty[i] += w * x[i] * y
            for j in range(n):
                xtx[i][j] += w * x[i] * x[j]
    for k in range(len(hours), n):
        xtx[k][k] += max(alpha, 1e-6) * wsum / len(rows)   # 極小值避免某氣象欄全為常數時矩陣奇異
    beta = _solve(xtx, xty)
    return {"intercept": {str(h): round(beta[i], 2) for i, h in enumerate(hours)},
            "slope": {f: round(beta[len(hours) + i], 3) for i, f in enumerate(FEATURES)},
            "mu": {f: round(mu[f], 3) for f in FEATURES}, "sd": {f: round(sd[f], 3) for f in FEATURES}}


def fit(rows, ref_date, halflife=21.0, alpha=3.0):
    """rows：[{date, hour, daytype, y, T, RH, CC, R, P}]（date 為 datetime.date）。回傳模型 dict（不含分位數）。"""
    model = {"halflife_days": halflife, "alpha": alpha, "hours": HOURS, "mag_off_kw": MAG_OFF_KW, "coef": {}, "n_days": {}}
    for dt in ("work", "off"):
        sub = [r for r in rows if r["daytype"] == dt and r["hour"] in HOURS and r["date"] <= ref_date]
        if len({r["hour"] for r in sub}) < len(HOURS):
            continue
        weights = [0.5 ** ((ref_date - r["date"]).days / halflife) for r in sub]
        model["coef"][dt] = _fit_daytype(sub, weights, alpha)
        model["n_days"][dt] = len({r["date"] for r in sub})
    return model


def fit_pv(rows, ref_date, halflife=21.0):
    """rows 中有 pv 欄位者：PV = c[小時] + bR × 日射/100 + bCC × 雲量/100（加權最小平方）。資料不足回傳 None。"""
    sub = [r for r in rows if r.get("pv") is not None and r["hour"] in HOURS and r["date"] <= ref_date]
    hours = sorted({r["hour"] for r in sub})
    if len({r["date"] for r in sub}) < 14 or len(hours) < len(HOURS):
        return None
    n = len(hours) + 2
    xtx = [[0.0] * n for _ in range(n)]
    xty = [0.0] * n
    for r in sub:
        w = 0.5 ** ((ref_date - r["date"]).days / halflife)
        x = [1.0 if r["hour"] == h else 0.0 for h in hours] + [r["R"] / 100.0, r["CC"] / 100.0]
        for i in range(n):
            if x[i] == 0.0:
                continue
            xty[i] += w * x[i] * r["pv"]
            for j in range(n):
                xtx[i][j] += w * x[i] * x[j]
    for k in range(len(hours), n):
        xtx[k][k] += 1e-6
    b = _solve(xtx, xty)
    return {"intercept": {str(h): round(b[i], 2) for i, h in enumerate(hours)},
            "R": round(b[-2], 3), "CC": round(b[-1], 3), "n_days": len({r["date"] for r in sub})}


# ---------------- 預測 ----------------

def predict_hour(model, dt, hour, wx, mag_off=False):
    """wx：{T, RH, CC, R, P}；缺的氣象項用訓練平均（等於不修正）。mag_off：該小時磁浮全關。"""
    c = model["coef"].get(dt)
    if c is None or str(hour) not in c["intercept"]:
        return None
    y = c["intercept"][str(hour)] - (model.get("mag_off_kw", MAG_OFF_KW) if mag_off else 0.0)
    for f in FEATURES:
        v = wx.get(f)
        if v is not None:
            y += c["slope"][f] * (v - c["mu"][f]) / c["sd"][f]
    return y


def predict_day(model, dt, wx_by_hour, mag_off_hours=()):
    """wx_by_hour：{小時(int): wx}；回傳 {小時: kW}。"""
    out = {}
    for h in HOURS:
        p = predict_hour(model, dt, h, wx_by_hour.get(h, {}), h in mag_off_hours)
        if p is not None:
            out[h] = p
    return out


def predict_pv(model, hour, wx):
    """明日該小時太陽能 kW；沒有 PV 模型或缺日射／雲量時回傳 None。"""
    p = model.get("pv")
    if not p or str(hour) not in p["intercept"] or wx.get("R") is None or wx.get("CC") is None:
        return None
    return min(PV_MAX_KW, max(0.0, p["intercept"][str(hour)] + p["R"] * wx["R"] / 100.0 + p["CC"] * wx["CC"] / 100.0))


def day_pv(model, wx_by_hour, hours=BLOCKS["semi"]):
    """白天半尖峰時段的平均預估 PV（kW）；無法估算時回傳 None。"""
    vals = [predict_pv(model, h, wx_by_hour.get(h, {})) for h in hours]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def block_upper(model, dt, preds, q="q95"):
    """各時段最壞情況：該時段預測最高 ＋ 歷史上「實測最高 − 預測最高」的分位數。回傳 {block: (預測最高, 上界)}。"""
    out = {}
    for blk, hrs in BLOCKS.items():
        vals = [preds[h] for h in hrs if h in preds]
        rq = model.get("resid_q", {}).get(dt, {}).get(blk)
        if vals and rq:
            out[blk] = (max(vals), max(vals) + rq[q])
    return out


def quantile(vals, q):
    s = sorted(vals)
    if not s:
        return None
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def load(path=DEFAULT_PATH):
    """讀模型檔；不存在或壞掉時回傳 None（預測引擎改用原本的物理模型）。"""
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        return m if m.get("coef") else None
    except (OSError, ValueError):
        return None
