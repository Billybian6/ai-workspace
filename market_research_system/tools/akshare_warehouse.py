import argparse
import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from research_db import CACHE_DIR, DB_PATH, connect, init_db, now_text


AK_SOURCE = "AKShare 1.17.5"
HEALTH_JSON = CACHE_DIR / "data_source_health.json"
HEALTH_JS = CACHE_DIR / "data_source_health.js"

INDEX_ASSETS = {
    "000016": {"name": "上证50", "ak_symbol": "sh000016", "method": "sina"},
    "000300": {"name": "沪深300", "ak_symbol": "sh000300", "method": "sina"},
    "000905": {"name": "中证500", "ak_symbol": "sh000905", "method": "sina"},
    "000852": {"name": "中证1000", "ak_symbol": "sh000852", "method": "sina"},
    "000464": {"name": "中证2000", "ak_symbol": "932000", "method": "csindex"},
    "399006": {"name": "创业板指", "ak_symbol": "sz399006", "method": "sina"},
    "000688": {"name": "科创50", "ak_symbol": "sh000688", "method": "sina"},
    "8841331": {"name": "中证红利", "ak_symbol": "000922", "method": "csindex"},
}

ETF_ASSETS = {
    "510050": {"name": "上证50ETF", "ak_symbol": "sh510050"},
    "510300": {"name": "沪深300ETF", "ak_symbol": "sh510300"},
    "510500": {"name": "中证500ETF", "ak_symbol": "sh510500"},
    "512100": {"name": "中证1000ETF", "ak_symbol": "sh512100"},
    "159915": {"name": "创业板ETF", "ak_symbol": "sz159915"},
    "588000": {"name": "科创50ETF", "ak_symbol": "sh588000"},
    "510880": {"name": "红利ETF", "ak_symbol": "sh510880"},
    "512880": {"name": "证券ETF", "ak_symbol": "sh512880"},
    "512480": {"name": "半导体ETF", "ak_symbol": "sh512480"},
    "515790": {"name": "光伏ETF", "ak_symbol": "sh515790"},
    "511010": {"name": "国债ETF", "ak_symbol": "sh511010"},
    "518880": {"name": "黄金ETF", "ak_symbol": "sh518880"},
    "159985": {"name": "豆粕ETF", "ak_symbol": "sz159985"},
}

FUND_ASSETS = {
    "000001": {"name": "华夏成长混合", "ak_symbol": "000001"},
    "110022": {"name": "易方达消费行业股票", "ak_symbol": "110022"},
    "161725": {"name": "招商中证白酒指数", "ak_symbol": "161725"},
}

FUTURE_ASSETS = {
    "IF0": {"name": "沪深300股指期货连续", "ak_symbol": "IF0"},
    "IC0": {"name": "中证500股指期货连续", "ak_symbol": "IC0"},
    "IM0": {"name": "中证1000股指期货连续", "ak_symbol": "IM0"},
    "IH0": {"name": "上证50股指期货连续", "ak_symbol": "IH0"},
}

PROBE_INDEX = ("000300", "000905")
PROBE_ETF = ("510300", "510500")
PROBE_FUND = ("000001", "110022")
PROBE_FUTURE = ("IF0", "IC0")


def import_akshare():
    import akshare as ak

    return ak


def safe_float(value):
    try:
        if value is None:
            return None
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    match = re.match(r"^(\d{4})年(\d{1,2})月份?$", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-01"
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def filter_by_date(rows, start, end):
    result = []
    for row in rows:
        dt = row.get("trade_date")
        if start and dt < start:
            continue
        if end and dt > end:
            continue
        result.append(row)
    return result


def raw_json(row):
    return json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":"))


def upsert_instrument(conn, asset_type, symbol, name, source, source_symbol):
    conn.execute(
        """
        INSERT INTO instruments (
          instrument_id, symbol, name, asset_type, exchange,
          source, source_symbol, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id) DO UPDATE SET
          symbol=excluded.symbol,
          name=excluded.name,
          asset_type=excluded.asset_type,
          exchange=excluded.exchange,
          source=excluded.source,
          source_symbol=excluded.source_symbol,
          updated_at=excluded.updated_at
        """,
        (
            f"{asset_type}:{symbol}",
            symbol,
            name,
            asset_type,
            source_symbol[:2] if source_symbol else None,
            source,
            source_symbol,
            now_text(),
        ),
    )


def insert_daily_rows(conn, rows):
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO market_daily_bars (
          asset_type, symbol, trade_date, source, name,
          open, high, low, close, volume, amount,
          turnover_rate, pct_chg, frequency, raw_json, updated_at
        )
        VALUES (
          :asset_type, :symbol, :trade_date, :source, :name,
          :open, :high, :low, :close, :volume, :amount,
          :turnover_rate, :pct_chg, :frequency, :raw_json, :updated_at
        )
        ON CONFLICT(asset_type, symbol, trade_date, source) DO UPDATE SET
          name=excluded.name,
          open=excluded.open,
          high=excluded.high,
          low=excluded.low,
          close=excluded.close,
          volume=excluded.volume,
          amount=excluded.amount,
          turnover_rate=excluded.turnover_rate,
          pct_chg=excluded.pct_chg,
          frequency=excluded.frequency,
          raw_json=excluded.raw_json,
          updated_at=excluded.updated_at
        """,
        rows,
    )
    return len(rows)


def upsert_health(conn, row):
    conn.execute(
        """
        INSERT INTO data_source_health (
          indicator_key, source, indicator_name, latest_date, frequency,
          rows_count, is_complete, status, detail, checked_at
        )
        VALUES (
          :indicator_key, :source, :indicator_name, :latest_date, :frequency,
          :rows_count, :is_complete, :status, :detail, :checked_at
        )
        ON CONFLICT(indicator_key, source) DO UPDATE SET
          indicator_name=excluded.indicator_name,
          latest_date=excluded.latest_date,
          frequency=excluded.frequency,
          rows_count=excluded.rows_count,
          is_complete=excluded.is_complete,
          status=excluded.status,
          detail=excluded.detail,
          checked_at=excluded.checked_at
        """,
        row,
    )


def make_health(indicator_key, indicator_name, source, latest_date, frequency, rows_count, complete, detail):
    return {
        "indicator_key": indicator_key,
        "indicator_name": indicator_name,
        "source": source,
        "latest_date": latest_date,
        "frequency": frequency,
        "rows_count": int(rows_count or 0),
        "is_complete": 1 if complete else 0,
        "status": "ok" if complete else "partial",
        "detail": detail,
        "checked_at": now_text(),
    }


def fail_health(indicator_key, indicator_name, source, frequency, error):
    return {
        "indicator_key": indicator_key,
        "indicator_name": indicator_name,
        "source": source,
        "latest_date": None,
        "frequency": frequency,
        "rows_count": 0,
        "is_complete": 0,
        "status": "failed",
        "detail": f"AKShare调用失败: {error}",
        "checked_at": now_text(),
    }


def latest_is_fresh(latest_date, max_lag_days=10):
    if not latest_date:
        return False
    try:
        latest = datetime.strptime(latest_date, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (datetime.now().date() - latest).days <= max_lag_days


def normalize_sina_daily(asset_type, symbol, name, source, df):
    rows = []
    for _, item in df.iterrows():
        trade_date = parse_date(item.get("date"))
        if not trade_date:
            continue
        source_row = item.to_dict()
        rows.append({
            "asset_type": asset_type,
            "symbol": symbol,
            "trade_date": trade_date,
            "source": source,
            "name": name,
            "open": safe_float(item.get("open")),
            "high": safe_float(item.get("high")),
            "low": safe_float(item.get("low")),
            "close": safe_float(item.get("close")),
            "volume": safe_float(item.get("volume")),
            "amount": safe_float(item.get("amount")),
            "turnover_rate": None,
            "pct_chg": None,
            "frequency": "daily",
            "raw_json": raw_json(source_row),
            "updated_at": now_text(),
        })
    return rows


def normalize_csindex_daily(symbol, name, source, df):
    rows = []
    for _, item in df.iterrows():
        trade_date = parse_date(item.get("日期"))
        if not trade_date:
            continue
        source_row = item.to_dict()
        amount_yi = safe_float(item.get("成交金额"))
        rows.append({
            "asset_type": "index",
            "symbol": symbol,
            "trade_date": trade_date,
            "source": source,
            "name": name,
            "open": safe_float(item.get("开盘")),
            "high": safe_float(item.get("最高")),
            "low": safe_float(item.get("最低")),
            "close": safe_float(item.get("收盘")),
            "volume": safe_float(item.get("成交量")),
            "amount": amount_yi * 100000000 if amount_yi is not None else None,
            "turnover_rate": None,
            "pct_chg": safe_float(item.get("涨跌幅")),
            "frequency": "daily",
            "raw_json": raw_json(source_row),
            "updated_at": now_text(),
        })
    return rows


def normalize_futures_daily(symbol, name, source, df):
    rows = []
    for _, item in df.iterrows():
        trade_date = parse_date(item.get("date"))
        if not trade_date:
            continue
        source_row = item.to_dict()
        rows.append({
            "asset_type": "future",
            "symbol": symbol,
            "trade_date": trade_date,
            "source": source,
            "name": name,
            "open": safe_float(item.get("open")),
            "high": safe_float(item.get("high")),
            "low": safe_float(item.get("low")),
            "close": safe_float(item.get("close")),
            "volume": safe_float(item.get("volume")),
            "amount": None,
            "turnover_rate": None,
            "pct_chg": None,
            "frequency": "daily",
            "raw_json": raw_json(source_row),
            "updated_at": now_text(),
        })
    return rows


def normalize_fund_nav_daily(symbol, name, source, df):
    rows = []
    for _, item in df.iterrows():
        trade_date = parse_date(item.get("净值日期"))
        if not trade_date:
            continue
        source_row = item.to_dict()
        nav = safe_float(item.get("单位净值"))
        rows.append({
            "asset_type": "fund",
            "symbol": symbol,
            "trade_date": trade_date,
            "source": source,
            "name": name,
            "open": nav,
            "high": nav,
            "low": nav,
            "close": nav,
            "volume": None,
            "amount": None,
            "turnover_rate": None,
            "pct_chg": safe_float(item.get("日增长率")),
            "frequency": "daily",
            "raw_json": raw_json(source_row),
            "updated_at": now_text(),
        })
    return rows


def fetch_index(asset_id, meta, start, end):
    ak = import_akshare()
    source = "akshare_sina_index_daily"
    if meta["method"] == "csindex":
        source = "akshare_csindex_daily"
        df = ak.stock_zh_index_hist_csindex(
            symbol=meta["ak_symbol"],
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
        )
        rows = normalize_csindex_daily(asset_id, meta["name"], source, df)
    else:
        df = ak.stock_zh_index_daily(symbol=meta["ak_symbol"])
        rows = normalize_sina_daily("index", asset_id, meta["name"], source, df)
    return source, filter_by_date(rows, start, end)


def fetch_etf(asset_id, meta, start, end):
    ak = import_akshare()
    source = "akshare_sina_etf_daily"
    df = ak.fund_etf_hist_sina(symbol=meta["ak_symbol"])
    rows = normalize_sina_daily("etf", asset_id, meta["name"], source, df)
    return source, filter_by_date(rows, start, end)


def fetch_fund(asset_id, meta, start, end):
    ak = import_akshare()
    source = "akshare_em_open_fund_nav"
    df = ak.fund_open_fund_info_em(symbol=meta["ak_symbol"], indicator="单位净值走势")
    rows = normalize_fund_nav_daily(asset_id, meta["name"], source, df)
    return source, filter_by_date(rows, start, end)


def fetch_future(asset_id, meta, start, end):
    ak = import_akshare()
    source = "akshare_sina_future_daily"
    df = ak.futures_zh_daily_sina(symbol=meta["ak_symbol"])
    rows = normalize_futures_daily(asset_id, meta["name"], source, df)
    return source, filter_by_date(rows, start, end)


def backfill_asset_group(conn, asset_type, assets, start, end):
    summary = {}
    for asset_id, meta in assets.items():
        source = f"akshare_{asset_type}_daily"
        try:
            if asset_type == "index":
                source, rows = fetch_index(asset_id, meta, start, end)
            elif asset_type == "etf":
                source, rows = fetch_etf(asset_id, meta, start, end)
            elif asset_type == "fund":
                source, rows = fetch_fund(asset_id, meta, start, end)
            elif asset_type == "future":
                source, rows = fetch_future(asset_id, meta, start, end)
            else:
                raise ValueError(f"unsupported asset_type: {asset_type}")
            upsert_instrument(conn, asset_type, asset_id, meta["name"], source, meta["ak_symbol"])
            inserted = insert_daily_rows(conn, rows)
            latest = max((row["trade_date"] for row in rows), default=None)
            complete = inserted >= 3 and latest_is_fresh(latest)
            upsert_health(
                conn,
                make_health(
                    f"{asset_type}:{asset_id}:daily",
                    meta["name"],
                    source,
                    latest,
                    "daily",
                    inserted,
                    complete,
                    f"AKShare日线入库 {inserted} 行；源代码 {meta['ak_symbol']}。",
                ),
            )
            summary[asset_id] = {"rows": inserted, "source": source, "latest": latest}
        except Exception as exc:
            upsert_health(
                conn,
                fail_health(
                    f"{asset_type}:{asset_id}:daily",
                    meta["name"],
                    source,
                    "daily",
                    exc,
                ),
            )
            summary[asset_id] = {"rows": 0, "source": source, "error": str(exc)}
        conn.commit()
    return summary


def backfill_macro_cpi(conn):
    ak = import_akshare()
    source = "akshare_macro_china_cpi"
    indicator_key = "macro:china_cpi"
    indicator_name = "中国CPI"
    try:
        df = ak.macro_china_cpi()
        rows = []
        for _, item in df.iterrows():
            obs_date = parse_date(item.get("月份"))
            if not obs_date:
                continue
            value = safe_float(item.get("全国-同比增长"))
            source_row = item.to_dict()
            conn.execute(
                """
                INSERT INTO macro_observations (
                  indicator_key, observation_date, source, indicator_name,
                  value_num, value_text, frequency, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(indicator_key, observation_date, source) DO UPDATE SET
                  indicator_name=excluded.indicator_name,
                  value_num=excluded.value_num,
                  value_text=excluded.value_text,
                  frequency=excluded.frequency,
                  raw_json=excluded.raw_json,
                  updated_at=excluded.updated_at
                """,
                (
                    indicator_key,
                    obs_date,
                    source,
                    indicator_name,
                    value,
                    str(value) if value is not None else None,
                    "monthly",
                    raw_json(source_row),
                    now_text(),
                ),
            )
            rows.append(obs_date)
        latest = max(rows) if rows else None
        complete = len(rows) > 0 and latest_is_fresh(latest, max_lag_days=120)
        upsert_health(
            conn,
            make_health(
                indicator_key,
                indicator_name,
                source,
                latest,
                "monthly",
                len(rows),
                complete,
                "AKShare宏观月频入库；当前取全国CPI同比。",
            ),
        )
        conn.commit()
        return {"rows": len(rows), "source": source, "latest": latest}
    except Exception as exc:
        upsert_health(conn, fail_health(indicator_key, indicator_name, source, "monthly", exc))
        conn.commit()
        return {"rows": 0, "source": source, "error": str(exc)}


def add_reserved_health(conn):
    rows = [
        {
            "indicator_key": "minute:two_year:reserved",
            "indicator_name": "两年分钟历史接口",
            "source": "QMT/Tushare/CSV reserved",
            "latest_date": None,
            "frequency": "1min",
            "rows_count": 0,
            "is_complete": 0,
            "status": "limited",
            "detail": "SQLite已预留 market_minute_bars；当前页面仍使用 index_minute_bars 最近5日，后续可接QMT、Tushare分钟或CSV导入。",
            "checked_at": now_text(),
        },
        {
            "indicator_key": "basic:a_share:reserved",
            "indicator_name": "A股基础资料",
            "source": "AKShare basic data reserved",
            "latest_date": None,
            "frequency": "ad hoc",
            "rows_count": 0,
            "is_complete": 0,
            "status": "limited",
            "detail": "基础资料入口预留；本机AKShare部分基础资料接口较慢，先不放入日常更新链路。",
            "checked_at": now_text(),
        },
    ]
    for row in rows:
        upsert_health(conn, row)
    conn.commit()


def load_health_rows(conn):
    rows = conn.execute(
        """
        SELECT indicator_key, indicator_name, source, latest_date, frequency,
               rows_count, is_complete, status, detail, checked_at
        FROM data_source_health
        ORDER BY indicator_key, source
        """
    ).fetchall()
    return [dict(row) for row in rows]


def write_health_cache(conn):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "0.1.0",
        "generated_at": now_text(),
        "db_path": str(DB_PATH),
        "rows": load_health_rows(conn),
    }
    HEALTH_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with HEALTH_JS.open("w", encoding="utf-8") as f:
        f.write("window.DATA_SOURCE_HEALTH = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    return payload


def pick_assets(assets, selected):
    return {key: assets[key] for key in selected if key in assets}


def preset_assets(preset):
    if preset == "probe":
        return (
            pick_assets(INDEX_ASSETS, PROBE_INDEX),
            pick_assets(ETF_ASSETS, PROBE_ETF),
            pick_assets(FUND_ASSETS, PROBE_FUND),
            pick_assets(FUTURE_ASSETS, PROBE_FUTURE),
            True,
        )
    return INDEX_ASSETS, ETF_ASSETS, FUND_ASSETS, FUTURE_ASSETS, True


def main():
    parser = argparse.ArgumentParser(description="AKShare daily data adapter for the research SQLite warehouse.")
    parser.add_argument("command", choices=["backfill", "health-cache"], nargs="?", default="backfill")
    parser.add_argument("--preset", choices=["probe", "core"], default="probe")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--start", help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", help="End date, YYYY-MM-DD.")
    parser.add_argument("--skip-macro", action="store_true")
    args = parser.parse_args()

    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=args.days)).strftime("%Y-%m-%d")

    with connect() as conn:
        init_db(conn)
        if args.command == "health-cache":
            payload = write_health_cache(conn)
            print(json.dumps({"health_rows": len(payload["rows"]), "json": str(HEALTH_JSON)}, ensure_ascii=False, indent=2))
            return

        index_assets, etf_assets, fund_assets, future_assets, include_macro = preset_assets(args.preset)
        result = {
            "db": str(DB_PATH),
            "source": AK_SOURCE,
            "range": {"start": start, "end": end},
            "preset": args.preset,
            "index": backfill_asset_group(conn, "index", index_assets, start, end),
            "etf": backfill_asset_group(conn, "etf", etf_assets, start, end),
            "fund": backfill_asset_group(conn, "fund", fund_assets, start, end),
            "future": backfill_asset_group(conn, "future", future_assets, start, end),
        }
        if include_macro and not args.skip_macro:
            result["macro"] = {"china_cpi": backfill_macro_cpi(conn)}
        add_reserved_health(conn)
        payload = write_health_cache(conn)
        result["health_rows"] = len(payload["rows"])
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
