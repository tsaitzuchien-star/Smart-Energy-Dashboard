import streamlit as st
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(page_title="氣象署 X光機", layout="wide")
st.title("🚨 氣象署原始資料 X光機 (無敵版)")

api_key = "CWA-3DD5DB13-517F-4C53-8A1C-0D2FB1595975"
url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-021?Authorization={api_key}&locationName=南投市"

try:
    res = requests.get(url, verify=False, timeout=10)
    data = res.json()
    
    # 無敵包容模式抓取
    records = data.get('records', {})
    locs = records.get('locations', records.get('Locations', []))
    
    if locs:
        target_locs = locs[0].get('location', locs[0].get('Location', []))
        if target_locs:
            st.success("✅ 成功抓到南投市！氣象署回傳的『天氣元素』原始結構如下：")
            st.json(target_locs[0].get('weatherElement', []))
        else:
            st.error("找不到 location 欄位，原始回傳內容：")
            st.json(data)
    else:
        st.error("找不到 locations 欄位，原始回傳內容：")
        st.json(data)
        
except Exception as e:
    st.error(f"發生錯誤：{e}")