from datetime import datetime

def calculate_dispatch(cloud_cover, current_month=None, target_demand_rthr=2500, daytime_hours=9):
    """
    氣候動能 × 儲冰空調聯防：核心算式 (含歷史需量動態天花板)
    """
    # 1. 抓取系統標準時間
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 2. 自動抓取當下月份 (若無手動指定)
    if current_month is None:
        current_month = datetime.now().month

    print(f"[{current_time}] 啟動聯防核心算式 (載入 {current_month} 月份歷史基準)...")
    print("-" * 50)

    # --- 3. 硬體戰鬥參數設定 (中創園區專屬) ---
    SOLAR_MAX_KW = 146.0         # 太陽能最大出力
    MAG_MAX_KW = 141.8           # 變頻磁浮冰機滿載耗電
    MAG_MAX_RT = 240.0           # 變頻磁浮冰機最大製冷噸數
    ICE_MAX_RTHR = 2500.0        # 儲冰槽總蓄冷量
    NIGHT_CHILLER_KW = 241.0     # 夜間螺旋式製冰主機耗電
    NIGHT_BUILD_MAX_HRS = 9.0    # 儲滿冰所需時間

    # --- 4. 契約容量與 2025 歷史需量記憶庫 (kW) ---
    CONTRACT_LIMIT = 452.0  # 以最嚴格的尖峰容量作為防禦底線
    
    # 載入 2025 各月份歷史最高需量
    # 💡 特別註記：6 月歷史值 606kW 經查為人為誤開儲冰主機，已修正扣除 241kW，還原為真實基礎負載 365kW
    historical_max_demand = {
        1: 274, 2: 262, 3: 286, 4: 366, 5: 362, 6: 365, 
        7: 530, 8: 504, 9: 428, 10: 460, 11: 500, 12: 394
    }
    
    # 抓取該月份的歷史最高需量作為「基礎用電量」預估
    base_load_prediction = historical_max_demand.get(current_month, 400)

    # --- 5. 太陽能發電預估 (依雲量衰減係數) ---
    if cloud_cover < 15:
        solar_efficiency = 0.95  # 萬里無雲，幾乎滿血
    elif cloud_cover < 40:
        solar_efficiency = 0.60  # 薄雲，部分遮蔽
    elif cloud_cover < 70:
        solar_efficiency = 0.30  # 多雲，發電腰斬
    else:
        solar_efficiency = 0.10  # 陰天/厚雲，發電極微弱

    est_solar_kw = SOLAR_MAX_KW * solar_efficiency

    # --- 6. 智慧經濟匹配與超約防禦核心 ---
    # 算式：白天安全可用電力 = 契約極限 - 歷史基礎負載 + 太陽能支援
    safe_margin_kw = CONTRACT_LIMIT - base_load_prediction + est_solar_kw
    
    print(f"📊 歷史基準分析：本月歷史極端基礎需量估為 {base_load_prediction} kW")
    print(f"⚡ 預估太陽能支援：+{est_solar_kw:.1f} kW (依雲量 {cloud_cover}%)")
    print(f"🛡️ 估算白天安全可用電力：{safe_margin_kw:.1f} kW (防禦底線 {CONTRACT_LIMIT} kW)")

    # 決策邏輯：根據安全餘裕，決定 141.8 kW 磁浮冰機的運轉策略
    if safe_margin_kw >= MAG_MAX_KW:
        # 狀況 A：安全空間很大！磁浮機可以放手跑，太陽能幫忙出錢！
        usable_mag_kw = MAG_MAX_KW
        strategy = "🟢 額度充足：綠電直供磁浮主機，極小化夜間製冰以節省度數！"
    elif safe_margin_kw > 0:
        # 狀況 B：空間有限，磁浮機只能降頻跑
        usable_mag_kw = safe_margin_kw
        strategy = "🟡 額度緊繃：限制磁浮主機運轉頻率，白天依賴部分融冰支援。"
    else:
        # 狀況 C：已經超約了！磁浮機絕對不准開！
        usable_mag_kw = 0
        strategy = "🔴 超約警報：白天無安全電力！磁浮機封印，強制依賴全額融冰！"

    # --- 7. 轉換為製冰排程指令 ---
    # 計算磁浮機能幫忙負擔多少冷氣量 (RT-HR)
    mag_rt_output = (usable_mag_kw / MAG_MAX_KW) * MAG_MAX_RT
    mag_total_rthr = mag_rt_output * daytime_hours

    # 剩下的冷氣缺口，就是儲冰槽今晚要準備的量
    shortfall_rthr = target_demand_rthr - mag_total_rthr
    if shortfall_rthr < 0: shortfall_rthr = 0

    # 安全係數與保底：多抓 20% 容錯，且不管天氣多好都保底 375 RT-HR 冰量
    safe_target_rthr = max(375.0, min(ICE_MAX_RTHR, shortfall_rthr * 1.2))
    
    # 計算今晚需要開機幾小時、花多少度電
    suggested_ice_hours = (safe_target_rthr / ICE_MAX_RTHR) * NIGHT_BUILD_MAX_HRS
    est_night_power_kwh = suggested_ice_hours * NIGHT_CHILLER_KW

    # --- 8. 輸出戰情報告 ---
    print("-" * 50)
    print(f"🎯 系統決策指令：{strategy}")
    print(f"❄️ 磁浮冰機(白晝)預計負擔冷氣量：{mag_total_rthr:.1f} RT-HR")
    print(f"🧊 儲冰槽(夜間)需備妥融冰救援量：{safe_target_rthr:.1f} RT-HR")
    print(f"👉 建議今晚儲冰排程：【 {suggested_ice_hours:.1f} 小時 】")
    print(f"👉 預估夜間製冰耗電：{est_night_power_kwh:.1f} 度 (kWh)")
    print("==================================================\n")

# ==========================================
# 壓力測試區 (直接執行本檔案即可看到模擬結果)
# ==========================================
if __name__ == "__main__":
    print("【模擬測試一：修正後的 6 月 (已排除人為異常) + 陰雨天 (雲量 80%)】")
    # 測試系統是否知道 6 月已經安全，但因為沒太陽還是需要防禦
    calculate_dispatch(cloud_cover=80, current_month=6)
    
    print("【模擬測試二：可怕的 7 月 (歷史飆高 530kW) + 萬里無雲 (雲量 10%)】")
    # 測試系統在超約邊緣，如何利用太陽能神救援免除全額製冰
    calculate_dispatch(cloud_cover=10, current_month=7)
    
    print("【模擬測試三：舒適的 1 月 (歷史僅 274kW) + 多雲 (雲量 50%)】")
    # 測試系統在空間極大時，是否聰明地只做保底儲冰
    calculate_dispatch(cloud_cover=50, current_month=1)