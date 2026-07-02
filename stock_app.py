#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股多功能強勢股選股面板 - 終極飆速雲端防禦版
===================================================================
優化重點：自動偵測海外 IP 封鎖，無縫切換 twstock 內建離線資料庫
執行指令：streamlit run stock_app.py
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor

# 設定網頁配置
st.set_page_config(page_title="台股飆速雙模組選股器", layout="wide")

st.title("⚡ 台股強勢股自訂選股後台 (雲端防禦飆速版)")
st.markdown("""
本版本已啟動 **【平行運算】**、**【數據快取】** 與 **【海外 IP 智慧無縫切換機制】**。
---
""")

# ============================================================
# 1. 側邊欄 UI 設定欄位
# ============================================================
st.sidebar.header("🎯 選擇選股策略模組")

strategy_mode = st.sidebar.selectbox(
    "核心策略模式",
    options=["🔄 回檔支撐模式", "⚡ 強勢純突破模式", "📦 箱型糾結突破模式"],
    index=2
)

st.sidebar.markdown("---")

if strategy_mode == "🔄 回檔支撐模式":
    st.sidebar.subheader("📉 回檔專屬設定")
    ma_choice = st.sidebar.selectbox("選擇回檔判定均線", options=["5日均線 (5MA)", "10日均線 (10MA)", "20日均線 (20MA)"], index=2)
    target_ma = int(ma_choice.split("日")[0])
    pullback_pct = st.sidebar.slider("回檔均線容忍範圍 (±%)", min_value=0.1, max_value=1.5, value=0.2, step=0.1)
    pullback_ratio = pullback_pct / 100.0
    require_bullish = st.sidebar.checkbox("必須滿足均線多頭排列 (5MA > 10MA > 20MA)", value=True)

elif strategy_mode == "⚡ 強勢純突破模式":
    st.sidebar.subheader("⚡ 突破專屬設定")
    require_bullish = st.sidebar.checkbox("突破當日必須滿足均線多頭排列", value=True)
    target_ma, pullback_ratio = 20, 0.002

elif strategy_mode == "📦 箱型糾結突破模式":
    st.sidebar.subheader("📦 箱型盤整與糾結設定")
    box_days = st.sidebar.slider("1. 箱型盤整時間 (交易日)", min_value=15, max_value=60, value=30, step=5)
    box_height_limit = st.sidebar.slider("2. 箱型高低震幅限制 (%)", min_value=5.0, max_value=20.0, value=12.0, step=1.0)
    tangle_limit = st.sidebar.slider("3. 均線糾結度限制 (%)", min_value=1.5, max_value=5.0, value=3.0, step=0.5)
    require_bullish = False

st.sidebar.markdown("---")
st.sidebar.subheader("🛡️ 通用風控與量能篩選")

min_vol_lots = st.sidebar.number_input("今日或5日平均成交量需大於 (張)", min_value=100, max_value=10000, value=500, step=100)
vol_ratio_min = st.sidebar.slider("突破當日量能放大倍數 (倍)", min_value=1.1, max_value=3.0, value=1.5, step=0.1)
min_price = st.sidebar.number_input("最低股價門檻 (元)", min_value=0.0, value=10.0, step=5.0)

LOOKBACK_DAYS = 100
SCAN_DAYS = 3
BREAKOUT_LOOKBACK = 15

# ============================================================
# 2. 核心加速與雲端防禦：股票清單與大批次下載快取
# ============================================================
@st.cache_data(ttl=3600)
def get_all_taiwan_stocks_safe():
    tickers, name_map = [], {}
    is_fallback = False
    
    # 1. 優先嘗試官方 OpenAPI (本地執行時有效)
    try:
        resp = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=3)
        if resp.status_code == 200:
            for item in resp.json():
                code = item.get("CompanyCode", item.get("公司代號", ""))
                name = item.get("公司簡稱", "")
                if code.isdigit() and len(code) == 4:
                    t = f"{code}.TW"
                    tickers.append(t)
                    name_map[t] = f"{name} (上市)"
    except Exception: pass
    
    try:
        resp = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=3)
        if resp.status_code == 200:
            for item in resp.json():
                code = item.get("SecuritiesCompanyCode", "")
                name = item.get("CompanyName", "")
                if code.isdigit() and len(code) == 4:
                    t = f"{code}.TWO"
                    tickers.append(t)
                    name_map[t] = f"{name} (上櫃)"
    except Exception: pass
    
    # 2. 🌟 雲端救星：如果前面因為海外 IP 封鎖導致完全抓不到股票，無縫啟動離線備用方案！
    if not tickers:
        is_fallback = True
        try:
            import twstock
            for code, info in twstock.codes.items():
                if len(code) == 4 and code.isdigit():
                    if info.market == "上市" and info.type == "股票":
                        t = f"{code}.TW"
                        tickers.append(t)
                        name_map[t] = f"{info.name} (上市)"
                    elif info.market == "上櫃" and info.type == "股票":
                        t = f"{code}.TWO"
                        tickers.append(t)
                        name_map[t] = f"{info.name} (上櫃)"
        except Exception: pass
        
    return list(dict.fromkeys(tickers)), name_map, is_fallback


@st.cache_data(ttl=14400) 
def download_all_data_fast(tickers: list, start_str: str, end_str: str) -> dict:
    all_data = {}
    BATCH_SIZE = 400  
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    
    for batch in batches:
        try:
            raw = yf.download(
                batch, start=start_str, end=end_str,
                auto_adjust=True, progress=False, threads=True, group_by="ticker"
            )
            if len(batch) == 1:
                ticker = batch[0]
                if not raw.empty: all_data[ticker] = raw
            else:
                for ticker in batch:
                    try:
                        df = raw[ticker].dropna(how="all")
                        if not df.empty: all_data[ticker] = df
                    except (KeyError, TypeError): pass
        except Exception: pass
    return all_data


# ============================================================
# 3. 核心演算法 (單一股票分析)
# ============================================================
def analyze_single_stock(ticker: str, df: pd.DataFrame, params: dict) -> dict | None:
    try:
        df = df.sort_index()
        if len(df) < 40: return None

        df["MA5"]  = df["Close"].rolling(5).mean()
        df["MA10"] = df["Close"].rolling(10).mean()
        df["MA20"] = df["Close"].rolling(20).mean()
        df["Vol_MA10"] = df["Volume"].rolling(10).mean()   
        df["Vol_MA5"]  = df["Volume"].rolling(5).mean()  

        df = df.dropna(subset=["MA5", "MA10", "MA20", "Vol_MA10", "Vol_MA5"])
        n_rows = len(df)
        if n_rows < 20: return None

        if df["Close"].iloc[-1] < params["min_price"]: return None

        mode = params["mode"]
        
        if mode == "📦 箱型糾結突破模式":
            if (df["Volume"].iloc[-1] / 1000) < params["min_vol_lots"]: return None
            
            for i in range(n_rows - SCAN_DAYS, n_rows):
                if i - params["box_days"] < 0: continue
                box_df = df.iloc[i - params["box_days"] : i]
                box_max = box_df["High"].max()
                box_min = box_df["Low"].min()
                box_height = (box_max - box_min) / box_min * 100
                
                if box_height > params["box_height_limit"]: continue
                
                row_prev = df.iloc[i - 1]
                ma_vals = [row_prev["MA5"], row_prev["MA10"], row_prev["MA20"]]
                ma_tangle = (max(ma_vals) - min(ma_vals)) / min(ma_vals) * 100
                if ma_tangle > params["tangle_limit"]: continue
                
                row_curr = df.iloc[i]
                if row_curr["Close"] > box_max:
                    if row_curr["Vol_MA10"] <= 0: continue
                    v_ratio = row_curr["Volume"] / row_curr["Vol_MA10"]
                    if v_ratio >= params["vol_ratio_min"]:
                        b_date = row_curr.name.date() if hasattr(row_curr.name, "date") else row_curr.name
                        days_ago = n_rows - 1 - i
                        note = "🎉 今日正式突破箱型！" if days_ago == 0 else f"{days_ago} 天前突破"
                        return {
                            "股票代碼": ticker.split(".")[0], "操作型態": "📦 箱型突破", "訊號觸發日": b_date.strftime("%Y-%m-%d"),
                            "當前收盤價": round(df["Close"].iloc[-1], 2), "今日成交量(張)": int(df["Volume"].iloc[-1] / 1000),
                            "突破量能放大(倍)": round(v_ratio, 2), "備註說明": f"{note} (盤整{params['box_days']}天,震幅{round(box_height,1)}%,糾結{round(ma_tangle,1)}%)"
                        }
        else:
            if (df["Vol_MA5"].iloc[-1] / 1000) < params["min_vol_lots"]: return None
            
            if mode == "🔄 回檔支撐模式":
                ma_col = f"MA{params['target_ma']}"
                for i in range(n_rows - SCAN_DAYS, n_rows):
                    row_p = df.iloc[i]
                    if abs(row_p["Close"] - row_p[ma_col]) / row_p[ma_col] > params["pullback_ratio"]: continue
                    if params["require_bullish"] and not (row_p["MA5"] > row_p["MA10"] > row_p["MA20"]): continue
                    
                    for j in range(i - 1, max(1, i - BREAKOUT_LOOKBACK), -1):
                        row_b = df.iloc[j]
                        row_b_prev = df.iloc[j - 1]
                        if not (row_b["Close"] > row_b["MA5"] and row_b["Close"] > row_b["MA10"] and row_b["Close"] > row_b["MA20"]): continue
                        if not (row_b_prev["Close"] > row_b_prev["MA5"] or row_b_prev["Close"] > row_b_prev["MA10"] or row_b_prev["Close"] > row_b_prev["MA20"]):
                            if (row_b["MA20"] - row_b_prev["MA20"]) >= 0 and row_b["Vol_MA10"] > 0:
                                v_ratio = row_b["Volume"] / row_b["Vol_MA10"]
                                if v_ratio >= params["vol_ratio_min"]:
                                    p_date = row_p.name.date() if hasattr(row_p.name, "date") else row_p.name
                                    return {
                                        "股票代碼": ticker.split(".")[0], "操作型態": "🔄 回檔支撐", "訊號觸發日": p_date.strftime("%Y-%m-%d"),
                                        "當前收盤價": round(df["Close"].iloc[-1], 2), "今日成交量(張)": int(df["Volume"].iloc[-1] / 1000),
                                        "突破量能放大(倍)": round(v_ratio, 2), "備註說明": f"回測近{params['target_ma']}MA附近"
                                    }
            else: 
                for i in range(n_rows - SCAN_DAYS, n_rows):
                    row_b = df.iloc[i]
                    row_b_prev = df.iloc[i - 1]
                    if not (row_b["Close"] > row_b["MA5"] and row_b["Close"] > row_b["MA10"] and row_b["Close"] > row_b["MA20"]): continue
                    if not (row_b_prev["Close"] > row_b_prev["MA5"] and row_b_prev["Close"] > row_b_prev["MA10"] and row_b_prev["Close"] > row_b_prev["MA20"]):
                        if (row_b["MA20"] - row_b_prev["MA20"]) < 0 or row_b["Vol_MA10"] <= 0: continue
                        v_ratio = row_b["Volume"] / row_b["Vol_MA10"]
                        if v_ratio >= params["vol_ratio_min"]:
                            if params["require_bullish"] and not (df["MA5"].iloc[-1] > df["MA10"].iloc[-1] > df["MA20"].iloc[-1]): continue
                            b_date = row_b.name.date() if hasattr(row_b.name, "date") else row_b.name
                            return {
                                "股票代碼": ticker.split(".")[0], "操作型態": "⚡ 強勢純突破", "訊號觸發日": b_date.strftime("%Y-%m-%d"),
                                "當前收盤價": round(df["Close"].iloc[-1], 2), "今日成交量(張)": int(df["Volume"].iloc[-1] / 1000),
                                "突破量能放大(倍)": round(v_ratio, 2), "備註說明": "爆量強勢突圍三線"
                            }
        return None
    except Exception: return None

# ============================================================
# 4. 驅動核心與 UI 回饋
# ============================================================
all_tickers, name_map, is_fallback = get_all_taiwan_stocks_safe()

# 提示目前伺服器抓取狀態
if is_fallback:
    st.sidebar.warning("🛡️ 提示：偵測到雲端海外 IP 阻擋，已自動切換為內建離線台股代碼庫！")
else:
    st.sidebar.success("🟢 提示：成功透過台灣官方 API 獲取即時股票代碼！")

current_params = {
    "mode": strategy_mode,
    "target_ma": target_ma if 'target_ma' in locals() else 20,
    "pullback_ratio": pullback_ratio if 'pullback_ratio' in locals() else 0.002,
    "require_bullish": require_bullish,
    "min_vol_lots": min_vol_lots,
    "vol_ratio_min": vol_ratio_min,
    "min_price": min_price,
    "box_days": box_days if 'box_days' in locals() else 30,
    "box_height_limit": box_height_limit if 'box_height_limit' in locals() else 12.0,
    "tangle_limit": tangle_limit if 'tangle_limit' in locals() else 3.0
}

if st.sidebar.button("🚀 開始全自動高速掃描", use_container_width=True):
    t_start = time.time()
    
    today = datetime.today()
    end_date_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    start_date_str = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    
    status_box = st.empty()
    status_box.info(f"📥 正在從大盤抓取 {len(all_tickers)} 檔個股歷史數據 (首次需約 15~25 秒)...")
    
    cached_data_dict = download_all_data_fast(all_tickers, start_date_str, end_date_str)
    
    status_box.info("⚡ 數據載入成功！正在調動 CPU 所有線程並行計算指標...")
    
    results = []
    
    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(analyze_single_stock, ticker, df, current_params): ticker 
            for ticker, df in cached_data_dict.items()
        }
        for future in futures:
            res = future.result()
            if res:
                res["股票名稱"] = name_map.get(f"{res['股票代碼']}.TW", name_map.get(f"{res['股票代碼']}.TWO", "未知"))
                results.append(res)
                
    t_end = time.time()
    status_box.success(f"✅ 掃描完成！本次真實計算共耗時：{round(t_end - t_start, 2)} 秒。")
    
    st.subheader(f"🎯 篩選結果 (當前模式：{strategy_mode}，共找到 {len(results)} 檔)")
    if results:
        df_res = pd.DataFrame(results)
        cols = ["股票代碼", "股票名稱", "操作型態", "訊號觸發日", "當前收盤價", "今日成交量(張)", "突破量能放大(倍)", "備註說明"]
        df_res = df_res[cols]
        st.dataframe(df_res, use_container_width=True, hide_index=True)
        
        csv = df_res.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="💾 下載本次選股結果為 CSV",
            data=csv,
            file_name=f"tw_fast_stock_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )
    else:
        st.warning("⚠️ 沒有符合當前參數的股票。建議將『箱型高低震幅限制』稍微拉大至 15%，或將『均線糾結度』放寬到 4% 試試看！")

        #streamlit run stock_app.py
