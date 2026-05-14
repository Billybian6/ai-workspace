
import os
import sys
import json
import pandas as pd
from datetime import datetime, timedelta
import re

# 添加 EmQuantAPI 路径
sys.path.append(r'C:\Users\bianzhengzhi\Desktop\EMQuantAPI_Python\EMQuantAPI_Python\python3')
from EmQuantAPI import c

def fetch_congestion_data():
    try:
        # 1. Login
        res = c.start()
        if res.ErrorCode != 0:
            print(f"Login failed: {res.ErrorMsg}")
            return
        
        # 2. Config
        INDEX_MAP = {
            '000016': '000016.SH', # 上证50
            '000300': '000300.SH', # 沪深300
            '000905': '000905.SH', # 中证500
            '000852': '000852.SH', # 中证1000
            '000464': '931446.CSI', # 中证2000
            '399006': '399006.SZ', # 创业板指
            '000688': '000688.SH', # 科创50
            '8841331': '000922.SH', # 中证红利
            '868008':  '931446.CSI', # 万得微盘 (用中证2000替代)
        }
        
        # 3. Get Dates
        today = "2026-05-14"
        dates_res = c.tradedates("2026-04-20", today)
        all_dates = [d.replace("/", "-") for d in dates_res.Data]
        
        # 4. Fetch Market Total
        market_amt_df = c.csd("000985.CSI", "AMOUNT", all_dates[0], all_dates[-1], "Ispandas=1")
        all_a_res = c.sector("001004", today)
        all_a_codes = [code for code in all_a_res.Data if (code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".BJ"))]
        
        batch_size = 500
        all_mv_data = []
        for i in range(0, len(all_a_codes), batch_size):
            batch = all_a_codes[i:i+batch_size]
            res_css = c.css(",".join(batch), "FREEFLOATMV,AMOUNT", f"TradeDate={today},Ispandas=1")
            if isinstance(res_css, pd.DataFrame) and not res_css.empty:
                all_mv_data.append(res_css)
        
        mv_df = pd.concat(all_mv_data)
        total_market_free_mv_yi = mv_df["FREEFLOATMV"].sum() / 1e8
        
        sorted_amt = mv_df["AMOUNT"].sort_values(ascending=False)
        top5_ratio = (sorted_amt.head(int(len(sorted_amt)*0.05)).sum() / sorted_amt.sum() * 100)
        
        final_json = {
            "ALL_DATES": all_dates,
            "MARKET_TOTAL": {},
            "RAW_DATA": {},
            "TOP5_RATIO": {}
        }
        
        for _, row in market_amt_df.iterrows():
            dt = row["DATES"].replace("/", "-")
            final_json["MARKET_TOTAL"][dt] = {"turnover": round(row["AMOUNT"]/1e8, 2), "free_float_mv": round(total_market_free_mv_yi, 2)}
            final_json["TOP5_RATIO"][dt] = round(top5_ratio, 1)

        # 5. Fetch Index Data
        for html_id, choice_code in INDEX_MAP.items():
            idx_hist = c.csd(choice_code, "AMOUNT", all_dates[0], all_dates[-1], "Ispandas=1")
            idx_const = c.ctr("IndexConstituent", "", f"IndexCode={choice_code},EndDate={today},Ispandas=1")
            idx_free_mv_yi = idx_const["SHRMARKETVALUE"].sum() if isinstance(idx_const, pd.DataFrame) else 10000
            
            idx_records = {}
            if isinstance(idx_hist, pd.DataFrame):
                for _, row in idx_hist.iterrows():
                    dt = row["DATES"].replace("/", "-")
                    cong = (row["AMOUNT"]/1e8 / idx_free_mv_yi * 100)
                    idx_records[dt] = {"turnover": round(row["AMOUNT"]/1e8, 2), "free_mv": round(idx_free_mv_yi, 2), "percentile": round(min(max(cong*10, 10), 95), 0)}
            final_json["RAW_DATA"][html_id] = idx_records

        # 6. Update HTML
        html_path = r'C:\Users\bianzhengzhi\Desktop\AI工作台\inbox\宽基指数交易拥挤度分析.html'
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 移除旧的 setRaw 调用和硬编码块
        content = re.sub(r'setRaw\(.*?\);', '', content, flags=re.DOTALL)
        content = re.sub(r'// --- .*? ---\s+setRaw\(.*?\);', '', content, flags=re.DOTALL)
        
        # 替换变量
        def replace_var(name, value, content):
            pattern = rf'const {name} = .*?;'
            replacement = f'const {name} = {json.dumps(value, ensure_ascii=False)};'
            return re.sub(pattern, replacement, content, count=1, flags=re.DOTALL)

        content = replace_var("ALL_DATES", all_dates, content)
        content = replace_var("MARKET_TOTAL", final_json["MARKET_TOTAL"], content)
        content = replace_var("TOP5_RATIO", final_json["TOP5_RATIO"], content)
        content = replace_var("RAW_DATA", final_json["RAW_DATA"], content)
        
        # 更新时间
        update_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        content = re.sub(r'⏱ 数据更新：.*?最近交易日', f'⏱ 数据更新：{update_str} | 最近交易日', content)

        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(content)

        print("Done! HTML updated and cleaned successfully.")
        c.stop()
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    fetch_congestion_data()
