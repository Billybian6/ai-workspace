import argparse
import csv
import json
import math
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from research_db import DB_PATH, connect, init_db, now_text


SYSTEM_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = SYSTEM_ROOT / "data" / "cache"

INDEX_UNIVERSE = {
    "000016": {"name": "上证50", "secid": "1.000016", "choice": "000016.SH"},
    "000300": {"name": "沪深300", "secid": "1.000300", "choice": "000300.SH"},
    "000905": {"name": "中证500", "secid": "1.000905", "choice": "000905.SH"},
    "000852": {"name": "中证1000", "secid": "1.000852", "choice": "000852.SH"},
    "000464": {"name": "中证2000", "secid": "2.932000", "choice": "931446.CSI"},
    "399006": {"name": "创业板指", "secid": "0.399006", "choice": "399006.SZ"},
    "000688": {"name": "科创50", "secid": "1.000688", "choice": "000688.SH"},
    "8841331": {"name": "中证红利", "secid": "1.000922", "choice": "000922.SH"},
    "800007": {"name": "Choice微盘", "secid": "47.800007", "choice": "800007"},
}

TRENDS_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    "?secid={secid}"
    "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
    "&ndays={ndays}&iscr=0&iscca=0"
)


def to_float(value):
    try:
        if value is None:
            return None
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def get_json(url, timeout=10, retries=2):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/",
    }
    last_error = None
    for attempt in range(retries):
        try:
            import requests

            session = requests.Session()
            session.trust_env = False
            response = session.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
        time.sleep(0.8 + attempt)
    raise last_error


def parse_eastmoney_line(line, index_id, meta):
    parts = line.split(",")
    if len(parts) < 4:
        return None
    dt = parts[0]
    close = to_float(parts[2]) or to_float(parts[1])
    if not close or close <= 0:
        return None
    return {
        "index_id": index_id,
        "index_name": meta["name"],
        "trade_datetime": dt,
        "trade_date": dt[:10],
        "trade_time": dt[11:16],
        "open": to_float(parts[1]),
        "close": close,
        "high": to_float(parts[3]) if len(parts) > 3 else close,
        "low": to_float(parts[4]) if len(parts) > 4 else close,
        "volume": to_float(parts[5]) if len(parts) > 5 else None,
        "amount_yuan": to_float(parts[6]) if len(parts) > 6 else None,
        "price_unit": "index_level",
        "return_from_start": None,
        "source": "eastmoney_trends2",
        "quality": "latest5",
    }


def fetch_eastmoney_index(index_id, ndays=5):
    meta = INDEX_UNIVERSE[index_id]
    url = TRENDS_URL.format(secid=meta["secid"], ndays=min(max(ndays, 1), 5))
    payload = get_json(url)
    rows = payload.get("data", {}).get("trends", []) or []
    result = [parse_eastmoney_line(line, index_id, meta) for line in rows]
    return [row for row in result if row]


def emquant_path():
    candidates = [
        Path(r"C:\Users\bianzhengzhi\Desktop\EMQuantAPI_Python\EMQuantAPI_Python\python3"),
        Path(r"C:\Users\bianzhengzhi\Desktop\EMQuantAPI_Python\python3"),
    ]
    for path in candidates:
        if path.exists() and str(path) not in sys.path:
            sys.path.append(str(path))


def normalize_choice_date(value):
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) == 14 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}:{text[12:14]}"
    return text


def fetch_choice_index(index_id, start, end):
    emquant_path()
    from EmQuantAPI import c

    meta = INDEX_UNIVERSE[index_id]
    last_error = None
    res = None
    for fn_name in ("cmc", "chmc"):
        fn = getattr(c, fn_name)
        res = fn(
            meta["choice"],
            "OPEN,HIGH,LOW,CLOSE,VOLUME,AMOUNT",
            start,
            end,
            "Period=1,RowIndex=2,Ispandas=0",
        )
        if getattr(res, "ErrorCode", -1) == 0:
            break
        last_error = f"{fn_name}: {getattr(res, 'ErrorMsg', '')}"
    if getattr(res, "ErrorCode", -1) != 0:
        raise RuntimeError(f"Choice minute failed for {meta['choice']}: {last_error}")
    dates = [normalize_choice_date(item) for item in (getattr(res, "Dates", []) or [])]
    indicators = [str(item).upper() for item in (getattr(res, "Indicators", []) or [])]
    data = getattr(res, "Data", []) or []
    by_indicator = {ind: data[pos] for pos, ind in enumerate(indicators) if pos < len(data)}
    result = []
    for pos, dt in enumerate(dates):
        if not dt or len(dt) < 10:
            continue
        close = to_float((by_indicator.get("CLOSE") or [None])[pos])
        if not close or close <= 0:
            continue
        result.append({
            "index_id": index_id,
            "index_name": meta["name"],
            "trade_datetime": dt[:16],
            "trade_date": dt[:10],
            "trade_time": dt[11:16] if len(dt) >= 16 else None,
            "open": to_float((by_indicator.get("OPEN") or [None])[pos]),
            "high": to_float((by_indicator.get("HIGH") or [None])[pos]),
            "low": to_float((by_indicator.get("LOW") or [None])[pos]),
            "close": close,
            "volume": to_float((by_indicator.get("VOLUME") or [None])[pos]),
            "amount_yuan": to_float((by_indicator.get("AMOUNT") or [None])[pos]),
            "price_unit": "index_level",
            "return_from_start": None,
            "source": "choice_cmc",
            "quality": "historical",
        })
    return result


def fetch_strategy_cache_index(index_id):
    path = CACHE_DIR / "strategy_dashboard.json"
    if not path.exists():
        raise FileNotFoundError(f"strategy cache not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    by_id = {row.get("id"): row for row in data.get("index_trends", [])}
    row = by_id.get(index_id)
    if not row:
        raise KeyError(f"strategy cache has no index {index_id}")
    result = []
    for point in row.get("points") or []:
        ret = to_float(point.get("return"))
        if ret is None:
            continue
        close = 100 * (1 + ret / 100)
        result.append({
            "index_id": index_id,
            "index_name": row.get("name"),
            "trade_datetime": point.get("datetime"),
            "trade_date": point.get("date"),
            "trade_time": point.get("time"),
            "open": None,
            "high": None,
            "low": None,
            "close": close,
            "volume": None,
            "amount_yuan": None,
            "price_unit": "normalized_100",
            "return_from_start": ret,
            "source": "strategy_cache_points",
            "quality": f"{row.get('quality') or 'cache'}_normalized",
        })
    return result


def insert_rows(conn, rows):
    updated_at = now_text()
    conn.executemany(
        """
        INSERT INTO index_minute_bars (
          index_id, index_name, trade_datetime, trade_date, trade_time,
          open, high, low, close, volume, amount_yuan, price_unit,
          return_from_start, source, quality, updated_at
        )
        VALUES (
          :index_id, :index_name, :trade_datetime, :trade_date, :trade_time,
          :open, :high, :low, :close, :volume, :amount_yuan, :price_unit,
          :return_from_start, :source, :quality, :updated_at
        )
        ON CONFLICT(index_id, trade_datetime) DO UPDATE SET
          index_name=excluded.index_name,
          trade_date=excluded.trade_date,
          trade_time=excluded.trade_time,
          open=excluded.open,
          high=excluded.high,
          low=excluded.low,
          close=excluded.close,
          volume=excluded.volume,
          amount_yuan=excluded.amount_yuan,
          price_unit=excluded.price_unit,
          return_from_start=excluded.return_from_start,
          source=excluded.source,
          quality=excluded.quality,
          updated_at=excluded.updated_at
        """,
        [dict(row, updated_at=updated_at) for row in rows],
    )
    conn.executemany(
        """
        INSERT INTO market_minute_bars (
          asset_type, symbol, trade_datetime, source, name,
          trade_date, trade_time, open, high, low, close,
          volume, amount, frequency, raw_json, updated_at
        )
        VALUES (
          'index', :index_id, :trade_datetime, :source, :index_name,
          :trade_date, :trade_time, :open, :high, :low, :close,
          :volume, :amount_yuan, '1min', :raw_json, :updated_at
        )
        ON CONFLICT(asset_type, symbol, trade_datetime, source) DO UPDATE SET
          name=excluded.name,
          trade_date=excluded.trade_date,
          trade_time=excluded.trade_time,
          open=excluded.open,
          high=excluded.high,
          low=excluded.low,
          close=excluded.close,
          volume=excluded.volume,
          amount=excluded.amount,
          frequency=excluded.frequency,
          raw_json=excluded.raw_json,
          updated_at=excluded.updated_at
        """,
        [
            dict(
                row,
                raw_json=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                updated_at=updated_at,
            )
            for row in rows
        ],
    )
    conn.commit()
    update_minute_health(conn)
    return len(rows)


def update_minute_health(conn):
    summary = conn.execute(
        """
        SELECT MIN(trade_date) AS min_date, MAX(trade_date) AS max_date,
               COUNT(*) AS rows, COUNT(DISTINCT trade_date) AS days,
               COUNT(DISTINCT symbol) AS indices
        FROM market_minute_bars
        WHERE asset_type = 'index'
        """
    ).fetchone()
    rows = summary["rows"] if summary else 0
    days = summary["days"] if summary else 0
    indices = summary["indices"] if summary else 0
    complete = rows > 0 and days >= 5 and indices >= 8
    conn.execute(
        """
        INSERT INTO data_source_health (
          indicator_key, source, indicator_name, latest_date, frequency,
          rows_count, is_complete, status, detail, checked_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        (
            "minute:index:latest5",
            "eastmoney_trends2/strategy_cache",
            "最近5日宽基分时",
            summary["max_date"] if summary else None,
            "1min/latest5",
            rows,
            1 if complete else 0,
            "ok" if complete else "partial",
            f"覆盖 {indices} 个指数、{days} 个交易日，日期 {summary['min_date']} 至 {summary['max_date']}；两年历史仍走预留表和后续数据源。",
            now_text(),
        ),
    )
    conn.commit()


def expand_indices(value):
    if value.lower() == "all":
        return list(INDEX_UNIVERSE.keys())
    result = []
    for item in value.split(","):
        code = item.strip()
        if not code:
            continue
        if code not in INDEX_UNIVERSE:
            raise ValueError(f"unknown index code: {code}")
        result.append(code)
    return result


def backfill_eastmoney(conn, indices, ndays, max_workers=4):
    inserted = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_eastmoney_index, code, ndays): code for code in indices}
        for future in as_completed(futures):
            code = futures[future]
            try:
                rows = future.result()
                inserted[code] = insert_rows(conn, rows)
            except Exception as exc:
                inserted[code] = f"failed: {exc}"
    return inserted


def backfill_choice(conn, indices, start, end):
    emquant_path()
    from EmQuantAPI import c

    login = c.start()
    if getattr(login, "ErrorCode", -1) != 0:
        raise RuntimeError(f"Choice login failed: {getattr(login, 'ErrorMsg', '')}")
    inserted = {}
    try:
        for code in indices:
            try:
                rows = fetch_choice_index(code, start, end)
                inserted[code] = insert_rows(conn, rows)
            except Exception as exc:
                inserted[code] = f"failed: {exc}"
    finally:
        try:
            c.stop()
        except Exception:
            pass
    return inserted


def backfill_cache(conn, indices):
    inserted = {}
    for code in indices:
        try:
            rows = fetch_strategy_cache_index(code)
            inserted[code] = insert_rows(conn, rows)
        except Exception as exc:
            inserted[code] = f"failed: {exc}"
    return inserted


def pick(row, *names):
    lower = {str(k).lower().strip(): v for k, v in row.items()}
    for name in names:
        key = name.lower()
        if key in lower and lower[key] not in ("", None):
            return lower[key]
    return None


def normalize_datetime_from_csv(row):
    dt = pick(row, "trade_datetime", "datetime", "date_time", "time", "timestamp", "日期时间")
    if dt:
        text = str(dt).strip().replace("/", "-")
        if len(text) >= 16:
            return text[:16]
        return text
    date = pick(row, "trade_date", "date", "日期")
    minute = pick(row, "trade_time", "minute", "time", "时间")
    if date and minute:
        return f"{str(date).strip().replace('/', '-')} {str(minute).strip()[:5]}"
    return None


def import_csv_rows(path, index_id, index_name=None, source="csv_import"):
    meta = INDEX_UNIVERSE.get(index_id, {})
    rows = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            dt = normalize_datetime_from_csv(raw)
            if not dt or len(dt) < 10:
                continue
            close = to_float(pick(raw, "close", "收盘", "收盘价", "price", "最新价"))
            if close is None:
                continue
            ret = to_float(pick(raw, "return_from_start", "return_pct", "涨跌幅", "收益率"))
            rows.append({
                "index_id": index_id,
                "index_name": index_name or meta.get("name") or index_id,
                "trade_datetime": dt,
                "trade_date": dt[:10],
                "trade_time": dt[11:16] if len(dt) >= 16 else None,
                "open": to_float(pick(raw, "open", "开盘", "开盘价")),
                "high": to_float(pick(raw, "high", "最高", "最高价")),
                "low": to_float(pick(raw, "low", "最低", "最低价")),
                "close": close,
                "volume": to_float(pick(raw, "volume", "vol", "成交量")),
                "amount_yuan": to_float(pick(raw, "amount_yuan", "amount", "成交额")),
                "price_unit": pick(raw, "price_unit") or "index_level",
                "return_from_start": ret,
                "source": source,
                "quality": "csv",
            })
    return rows


def minute_summary(conn):
    total = conn.execute(
        """
        SELECT MIN(trade_date) AS min_date, MAX(trade_date) AS max_date,
               COUNT(*) AS rows, COUNT(DISTINCT trade_date) AS days,
               COUNT(DISTINCT index_id) AS indices
        FROM index_minute_bars
        """
    ).fetchone()
    print("Minute warehouse:")
    print(dict(total))
    for row in conn.execute(
        """
        SELECT index_id, index_name, MIN(trade_date) AS min_date,
               MAX(trade_date) AS max_date, COUNT(*) AS rows,
               COUNT(DISTINCT trade_date) AS days,
               MAX(source) AS source,
               MAX(price_unit) AS price_unit
        FROM index_minute_bars
        GROUP BY index_id, index_name
        ORDER BY index_id
        """
    ):
        print(dict(row))


def export_series(conn, index_id, start, end, output_path):
    rows = conn.execute(
        """
        SELECT index_id, index_name, trade_datetime, trade_date, trade_time,
               open, high, low, close, volume, amount_yuan, price_unit,
               return_from_start, source, quality
        FROM index_minute_bars
        WHERE index_id = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_datetime
        """,
        (index_id, start, end),
    ).fetchall()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        f.write("index_id,index_name,trade_datetime,trade_date,trade_time,open,high,low,close,volume,amount_yuan,price_unit,source,quality,return_from_start\n")
        start_close = None
        for row in rows:
            close = row["close"]
            if start_close is None and close:
                start_close = close
            ret = row["return_from_start"]
            if ret is None:
                ret = (close / start_close - 1) * 100 if close and start_close else ""
            f.write(",".join([
                str(row["index_id"]),
                str(row["index_name"]),
                str(row["trade_datetime"]),
                str(row["trade_date"]),
                str(row["trade_time"]),
                str(row["open"] or ""),
                str(row["high"] or ""),
                str(row["low"] or ""),
                str(row["close"] or ""),
                str(row["volume"] or ""),
                str(row["amount_yuan"] or ""),
                str(row["price_unit"] or ""),
                str(row["source"] or ""),
                str(row["quality"] or ""),
                "" if ret == "" else f"{ret:.6f}",
            ]) + "\n")
    return output_path, len(rows)


def main():
    parser = argparse.ArgumentParser(description="Backfill raw broad-index minute bars into SQLite.")
    parser.add_argument("--source", choices=["eastmoney", "choice", "cache"], default="eastmoney")
    parser.add_argument("--index", default="all", help="Index id list, e.g. 000300,000905, or all.")
    parser.add_argument("--ndays", type=int, default=5, help="Eastmoney latest days, max 5.")
    parser.add_argument("--start", help="Start date for Choice/export, YYYY-MM-DD.")
    parser.add_argument("--end", help="End date for Choice/export, YYYY-MM-DD.")
    parser.add_argument("--summary", action="store_true", help="Print current minute warehouse summary.")
    parser.add_argument("--export", help="Export one index/date range to CSV.")
    parser.add_argument("--import-csv", help="Import one CSV file into index_minute_bars.")
    parser.add_argument("--index-name", help="Index display name for CSV import.")
    args = parser.parse_args()

    with connect() as conn:
        init_db(conn)
        if args.summary:
            minute_summary(conn)
            return
        if args.import_csv:
            if args.index.lower() == "all" or "," in args.index:
                raise ValueError("--import-csv requires one --index code.")
            rows = import_csv_rows(args.import_csv, args.index, args.index_name)
            inserted = insert_rows(conn, rows)
            print(json.dumps({"import_csv": args.import_csv, "index": args.index, "rows": inserted}, ensure_ascii=False, indent=2))
            minute_summary(conn)
            return
        if args.export:
            if args.index.lower() == "all" or "," in args.index:
                raise ValueError("--export requires one --index code.")
            if not args.start or not args.end:
                raise ValueError("--export requires --start and --end.")
            path, rows = export_series(conn, args.index, args.start, args.end, args.export)
            print(json.dumps({"export": str(path), "rows": rows}, ensure_ascii=False, indent=2))
            return

        indices = expand_indices(args.index)
        if args.source == "eastmoney":
            result = backfill_eastmoney(conn, indices, args.ndays)
        elif args.source == "choice":
            if not args.start or not args.end:
                raise ValueError("Choice backfill requires --start and --end.")
            result = backfill_choice(conn, indices, args.start, args.end)
        else:
            result = backfill_cache(conn, indices)

        print(json.dumps({
            "db": str(DB_PATH),
            "source": args.source,
            "result": result,
        }, ensure_ascii=False, indent=2))
        minute_summary(conn)


if __name__ == "__main__":
    main()
