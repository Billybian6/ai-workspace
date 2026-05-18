import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from research_db import DB_PATH, SYSTEM_ROOT
from source_health import load_source_health, summarize_source_health


CACHE_DIR = SYSTEM_ROOT / "data" / "cache"
OUTPUT_JSON = CACHE_DIR / "intraday_research.json"
OUTPUT_JS = CACHE_DIR / "intraday_research_data.js"


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_metadata(conn):
    rows = conn.execute(
        """
        SELECT
          index_id,
          index_name,
          MIN(trade_date) AS min_date,
          MAX(trade_date) AS max_date,
          COUNT(*) AS rows,
          COUNT(DISTINCT trade_date) AS days,
          MAX(price_unit) AS price_unit,
          MAX(source) AS source,
          MAX(quality) AS quality
        FROM index_minute_bars
        GROUP BY index_id, index_name
        ORDER BY index_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def load_points(conn, start=None, end=None, index_id=None, max_rows=250000):
    where = []
    params = []
    if start:
        where.append("trade_date >= ?")
        params.append(start)
    if end:
        where.append("trade_date <= ?")
        params.append(end)
    if index_id and index_id.lower() != "all":
        where.append("index_id = ?")
        params.append(index_id)
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"""
        SELECT
          index_id, index_name, trade_datetime, trade_date, trade_time,
          close, return_from_start, price_unit, source, quality
        FROM index_minute_bars
        {sql_where}
        ORDER BY index_id, trade_datetime
        LIMIT ?
        """,
        params + [max_rows],
    ).fetchall()
    by_index = {}
    for row in rows:
        code = row["index_id"]
        by_index.setdefault(code, {
            "id": code,
            "name": row["index_name"],
            "points": [],
        })
        by_index[code]["points"].append({
            "dt": row["trade_datetime"],
            "d": row["trade_date"],
            "t": row["trade_time"],
            "c": safe_float(row["close"]),
            "r": safe_float(row["return_from_start"]),
            "unit": row["price_unit"],
            "source": row["source"],
            "quality": row["quality"],
        })
    total = conn.execute(f"SELECT COUNT(*) AS n FROM index_minute_bars {sql_where}", params).fetchone()["n"]
    return by_index, total, len(rows)


def build_payload(start=None, end=None, index_id=None, max_rows=250000):
    with connect() as conn:
        metadata = load_metadata(conn)
        by_index, total_rows, loaded_rows = load_points(conn, start, end, index_id, max_rows)
    all_dates = sorted({
        item["d"]
        for row in by_index.values()
        for item in row["points"]
        if item.get("d")
    })
    data_health = load_source_health()
    payload = {
        "version": "0.1.0",
        "generated_at": now_text(),
        "db_path": str(DB_PATH),
        "filters": {
            "start": start,
            "end": end,
            "index_id": index_id or "all",
            "max_rows": max_rows,
        },
        "coverage": {
            "min_date": all_dates[0] if all_dates else None,
            "max_date": all_dates[-1] if all_dates else None,
            "dates": all_dates,
            "indices": len(by_index),
            "total_rows": total_rows,
            "loaded_rows": loaded_rows,
            "truncated": loaded_rows < total_rows,
        },
        "indices": metadata,
        "series": by_index,
        "data_health": data_health,
        "data_health_summary": summarize_source_health(data_health),
        "notes": [
            "当前页面读取 SQLite 的 index_minute_bars 表。",
            "price_unit=normalized_100 表示归一化收益线，适合趋势效率和噪声研究；原始 OHLC 需后续由 Choice/CSV 补齐。",
            "若未来分钟数据超过页面可承载规模，可用生成器的 --start/--end 限定研究区间。",
        ],
    }
    return payload


def write_payload(payload):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with OUTPUT_JS.open("w", encoding="utf-8") as f:
        f.write("window.INTRADAY_RESEARCH_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    return OUTPUT_JSON, OUTPUT_JS


def main():
    parser = argparse.ArgumentParser(description="Build static data for the intraday research page.")
    parser.add_argument("--start", help="Start trade date, YYYY-MM-DD.")
    parser.add_argument("--end", help="End trade date, YYYY-MM-DD.")
    parser.add_argument("--index", default="all", help="One index id or all.")
    parser.add_argument("--max-rows", type=int, default=250000)
    args = parser.parse_args()
    payload = build_payload(args.start, args.end, args.index, args.max_rows)
    output_json, output_js = write_payload(payload)
    print(json.dumps({
        "generated_at": payload["generated_at"],
        "coverage": payload["coverage"],
        "json": str(output_json.relative_to(SYSTEM_ROOT)).replace("\\", "/"),
        "js": str(output_js.relative_to(SYSTEM_ROOT)).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
