import requests
import urllib3
import csv
import os
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 設定時間與檔案路徑
current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
log_file = "energy_decision_log.csv"

print(f"[{current_time}] 啟動深度雲層監控...")

api_key = "CWA-3DD5DB13-517F-4C53-8A1C-0D2FB1595975"
url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-021?Authorization={api_key}&locationName=南投市"

try:
    response = requests.get(url, verify=False)
    data = response.json()
    
    # 解析路徑
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
    
    # 嚴格判定邏輯
    mode = "☀️ 晴天攻擊"
    reason = "預期日照充足"
    if prob_val >= 50 or cloud_val >= 70:
        mode = "🌧️ 雨天/多雲防禦"
        reason = f"降雨{prob_val}% 或 雲量{cloud_val}% 過高"
    elif 30 <= cloud_val < 70:
        mode = "⛅ 混合調度"
        reason = "多雲不穩定"

    print(f"✅ 判定完成：目前處於【{mode}】")

    # --- 自動寫入 Excel/CSV 紀錄 ---
    file_exists = os.path.isfile(log_file)
    with open(log_file, mode='a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['紀錄時間', '天氣描述', '降雨機率', '雲量', '判定模式', '判定原因'])
        writer.writerow([current_time, wx, f"{prob_val}%", f"{cloud_val}%", mode, reason])
    
    print(f"📝 決策紀錄已更新至 {log_file}")

except Exception as e:
    print(f"❌ 錯誤：{e}")