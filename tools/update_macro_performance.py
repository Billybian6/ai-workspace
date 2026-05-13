import os
import glob
import pandas as pd
from datetime import datetime, timedelta

def get_latest_download(pattern, folder):
    files = glob.glob(os.path.join(folder, pattern))
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def update_macro_performance():
    downloads_folder = os.path.join(os.path.expanduser("~"), "Downloads")
    
    # 1. Find files
    latest_nav_file = get_latest_download("xq-beacon_downloads_*.xlsx", downloads_folder)
    macro_performance_file = os.path.join(downloads_folder, "宏观每周业绩.xlsx")
    
    if not latest_nav_file:
        print("未找到最新的 xq-beacon_downloads_*.xlsx 文件。")
        return
    
    if not os.path.exists(macro_performance_file):
        print(f"未找到底稿文件: {macro_performance_file}")
        return

    print(f"正在读取最新净值数据: {os.path.basename(latest_nav_file)}")
    print(f"正在更新底稿文件: {os.path.basename(macro_performance_file)}")

    # 2. Load data
    try:
        # Load macro performance file
        with pd.ExcelFile(macro_performance_file) as xls:
            df_perf = pd.read_excel(xls, sheet_name=0)
            df_history = pd.read_excel(xls, sheet_name=1)
            sheet_names = xls.sheet_names
    except Exception as e:
        print(f"读取底稿失败: {e}")
        return

    try:
        df_new_nav = pd.read_excel(latest_nav_file)
    except Exception as e:
        print(f"读取新净值数据失败: {e}")
        return

    # 3. Update Full NAV History (Sheet 1)
    # Convert dates to datetime objects for consistent comparison
    df_new_nav['end_date'] = pd.to_datetime(df_new_nav['end_date'])
    df_history['end_date'] = pd.to_datetime(df_history['end_date'])
    
    # Combine and drop duplicates
    df_combined_history = pd.concat([df_history, df_new_nav], ignore_index=True)
    df_combined_history = df_combined_history.drop_duplicates(subset=['symbol', 'end_date'], keep='last')
    df_combined_history = df_combined_history.sort_values(by=['symbol', 'end_date'])

    # 4. Update Macro Performance (Sheet 0)
    # Identify target date (latest Friday or latest date in new data)
    target_date = df_new_nav['end_date'].max()
    print(f"目标更新日期: {target_date.strftime('%Y-%m-%d')}")
    
    # Ensure target_date is in datetime format in df_perf columns if they are already datetimes
    # Date columns in df_perf are often datetime objects
    
    # Find Previous Friday (7 days before target_date if target_date is Friday)
    # If target_date is not Friday, we might need a more flexible logic, 
    # but based on the plan, we assume weekly updates on Fridays.
    prev_friday_date = target_date - timedelta(days=7)
    print(f"上周五日期 (用于计算涨跌幅): {prev_friday_date.strftime('%Y-%m-%d')}")

    # Prepare for updating returns and NAV columns
    # Ensure date columns in df_perf are compared correctly
    # We add the target_date column if it doesn't exist
    if target_date not in df_perf.columns:
        df_perf[target_date] = None

    # Update logic for each product
    for idx, row in df_perf.iterrows():
        p_code = row['P码']
        
        # Get latest NAV
        latest_nav_row = df_new_nav[(df_new_nav['symbol'] == p_code) & (df_new_nav['end_date'] == target_date)]
        if not latest_nav_row.empty:
            latest_nav = latest_nav_row.iloc[0]['unit_nav']
            df_perf.at[idx, target_date] = latest_nav
            
            # Get previous Friday NAV from combined history
            prev_nav_row = df_combined_history[(df_combined_history['symbol'] == p_code) & (df_combined_history['end_date'] == prev_friday_date)]
            if not prev_nav_row.empty:
                prev_nav = prev_nav_row.iloc[0]['unit_nav']
                # Calculate return
                if prev_nav and prev_nav != 0:
                    weekly_return = (latest_nav / prev_nav) - 1
                    df_perf.at[idx, '周度涨跌幅'] = weekly_return
            else:
                print(f"警告: 未找到产品 {p_code} 在 {prev_friday_date.strftime('%Y-%m-%d')} 的净值，无法计算涨跌幅。")

    # 5. Save Output
    output_file = os.path.join(downloads_folder, f"宏观每周业绩_更新_{target_date.strftime('%Y%m%d')}.xlsx")
    
    try:
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            df_perf.to_excel(writer, sheet_name=sheet_names[0], index=False)
            df_combined_history.to_excel(writer, sheet_name=sheet_names[1], index=False)
        print(f"更新成功！输出文件: {output_file}")
    except Exception as e:
        print(f"保存文件失败: {e}")

if __name__ == "__main__":
    update_macro_performance()
