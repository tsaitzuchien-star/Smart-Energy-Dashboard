import requests
import urllib3
import csv
import os
from datetime import datetime

# 從我們剛剛寫好的模組中，把大腦算式借過來用！
from energy_calculator import calculate_dispatch

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 抓取電腦本身的時間作為報告標準時間
current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
log_file = "energy_decision_log.csv"

print("==================================================")
print(f"[{current_time}] 🚀 氣候動能 × 儲冰空調聯防系統：全面啟動")
print("==================================================")

# --- 階段一：啟動氣象之眼 ---
print("📡 [階段 1/2] 正在連線氣象署，抓取南投市最新環境數據...")
api_key = "CWA-3DD5DB13-517F-4C53-8A1C-0D2FB1595975"
url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-021?Authorization={api_key}&locationName=南投市"

try:
    response = requests.get(url, verify=False)
    data = response.json()
    
    records = data.get('records', {})
    locations = records.get('locations', records.get('Locations', []))
    target_city = locations[0].get('location', locations[0].get('Location', []))[0]
    elements = target_city.get('weatherElement', [])
    
    pop, wx, cloud = "0", "未知", "0"
    for e in elements:
        name = e.get('elementName')
        if name == 'PoP12h': pop = e['time'][0]['elementValue'][0]['value']
        elif name == 'Wx': wx = e['time'][0]['elementValue'][0]['value']
        elif name == 'TCC': cloud = e['time'][0]['elementValue'][0]['value']
    
    prob_val = int(pop) if pop.isdigit() else 0
    cloud_val = int(cloud) if cloud.isdigit() else 0

    print(f"✅ 氣象數據取得成功！(天氣: {wx}, 降雨機率: {prob_val}%, 雲量: {cloud_val}%)")
    print("-" * 50)
    
    # --- 階段二：啟動聯防大腦 ---
    print("🧠 [階段 2/2] 將真實雲量匯入核心算式，進行排程精算...\n")
    
    # 【關鍵神經連結】：把剛剛抓到的真實雲量 (cloud_val)，直接餵給大腦精算！
    calculate_dispatch(cloud_cover=cloud_val)

    # --- 階段三：戰情日誌存檔 ---
    file_exists = os.path.isfile(log_file)
    with open(log_file, mode='a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['紀錄時間', '天氣描述', '降雨機率', '雲量', '系統執行狀態'])
        writer.writerow([current_time, wx, f"{prob_val}%", f"{cloud_val}%", "排程精算完成"])
        
    print(f"📝 系統日誌已更新至 {log_file}")
    print("==================================================")

except Exception as e:
    print(f"❌ 發生致命錯誤，請檢查網路或 API 狀態：{e}")