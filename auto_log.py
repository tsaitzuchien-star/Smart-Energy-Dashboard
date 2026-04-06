import os
import json
import requests
import urllib3
from datetime import datetime, timedelta, timezone
import gspread
from google.oauth2.service_account import Credentials

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
TW_TZ = timezone(timedelta(hours=8))

# --- 1. 抓取環境變數中的金鑰 ---
creds_json_str = os.environ.get("GOOGLE_CREDENTIALS")
if not creds_json_str:
    print("❌ 找不到 GOOGLE_CREDENTIALS 環境變數")
    exit(1)
    
creds_info = json.loads(creds_json_str)
scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
client = gspread.authorize(creds)

# --- 2. 執行預測邏輯 (簡化版) ---
now_dt = datetime.now(TW_TZ)
current_month = now_dt.month
today_str = now_dt.strftime("%Y-%m-%d")
tmr_dt = now_dt + timedelta(days=1)

# ⚠️ 這裡請務必把您 app.py 裡面的 get_dual_weather() 和算需量的核心邏輯貼過來！
# 下面為假資料示意：
w = {"fetch_time": now_dt.strftime('%Y-%m-%d %H:%M:%S')}
occupancy_rate = 70
temp = 25.0
current_rad = 200
today_worst_hour = "14:00"
today_max_net = 350.0
tmr_temp = 26.0
est_solar = 80.0
worst_hour = "16:00"
max_net_grid_demand = 400.0
suggested_ice_hrs = 5.0
# -------------------------------------

# --- 3. 寫入 Google Sheets ---
try:
    sheet = client.open('中創園區空調戰情大數據').sheet1
    expected_headers = [
        "紀錄時間", "今日進駐率(%)", "今日氣溫(°C)", "今日輻射(W/m²)", 
        "今日最危險時段", "今日最高需量(kW)", "明日預估高溫(°C)", 
        "明日太陽能峰值(kW)", "明日最危險時段", "明日預估最高需量(kW)", "建議今晚儲冰(小時)"
    ]
    if not sheet.row_values(1): sheet.append_row(expected_headers)
    
    data_row = [w['fetch_time'], occupancy_rate, temp, current_rad, today_worst_hour, today_max_net, tmr_temp, est_solar, worst_hour, max_net_grid_demand, suggested_ice_hrs]
    sheet.append_row(data_row)
    print(f"✅ 成功寫入資料庫：{w['fetch_time']}")
except Exception as e:
    print(f"❌ 寫入失敗：{e}")