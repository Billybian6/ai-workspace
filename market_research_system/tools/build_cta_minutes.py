import sqlite3
import pandas as pd
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR.parent / 'data' / 'research_warehouse.sqlite'
OUTPUT_FILE = SCRIPT_DIR.parent / 'data' / 'cache' / 'cta_intraday_data.js'

def build_data():
    conn = sqlite3.connect(DB_PATH)
    
    # 目标指数
    target_indices = ["沪深300", "中证500", "中证1000", "创业板指", "科创50", "Choice微盘"]
    
    result = {}
    
    for idx_name in target_indices:
        # 1. 查找最近 5 个交易日
        dates_df = pd.read_sql(
            "SELECT DISTINCT trade_date FROM index_minute_bars WHERE index_name=? ORDER BY trade_date DESC LIMIT 5",
            conn, params=(idx_name,)
        )
        if dates_df.empty:
            continue
        
        last_5_dates = dates_df['trade_date'].tolist()
        last_5_dates.sort()  # 升序排列
        
        # 2. 查询这5天的所有分钟线
        places_str = ','.join(['?'] * len(last_5_dates))
        query = f"""
            SELECT trade_datetime, close 
            FROM index_minute_bars 
            WHERE index_name=? AND trade_date IN ({places_str})
            ORDER BY trade_datetime ASC
        """
        params = [idx_name] + last_5_dates
        df = pd.read_sql(query, conn, params=params)
        
        if df.empty:
            continue
            
        times = df['trade_datetime'].tolist()
        closes = df['close'].tolist()
        
        # 基础归一化（用第一天的第一个点作为基准0），展示偏离度，让各个指数放一起比较
        base_price = closes[0]
        # 或者直接传绝对价格，前台展示。为了连贯性和方便前台Echarts直接画，我们传绝对价格，并在前台计算
        
        result[idx_name] = {
            "dates": last_5_dates,
            "times": times, # 格式一般是 '2026-05-18 10:30:00'
            "prices": closes
        }
        
    conn.close()
    
    # 写入JS
    js_content = f"window.CTA_INTRADAY_DATA = {json.dumps(result, ensure_ascii=False)};\n"
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(js_content, encoding='utf-8')
    print(f"数据已生成至 {OUTPUT_FILE}")

if __name__ == '__main__':
    build_data()
