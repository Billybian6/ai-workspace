
import os
import sys
import json
import pandas as pd
from datetime import datetime, timedelta
import re
import time
from urllib.request import Request, urlopen

# 添加 EmQuantAPI 路径
sys.path.append(r'C:\Users\bianzhengzhi\Desktop\EMQuantAPI_Python\EMQuantAPI_Python\python3')
from EmQuantAPI import c

def format_date(d_str):
    """确保日期格式为 YYYY-MM-DD，带补零"""
    return pd.to_datetime(d_str).strftime("%Y-%m-%d")

def percentile_rank(history_values, current_value):
    """Return current_value's percentile rank in history_values."""
    values = [v for v in history_values if pd.notna(v)]
    if not values:
        return 10
    below_or_equal = sum(1 for v in values if v <= current_value)
    return round(below_or_equal / len(values) * 100, 0)

def safe_number(value, default=None):
    """Convert Choice/Eastmoney values to float, treating None/NaN as missing."""
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def numeric_series(series):
    return pd.to_numeric(series, errors="coerce").fillna(0)

def get_json_with_retry(url, retries=3, timeout=20):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    last_error = None
    for attempt in range(retries):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            time.sleep(1 + attempt)
    raise last_error

def load_cached_records(code, all_dates):
    json_path = r'C:\Users\bianzhengzhi\Desktop\AI工作台\congestion_data.json'
    if not os.path.exists(json_path):
        return {}
    with open(json_path, 'r', encoding='utf-8') as f:
        cached = json.load(f)
    raw = cached.get("RAW_DATA", {}).get(code, {})
    return {dt: raw[dt] for dt in all_dates if dt in raw}

def fetch_choice_microcap_records(history_start_date, end_date, all_dates):
    """Fetch Choice microcap index 800007 from Eastmoney K-line API."""
    start_dt = pd.to_datetime(history_start_date)
    end_dt = pd.to_datetime(end_date)
    lines_by_date = {}

    while start_dt <= end_dt:
        chunk_end = min(start_dt + timedelta(days=180), end_dt)
        beg = start_dt.strftime("%Y%m%d")
        end = chunk_end.strftime("%Y%m%d")
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            "?secid=47.800007"
            "&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt=101&fqt=0&beg={beg}&end={end}"
        )
        payload = get_json_with_retry(url)
        for line in payload.get("data", {}).get("klines", []):
            dt = line.split(",", 1)[0]
            lines_by_date[dt] = line
        start_dt = chunk_end + timedelta(days=1)

    records = {}
    history_turnover_rates = []
    for line in [lines_by_date[dt] for dt in sorted(lines_by_date)]:
        parts = line.split(",")
        dt = format_date(parts[0])
        amount_yuan = float(parts[6])
        turnover_rate = float(parts[10])
        history_turnover_rates.append(turnover_rate)
        if dt not in all_dates:
            continue
        turnover_yi = amount_yuan / 1e8
        free_mv_yi = turnover_yi / (turnover_rate / 100) if turnover_rate > 0 else 0
        records[dt] = {
            "turnover": round(turnover_yi, 2),
            "free_mv": round(free_mv_yi, 2),
            "percentile": percentile_rank(history_turnover_rates, turnover_rate)
        }

    return records

def fetch_congestion_data():
    try:
        # 1. Login
        res = c.start()
        if res.ErrorCode != 0:
            print(f"Login failed: {res.ErrorMsg}")
            return
        
        # 2. Config
        # 宽基指数映射 (HTML显示名: Choice代码)
        INDEX_MAP = {
            '000016': '000016.SH', # 上证50
            '000300': '000300.SH', # 沪深300
            '000905': '000905.SH', # 中证500
            '000852': '000852.SH', # 中证1000
            '000464': '931446.CSI', # 中证2000
            '399006': '399006.SZ', # 创业板指
            '000688': '000688.SH', # 科创50
            '8841331': '000922.SH', # 中证红利
            '800007':  '800007', # Choice微盘股指数
        }
        
        # 3. Get Dates
        today_dt = datetime.now()
        today = today_dt.strftime("%Y-%m-%d")
        # 抓取最近35天内的交易日，确保有足够数据填充
        start_search = (today_dt - timedelta(days=35)).strftime("%Y-%m-%d")
        dates_res = c.tradedates(start_search, today)
        if dates_res.ErrorCode != 0:
             print("Failed to get trade dates")
             return
        
        # 统一日期格式 YYYY-MM-DD
        all_dates = [format_date(d) for d in dates_res.Data]
        # 只取最近20个交易日（HTML展示区间）
        all_dates = all_dates[-20:]
        latest_trade_date = all_dates[-1]
        history_start = (pd.to_datetime(latest_trade_date) - timedelta(days=365 * 3 + 30)).strftime("%Y-%m-%d")
        print(f"Analyzing {len(all_dates)} days from {all_dates[0]} to {latest_trade_date}")
        print(f"Percentile history window starts from {history_start}")
        
        # 4. Fetch Market Total (All A-shares)
        # 使用 000985.CSI 作为全市场成交额参考
        print("Fetching Market Total Amount History (000985.CSI)...")
        market_amt_df = c.csd("000985.CSI", "AMOUNT", all_dates[0], latest_trade_date, "Ispandas=1")
        if not isinstance(market_amt_df, pd.DataFrame) or market_amt_df.empty:
            print("Failed to get valid market amount history")
            return
        market_amt_df["AMOUNT"] = numeric_series(market_amt_df["AMOUNT"])
        valid_market_dates = {
            format_date(row["DATES"])
            for _, row in market_amt_df.iterrows()
            if safe_number(row.get("AMOUNT"), 0) > 0
        }
        filtered_dates = [dt for dt in all_dates if dt in valid_market_dates]
        if not filtered_dates:
            print("No completed trading day has valid AMOUNT; aborting update.")
            return
        if filtered_dates[-1] != all_dates[-1]:
            print(f"Latest trade date {all_dates[-1]} has no completed AMOUNT yet; using {filtered_dates[-1]} as latest valid close.")
            all_dates = filtered_dates
            latest_trade_date = all_dates[-1]
            history_start = (pd.to_datetime(latest_trade_date) - timedelta(days=365 * 3 + 30)).strftime("%Y-%m-%d")
            market_amt_df = market_amt_df[market_amt_df["DATES"].apply(format_date).isin(all_dates)]
        
        print("Fetching current listed A-shares...")
        all_a_res = c.sector("001004", latest_trade_date)
        all_a_codes = [code for code in all_a_res.Data if (code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".BJ"))]
        
        print(f"Fetching FreeFloatMV & Amount for stocks...")
        batch_size = 500
        all_mv_data = []
        for i in range(0, len(all_a_codes), batch_size):
            batch = all_a_codes[i:i+batch_size]
            res_css = c.css(",".join(batch), "FREEFLOATMV,AMOUNT", f"TradeDate={latest_trade_date},Ispandas=1")
            if isinstance(res_css, pd.DataFrame) and not res_css.empty:
                all_mv_data.append(res_css)
        
        mv_df = pd.concat(all_mv_data)
        mv_df["FREEFLOATMV"] = numeric_series(mv_df["FREEFLOATMV"])
        mv_df["AMOUNT"] = numeric_series(mv_df["AMOUNT"])
        # FREEFLOATMV 在 css 中单位是元
        total_market_free_mv_yi = mv_df["FREEFLOATMV"].sum() / 1e8
        print(f"Market FreeFloatMV: {total_market_free_mv_yi:.2f} 亿")
        
        # 前5%成交额占比
        sorted_amt = mv_df["AMOUNT"].sort_values(ascending=False)
        top5_count = int(len(sorted_amt) * 0.05)
        top5_amt_sum = sorted_amt.head(top5_count).sum()
        market_total_amt_today = sorted_amt.sum()
        top5_ratio = (top5_amt_sum / market_total_amt_today * 100) if market_total_amt_today > 0 else 0
        
        final_json = {
            "ALL_DATES": all_dates,
            "MARKET_TOTAL": {},
            "RAW_DATA": {},
            "TOP5_RATIO": {}
        }
        
        for _, row in market_amt_df.iterrows():
            dt = format_date(row["DATES"])
            amount = safe_number(row.get("AMOUNT"), 0)
            final_json["MARKET_TOTAL"][dt] = {
                "turnover": round(amount / 1e8, 2),
                "free_float_mv": round(total_market_free_mv_yi, 2)
            }
            final_json["TOP5_RATIO"][dt] = round(top5_ratio, 1)

        # 5. Fetch Index Data
        for html_id, choice_code in INDEX_MAP.items():
            print(f"Processing {html_id} ({choice_code})...")
            if html_id == "800007":
                try:
                    final_json["RAW_DATA"][html_id] = fetch_choice_microcap_records(
                        history_start,
                        latest_trade_date,
                        all_dates
                    )
                except Exception as micro_error:
                    print(f"Microcap fetch failed, using cache: {micro_error}")
                    cached_records = load_cached_records(html_id, all_dates)
                    if not cached_records:
                        raise
                    final_json["RAW_DATA"][html_id] = cached_records
                continue

            # 历史成交额：展示最近20个交易日，但分位数用近3年滚动窗口。
            idx_hist = c.csd(choice_code, "AMOUNT", history_start, latest_trade_date, "Ispandas=1")
            # 成份股流通市值之和 (EndDate 必须)
            idx_const = c.ctr("IndexConstituent", "", f"IndexCode={choice_code},EndDate={latest_trade_date},Ispandas=1")
            
            if isinstance(idx_const, pd.DataFrame) and not idx_const.empty:
                # SHRMARKETVALUE 在 ctr 中是亿元
                idx_free_mv_yi = numeric_series(idx_const["SHRMARKETVALUE"]).sum()
            else:
                # Fallback: 使用指数自带的换手率反推 (假设 TURN 是基于自由流通市值)
                idx_css = c.css(choice_code, "AMOUNT,TURN", f"TradeDate={latest_trade_date},Ispandas=1")
                css_amount = safe_number(idx_css["AMOUNT"].iloc[0], 0) if isinstance(idx_css, pd.DataFrame) and not idx_css.empty else 0
                css_turn = safe_number(idx_css["TURN"].iloc[0], 0) if isinstance(idx_css, pd.DataFrame) and not idx_css.empty else 0
                if css_turn > 0:
                    idx_free_mv_yi = (css_amount / 1e8) / (css_turn / 100)
                else:
                    idx_free_mv_yi = 10000 # 兜底
            
            idx_records = {}
            if isinstance(idx_hist, pd.DataFrame):
                history_congestion = []
                for _, row in idx_hist.iterrows():
                    dt = format_date(row["DATES"])
                    amount = safe_number(row.get("AMOUNT"), 0)
                    turnover_yi = amount / 1e8
                    cong = (turnover_yi / idx_free_mv_yi * 100) if idx_free_mv_yi > 0 else 0
                    history_congestion.append(cong)
                    if dt not in all_dates:
                        continue

                    idx_records[dt] = {
                        "turnover": round(turnover_yi, 2),
                        "free_mv": round(idx_free_mv_yi, 2),
                        "percentile": percentile_rank(history_congestion, cong)
                    }
            final_json["RAW_DATA"][html_id] = idx_records

        # 6. Update HTML
        html_path = r'C:\Users\bianzhengzhi\Desktop\AI工作台\inbox\宽基指数交易拥挤度分析.html'
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 清除旧的 setRaw 初始化
        content = re.sub(r'setRaw\(.*?\);', '', content, flags=re.DOTALL)
        content = re.sub(r'// --- .*? ---\s+setRaw\(.*?\);', '', content, flags=re.DOTALL)
        content = re.sub(r'setRaw\(\'[0-9]+\', \{.*?\)\;', '', content, flags=re.DOTALL)

        # 替换核心数据变量
        def replace_var(name, value, content):
            pattern = rf'const {name} = .*?;'
            replacement = f'const {name} = {json.dumps(value, ensure_ascii=False)};'
            return re.sub(pattern, replacement, content, count=1, flags=re.DOTALL)

        content = replace_var("ALL_DATES", all_dates, content)
        content = replace_var("MARKET_TOTAL", final_json["MARKET_TOTAL"], content)
        content = replace_var("TOP5_RATIO", final_json["TOP5_RATIO"], content)
        content = replace_var("RAW_DATA", final_json["RAW_DATA"], content)

        json_path = r'C:\Users\bianzhengzhi\Desktop\AI工作台\congestion_data.json'
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(final_json, f, ensure_ascii=False, indent=2)
        
        # 更新更新时间显示
        update_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        content = re.sub(r'⏱ 数据更新：.*最近交易日', f'⏱ 数据更新：{update_str} | 最近交易日', content)
        
        content = re.sub(
            r'document\.getElementById\(\'startDate\'\)\.value = ".*?";',
            f'document.getElementById(\'startDate\').value = "{all_dates[0]}";',
            content,
            count=1
        )
        content = re.sub(
            r'document\.getElementById\(\'endDate\'\)\.value = ".*?";',
            f'document.getElementById(\'endDate\').value = "{all_dates[-1]}";',
            content,
            count=1
        )

        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"Done! HTML updated successfully. Latest date: {latest_trade_date}")
        c.stop()
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    fetch_congestion_data()
