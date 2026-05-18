import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path


SYSTEM_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = SYSTEM_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
HISTORY_DIR = DATA_DIR / "history"
DB_PATH = DATA_DIR / "research_warehouse.sqlite"


NO_STOCK_DETAIL_KEYS = {"leader", "leader_change"}


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def rel_path(path):
    return str(Path(path).resolve().relative_to(SYSTEM_ROOT)).replace("\\", "/")


def safe_float(value):
    try:
        if value is None:
            return None
        number = float(value)
        if number != number:
            return None
        return number
    except (TypeError, ValueError):
        return None


def clean_text(value):
    if not isinstance(value, str):
        return value
    return re.sub(r"，?领涨股[^。；，,\n]*", "", value)


def sanitize(value):
    if isinstance(value, dict):
        return {
            key: sanitize(item)
            for key, item in value.items()
            if key not in NO_STOCK_DETAIL_KEYS
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return clean_text(value)


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def value_text(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def level_label(row):
    level = row.get("level") or row.get("status") or {}
    if isinstance(level, dict):
        return level.get("label"), level.get("class")
    return level, None


def detect_dashboard_type(data, path):
    name = path.name.lower()
    if "congestion_data" in name:
        return "congestion"
    if "strategy_scores" in data or "strategy_dashboard" in name:
        return "strategy"
    if "market_overview" in data or "morning_dashboard" in name:
        return "morning"
    if "market_state" in data or "market_dashboard" in name:
        return "daily"
    return "unknown"


def connect(db_path=DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_column(conn, table, column, definition):
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          dashboard_type TEXT NOT NULL,
          trade_date TEXT,
          generated_at TEXT,
          version TEXT,
          source_path TEXT,
          content_hash TEXT NOT NULL UNIQUE,
          headline TEXT,
          summary_json TEXT,
          raw_json TEXT,
          ingested_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_runs_type_date
          ON runs(dashboard_type, trade_date, generated_at);

        CREATE TABLE IF NOT EXISTS metrics (
          run_id INTEGER NOT NULL,
          namespace TEXT NOT NULL,
          metric_key TEXT NOT NULL,
          value_num REAL,
          value_text TEXT,
          PRIMARY KEY(run_id, namespace, metric_key),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS index_snapshots (
          run_id INTEGER NOT NULL,
          snapshot_kind TEXT NOT NULL,
          index_id TEXT NOT NULL,
          index_name TEXT,
          group_name TEXT,
          style TEXT,
          date TEXT,
          score REAL,
          level_label TEXT,
          level_class TEXT,
          day_delta REAL,
          range_delta REAL,
          recent_delta REAL,
          congestion REAL,
          turnover_ratio REAL,
          turnover_rate REAL,
          percentile REAL,
          turnover REAL,
          morning_amount REAL,
          amount_progress REAL,
          projected_vs_last REAL,
          pct_chg REAL,
          net_inflow REAL,
          quality TEXT,
          PRIMARY KEY(run_id, snapshot_kind, index_id),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS strategy_scores (
          run_id INTEGER NOT NULL,
          strategy_id TEXT NOT NULL,
          name TEXT,
          score REAL,
          level_label TEXT,
          level_class TEXT,
          comment TEXT,
          PRIMARY KEY(run_id, strategy_id),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS strategy_trend_metrics (
          run_id INTEGER NOT NULL,
          index_id TEXT NOT NULL,
          index_name TEXT,
          style TEXT,
          quality TEXT,
          total_return REAL,
          path_return REAL,
          trend_efficiency REAL,
          minute_consistency REAL,
          day_consistency REAL,
          cta_score REAL,
          label TEXT,
          PRIMARY KEY(run_id, index_id),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS index_intraday_points (
          run_id INTEGER NOT NULL,
          index_id TEXT NOT NULL,
          index_name TEXT,
          point_datetime TEXT NOT NULL,
          point_date TEXT,
          point_time TEXT,
          return_pct REAL,
          quality TEXT,
          PRIMARY KEY(run_id, index_id, point_datetime),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS index_minute_bars (
          index_id TEXT NOT NULL,
          index_name TEXT,
          trade_datetime TEXT NOT NULL,
          trade_date TEXT,
          trade_time TEXT,
          open REAL,
          high REAL,
          low REAL,
          close REAL,
          volume REAL,
          amount_yuan REAL,
          price_unit TEXT,
          return_from_start REAL,
          source TEXT,
          quality TEXT,
          updated_at TEXT,
          PRIMARY KEY(index_id, trade_datetime)
        );

        CREATE INDEX IF NOT EXISTS idx_index_minute_bars_date
          ON index_minute_bars(index_id, trade_date, trade_time);

        CREATE TABLE IF NOT EXISTS quant_rows (
          run_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          style TEXT,
          benchmark TEXT,
          return_5d REAL,
          excess_vs_core REAL,
          score REAL,
          level_label TEXT,
          level_class TEXT,
          comment TEXT,
          PRIMARY KEY(run_id, name),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS macro_rows (
          run_id INTEGER NOT NULL,
          asset TEXT NOT NULL,
          proxy TEXT,
          horizon TEXT,
          change_pct REAL,
          signal TEXT,
          PRIMARY KEY(run_id, asset),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS style_rotation (
          run_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          members TEXT,
          score REAL,
          level_label TEXT,
          level_class TEXT,
          day_delta REAL,
          range_delta REAL,
          pct_chg REAL,
          amount_progress REAL,
          PRIMARY KEY(run_id, name),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS style_spreads (
          run_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          value REAL,
          abs_value REAL,
          direction TEXT,
          PRIMARY KEY(run_id, name),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS industries (
          run_id INTEGER NOT NULL,
          source_key TEXT NOT NULL,
          industry_id TEXT,
          name TEXT NOT NULL,
          score REAL,
          ret_5d REAL,
          ret_20d REAL,
          amount REAL,
          amount_pct REAL,
          net_inflow REAL,
          net_inflow_pct REAL,
          state TEXT,
          quality TEXT,
          PRIMARY KEY(run_id, source_key, name),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS etf_tracking (
          run_id INTEGER NOT NULL,
          source_key TEXT NOT NULL,
          etf_id TEXT NOT NULL,
          name TEXT,
          bucket TEXT,
          pct_chg REAL,
          amount REAL,
          net_inflow REAL,
          PRIMARY KEY(run_id, source_key, etf_id),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS signals (
          run_id INTEGER NOT NULL,
          source_key TEXT NOT NULL,
          row_no INTEGER NOT NULL,
          type TEXT,
          level TEXT,
          target TEXT,
          message TEXT,
          PRIMARY KEY(run_id, source_key, row_no),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS data_quality (
          run_id INTEGER NOT NULL,
          item TEXT NOT NULL,
          status TEXT,
          detail TEXT,
          PRIMARY KEY(run_id, item),
          FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS instruments (
          instrument_id TEXT PRIMARY KEY,
          symbol TEXT NOT NULL,
          name TEXT,
          asset_type TEXT NOT NULL,
          exchange TEXT,
          source TEXT,
          source_symbol TEXT,
          updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_instruments_type_symbol
          ON instruments(asset_type, symbol);

        CREATE TABLE IF NOT EXISTS market_daily_bars (
          asset_type TEXT NOT NULL,
          symbol TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          source TEXT NOT NULL,
          name TEXT,
          open REAL,
          high REAL,
          low REAL,
          close REAL,
          volume REAL,
          amount REAL,
          turnover_rate REAL,
          pct_chg REAL,
          frequency TEXT DEFAULT 'daily',
          raw_json TEXT,
          updated_at TEXT,
          PRIMARY KEY(asset_type, symbol, trade_date, source)
        );

        CREATE INDEX IF NOT EXISTS idx_market_daily_bars_symbol_date
          ON market_daily_bars(asset_type, symbol, trade_date);

        CREATE TABLE IF NOT EXISTS market_minute_bars (
          asset_type TEXT NOT NULL,
          symbol TEXT NOT NULL,
          trade_datetime TEXT NOT NULL,
          source TEXT NOT NULL,
          name TEXT,
          trade_date TEXT,
          trade_time TEXT,
          open REAL,
          high REAL,
          low REAL,
          close REAL,
          volume REAL,
          amount REAL,
          frequency TEXT DEFAULT '1min',
          raw_json TEXT,
          updated_at TEXT,
          PRIMARY KEY(asset_type, symbol, trade_datetime, source)
        );

        CREATE INDEX IF NOT EXISTS idx_market_minute_bars_symbol_date
          ON market_minute_bars(asset_type, symbol, trade_date, trade_time);

        CREATE TABLE IF NOT EXISTS macro_observations (
          indicator_key TEXT NOT NULL,
          observation_date TEXT NOT NULL,
          source TEXT NOT NULL,
          indicator_name TEXT,
          value_num REAL,
          value_text TEXT,
          frequency TEXT,
          raw_json TEXT,
          updated_at TEXT,
          PRIMARY KEY(indicator_key, observation_date, source)
        );

        CREATE TABLE IF NOT EXISTS data_source_health (
          indicator_key TEXT NOT NULL,
          source TEXT NOT NULL,
          indicator_name TEXT,
          latest_date TEXT,
          frequency TEXT,
          rows_count INTEGER,
          is_complete INTEGER,
          status TEXT,
          detail TEXT,
          checked_at TEXT,
          PRIMARY KEY(indicator_key, source)
        );

        CREATE TABLE IF NOT EXISTS market_total_daily (
          trade_date TEXT PRIMARY KEY,
          turnover REAL,
          free_float_mv REAL,
          source_path TEXT,
          updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS wide_index_base_daily (
          trade_date TEXT NOT NULL,
          index_id TEXT NOT NULL,
          turnover REAL,
          free_mv REAL,
          percentile REAL,
          source_path TEXT,
          updated_at TEXT,
          PRIMARY KEY(trade_date, index_id)
        );

        CREATE TABLE IF NOT EXISTS market_concentration_daily (
          trade_date TEXT PRIMARY KEY,
          top5_ratio REAL,
          source_path TEXT,
          updated_at TEXT
        );
        """
    )
    ensure_column(conn, "index_minute_bars", "price_unit", "TEXT")
    ensure_column(conn, "index_minute_bars", "return_from_start", "REAL")
    conn.commit()


def clear_child_rows(conn, run_id):
    tables = [
        "metrics",
        "index_snapshots",
        "strategy_scores",
        "strategy_trend_metrics",
        "index_intraday_points",
        "quant_rows",
        "macro_rows",
        "style_rotation",
        "style_spreads",
        "industries",
        "etf_tracking",
        "signals",
        "data_quality",
    ]
    for table in tables:
        conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))


def upsert_run(conn, data, dashboard_type, path, content_hash):
    summary = data.get("summary") or {}
    headline = clean_text(summary.get("headline"))
    existing = conn.execute(
        "SELECT id FROM runs WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    payload = (
        dashboard_type,
        data.get("trade_date"),
        data.get("generated_at"),
        data.get("version"),
        rel_path(path),
        content_hash,
        headline,
        json.dumps(summary, ensure_ascii=False),
        json.dumps(data, ensure_ascii=False),
        now_text(),
    )
    if existing:
        run_id = existing["id"]
        conn.execute(
            """
            UPDATE runs
            SET dashboard_type=?, trade_date=?, generated_at=?, version=?,
                source_path=?, content_hash=?, headline=?, summary_json=?,
                raw_json=?, ingested_at=?
            WHERE id=?
            """,
            payload + (run_id,),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO runs (
              dashboard_type, trade_date, generated_at, version, source_path,
              content_hash, headline, summary_json, raw_json, ingested_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        run_id = cur.lastrowid
    clear_child_rows(conn, run_id)
    return run_id


def insert_metrics(conn, run_id, namespace, values):
    if not isinstance(values, dict):
        return
    for key, value in values.items():
        conn.execute(
            """
            INSERT OR REPLACE INTO metrics
              (run_id, namespace, metric_key, value_num, value_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, namespace, key, safe_float(value), value_text(value)),
        )


def insert_index_snapshots(conn, run_id, rows, snapshot_kind):
    for row in rows or []:
        label, klass = level_label(row)
        conn.execute(
            """
            INSERT OR REPLACE INTO index_snapshots (
              run_id, snapshot_kind, index_id, index_name, group_name, style,
              date, score, level_label, level_class, day_delta, range_delta,
              recent_delta, congestion, turnover_ratio, turnover_rate,
              percentile, turnover, morning_amount, amount_progress,
              projected_vs_last, pct_chg, net_inflow, quality
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_kind,
                str(row.get("id") or row.get("name")),
                row.get("name"),
                row.get("group"),
                row.get("style"),
                row.get("date"),
                safe_float(row.get("score")),
                label,
                klass,
                safe_float(row.get("day_delta")),
                safe_float(row.get("range_delta")),
                safe_float(row.get("recent_delta")),
                safe_float(row.get("congestion")),
                safe_float(row.get("turnover_ratio")),
                safe_float(row.get("turnover_rate")),
                safe_float(row.get("percentile")),
                safe_float(row.get("turnover")),
                safe_float(row.get("morning_amount")),
                safe_float(row.get("amount_progress")),
                safe_float(row.get("projected_vs_last")),
                safe_float(row.get("pct_chg")),
                safe_float(row.get("net_inflow")),
                row.get("quality"),
            ),
        )


def insert_strategy(conn, run_id, data):
    for key, row in (data.get("strategy_scores") or {}).items():
        label, klass = level_label(row)
        conn.execute(
            """
            INSERT OR REPLACE INTO strategy_scores
              (run_id, strategy_id, name, score, level_label, level_class, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, key, row.get("name"), safe_float(row.get("score")), label, klass, row.get("comment")),
        )

    for row in data.get("index_trends") or []:
        metrics = row.get("metrics") or {}
        conn.execute(
            """
            INSERT OR REPLACE INTO strategy_trend_metrics (
              run_id, index_id, index_name, style, quality, total_return,
              path_return, trend_efficiency, minute_consistency,
              day_consistency, cta_score, label
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                str(row.get("id")),
                row.get("name"),
                row.get("style"),
                row.get("quality"),
                safe_float(metrics.get("total_return")),
                safe_float(metrics.get("path_return")),
                safe_float(metrics.get("trend_efficiency")),
                safe_float(metrics.get("minute_consistency")),
                safe_float(metrics.get("day_consistency")),
                safe_float(metrics.get("cta_score")),
                metrics.get("label"),
            ),
        )
        for point in row.get("points") or []:
            conn.execute(
                """
                INSERT OR REPLACE INTO index_intraday_points (
                  run_id, index_id, index_name, point_datetime,
                  point_date, point_time, return_pct, quality
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(row.get("id")),
                    row.get("name"),
                    point.get("datetime"),
                    point.get("date"),
                    point.get("time"),
                    safe_float(point.get("return")),
                    row.get("quality"),
                ),
            )

    quant_view = data.get("quant_view") or {}
    insert_metrics(conn, run_id, "quant_diagnostics", quant_view.get("diagnostics"))
    for row in quant_view.get("rows") or []:
        label, klass = level_label(row)
        conn.execute(
            """
            INSERT OR REPLACE INTO quant_rows (
              run_id, name, style, benchmark, return_5d, excess_vs_core,
              score, level_label, level_class, comment
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row.get("name"),
                row.get("style"),
                row.get("benchmark"),
                safe_float(row.get("return_5d")),
                safe_float(row.get("excess_vs_core")),
                safe_float(row.get("score")),
                label,
                klass,
                row.get("comment"),
            ),
        )

    macro_view = data.get("macro_view") or {}
    insert_metrics(conn, run_id, "macro_view", {
        "score": macro_view.get("score"),
        "regime": macro_view.get("regime"),
        "dispersion": macro_view.get("dispersion"),
        "comment": macro_view.get("comment"),
    })
    for row in macro_view.get("rows") or []:
        conn.execute(
            """
            INSERT OR REPLACE INTO macro_rows
              (run_id, asset, proxy, horizon, change_pct, signal)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row.get("asset"),
                row.get("proxy"),
                row.get("horizon"),
                safe_float(row.get("change")),
                row.get("signal"),
            ),
        )

    insert_etfs(conn, run_id, data.get("etf_tracking"), "etf_tracking")
    insert_etfs(conn, run_id, data.get("macro_assets"), "macro_assets")
    insert_industries(conn, run_id, data.get("industry_rotation"), "industry_rotation")
    insert_metrics(conn, run_id, "microstructure", data.get("microstructure"))


def insert_styles(conn, run_id, rows):
    for row in rows or []:
        label, klass = level_label(row)
        members = row.get("members")
        if isinstance(members, list):
            members = "、".join(members)
        conn.execute(
            """
            INSERT OR REPLACE INTO style_rotation (
              run_id, name, members, score, level_label, level_class,
              day_delta, range_delta, pct_chg, amount_progress
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row.get("name"),
                members,
                safe_float(row.get("score")),
                label,
                klass,
                safe_float(row.get("day_delta")),
                safe_float(row.get("range_delta")),
                safe_float(row.get("pct_chg")),
                safe_float(row.get("amount_progress")),
            ),
        )


def insert_spreads(conn, run_id, rows):
    for row in rows or []:
        conn.execute(
            """
            INSERT OR REPLACE INTO style_spreads
              (run_id, name, value, abs_value, direction)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row.get("name"),
                safe_float(row.get("value")),
                safe_float(row.get("abs_value")),
                row.get("direction"),
            ),
        )


def insert_industries(conn, run_id, rows, source_key):
    for row in rows or []:
        conn.execute(
            """
            INSERT OR REPLACE INTO industries (
              run_id, source_key, industry_id, name, score, ret_5d, ret_20d,
              amount, amount_pct, net_inflow, net_inflow_pct, state, quality
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_key,
                row.get("id"),
                row.get("name"),
                safe_float(row.get("score")),
                safe_float(row.get("ret_5d")),
                safe_float(row.get("ret_20d")),
                safe_float(row.get("amount")),
                safe_float(row.get("amount_pct")),
                safe_float(row.get("net_inflow")),
                safe_float(row.get("net_inflow_pct")),
                row.get("state"),
                row.get("quality"),
            ),
        )


def insert_etfs(conn, run_id, rows, source_key):
    for row in rows or []:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_tracking
              (run_id, source_key, etf_id, name, bucket, pct_chg, amount, net_inflow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_key,
                str(row.get("id")),
                row.get("name"),
                row.get("bucket"),
                safe_float(row.get("pct_chg")),
                safe_float(row.get("amount")),
                safe_float(row.get("net_inflow")),
            ),
        )


def insert_signals(conn, run_id, rows, source_key):
    for idx, row in enumerate(rows or [], start=1):
        conn.execute(
            """
            INSERT OR REPLACE INTO signals
              (run_id, source_key, row_no, type, level, target, message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_key,
                idx,
                row.get("type"),
                row.get("level"),
                row.get("target"),
                row.get("message"),
            ),
        )


def insert_data_quality(conn, run_id, rows):
    for row in rows or []:
        conn.execute(
            """
            INSERT OR REPLACE INTO data_quality
              (run_id, item, status, detail)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, row.get("item"), row.get("status"), row.get("detail")),
        )


def insert_common_dashboard_tables(conn, run_id, data):
    insert_metrics(conn, run_id, "source", data.get("source"))
    insert_metrics(conn, run_id, "market_breadth", data.get("market_breadth"))
    insert_metrics(conn, run_id, "market_overview", data.get("market_overview"))
    insert_styles(conn, run_id, data.get("style_rotation"))
    insert_spreads(conn, run_id, data.get("style_spreads"))
    insert_industries(conn, run_id, data.get("industry_heatmap"), "industry_heatmap")
    insert_signals(conn, run_id, data.get("risk_alerts"), "risk_alerts")
    insert_signals(conn, run_id, data.get("divergence_signals"), "divergence_signals")
    insert_signals(conn, run_id, data.get("style_switch_signals"), "style_switch_signals")
    insert_signals(conn, run_id, data.get("anomaly_signals"), "anomaly_signals")
    insert_data_quality(conn, run_id, data.get("data_quality"))


def insert_daily(conn, run_id, data):
    insert_common_dashboard_tables(conn, run_id, data)
    insert_index_snapshots(conn, run_id, data.get("wide_indices"), "daily_close")
    for row in data.get("market_state") or []:
        label, klass = level_label(row)
        conn.execute(
            """
            INSERT OR REPLACE INTO metrics
              (run_id, namespace, metric_key, value_num, value_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "market_state",
                row.get("id") or row.get("name"),
                safe_float(row.get("score")),
                json.dumps({
                    "name": row.get("name"),
                    "label": label,
                    "class": klass,
                    "detail": row.get("detail"),
                }, ensure_ascii=False),
            ),
        )


def insert_morning(conn, run_id, data):
    insert_common_dashboard_tables(conn, run_id, data)
    insert_index_snapshots(conn, run_id, data.get("morning_indices"), "morning")


def ingest_dashboard_file(conn, path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    data = sanitize(raw)
    dashboard_type = detect_dashboard_type(data, Path(path))
    if dashboard_type in ("unknown", "congestion"):
        return None
    content_hash = hashlib.sha256(stable_json(data).encode("utf-8")).hexdigest()
    run_id = upsert_run(conn, data, dashboard_type, path, content_hash)
    if dashboard_type == "strategy":
        insert_common_dashboard_tables(conn, run_id, data)
        insert_strategy(conn, run_id, data)
    elif dashboard_type == "daily":
        insert_daily(conn, run_id, data)
    elif dashboard_type == "morning":
        insert_morning(conn, run_id, data)
    conn.commit()
    return run_id, dashboard_type, data.get("trade_date"), data.get("generated_at")


def ingest_congestion_data(conn, path=DATA_DIR / "congestion_data.json"):
    path = Path(path)
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    source_path = rel_path(path)
    updated_at = now_text()
    count = 0
    for trade_date, row in (data.get("MARKET_TOTAL") or {}).items():
        conn.execute(
            """
            INSERT INTO market_total_daily
              (trade_date, turnover, free_float_mv, source_path, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
              turnover=excluded.turnover,
              free_float_mv=excluded.free_float_mv,
              source_path=excluded.source_path,
              updated_at=excluded.updated_at
            """,
            (
                trade_date,
                safe_float(row.get("turnover")),
                safe_float(row.get("free_float_mv")),
                source_path,
                updated_at,
            ),
        )
        count += 1
    for trade_date, value in (data.get("TOP5_RATIO") or {}).items():
        conn.execute(
            """
            INSERT INTO market_concentration_daily
              (trade_date, top5_ratio, source_path, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
              top5_ratio=excluded.top5_ratio,
              source_path=excluded.source_path,
              updated_at=excluded.updated_at
            """,
            (trade_date, safe_float(value), source_path, updated_at),
        )
    for index_id, rows in (data.get("RAW_DATA") or {}).items():
        for trade_date, row in rows.items():
            conn.execute(
                """
                INSERT INTO wide_index_base_daily
                  (trade_date, index_id, turnover, free_mv, percentile, source_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, index_id) DO UPDATE SET
                  turnover=excluded.turnover,
                  free_mv=excluded.free_mv,
                  percentile=excluded.percentile,
                  source_path=excluded.source_path,
                  updated_at=excluded.updated_at
                """,
                (
                    trade_date,
                    index_id,
                    safe_float(row.get("turnover")),
                    safe_float(row.get("free_mv")),
                    safe_float(row.get("percentile")),
                    source_path,
                    updated_at,
                ),
            )
    conn.commit()
    return count


def dashboard_paths():
    paths = []
    for folder in (CACHE_DIR, HISTORY_DIR):
        if folder.exists():
            paths.extend(folder.rglob("*.json"))
    return sorted(set(paths))


def latest_path(kind):
    mapping = {
        "strategy": CACHE_DIR / "strategy_dashboard.json",
        "morning": CACHE_DIR / "morning_dashboard.json",
        "daily": CACHE_DIR / "market_dashboard.json",
        "congestion": DATA_DIR / "congestion_data.json",
    }
    return mapping.get(kind)


def ingest_all(conn):
    market_days = ingest_congestion_data(conn)
    runs = []
    for path in dashboard_paths():
        try:
            result = ingest_dashboard_file(conn, path)
            if result:
                runs.append(result)
        except Exception as exc:
            print(f"skip {rel_path(path)}: {exc}")
    return market_days, runs


def print_summary(conn):
    print(f"DB: {DB_PATH}")
    rows = conn.execute(
        "SELECT dashboard_type, COUNT(*) AS n, MAX(generated_at) AS latest_generated FROM runs GROUP BY dashboard_type ORDER BY dashboard_type"
    ).fetchall()
    print("Runs:")
    for row in rows:
        print(f"  {row['dashboard_type']}: {row['n']} runs, latest {row['latest_generated']}")
    counts = [
        ("market_total_daily", "market days"),
        ("wide_index_base_daily", "wide index base rows"),
        ("market_daily_bars", "unified daily bars"),
        ("market_minute_bars", "unified minute bars"),
        ("macro_observations", "macro observations"),
        ("data_source_health", "source health rows"),
        ("index_minute_bars", "raw minute bars"),
        ("index_intraday_points", "intraday points"),
        ("strategy_scores", "strategy scores"),
        ("industries", "industry rows"),
        ("signals", "signals"),
    ]
    print("Tables:")
    for table, label in counts:
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        print(f"  {label}: {n}")
    latest = conn.execute(
        """
        SELECT dashboard_type, trade_date, generated_at, headline
        FROM runs
        ORDER BY generated_at DESC, id DESC
        LIMIT 5
        """
    ).fetchall()
    print("Latest snapshots:")
    for row in latest:
        print(f"  {row['generated_at']} [{row['dashboard_type']}] {row['trade_date']} - {row['headline']}")


def print_health(conn):
    rows = conn.execute(
        """
        SELECT indicator_name, source, latest_date, frequency, rows_count,
               is_complete, status, detail, checked_at
        FROM data_source_health
        ORDER BY
          CASE status
            WHEN 'ok' THEN 1
            WHEN 'partial' THEN 2
            WHEN 'limited' THEN 3
            WHEN 'fallback' THEN 4
            ELSE 5
          END,
          indicator_key,
          source
        """
    ).fetchall()
    print("Data source health:")
    if not rows:
        print("  no health rows yet")
        return
    for row in rows:
        complete = "complete" if row["is_complete"] else "incomplete"
        print(
            f"  [{row['status']}] {row['indicator_name']} | {row['source']} | "
            f"latest {row['latest_date']} | {row['frequency']} | "
            f"{row['rows_count']} rows | {complete} | {row['detail']}"
        )


def main():
    parser = argparse.ArgumentParser(description="Local SQLite warehouse for the A-share research system.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init")

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--all", action="store_true", help="Ingest congestion data and all dashboard JSON files.")
    ingest.add_argument("--file", help="Ingest one dashboard JSON file.")
    ingest.add_argument(
        "--latest",
        choices=["strategy", "morning", "daily", "congestion", "all"],
        help="Ingest the latest cache for one module.",
    )

    sub.add_parser("summary")
    sub.add_parser("health")

    args = parser.parse_args()
    if not args.command:
        args.command = "summary"

    with connect() as conn:
        init_db(conn)
        if args.command == "init":
            print(f"Initialized {DB_PATH}")
        elif args.command == "ingest":
            if args.all or args.latest == "all":
                market_days, runs = ingest_all(conn)
                print(f"Ingested market base days: {market_days}")
                print(f"Ingested dashboard runs: {len(runs)}")
            elif args.latest:
                if args.latest == "congestion":
                    print(f"Ingested market base days: {ingest_congestion_data(conn)}")
                else:
                    if args.latest == "daily":
                        ingest_congestion_data(conn)
                    path = latest_path(args.latest)
                    result = ingest_dashboard_file(conn, path)
                    print(f"Ingested latest {args.latest}: {result}")
            elif args.file:
                path = Path(args.file)
                if path.name == "congestion_data.json":
                    print(f"Ingested market base days: {ingest_congestion_data(conn, path)}")
                else:
                    print(f"Ingested file: {ingest_dashboard_file(conn, path)}")
            else:
                market_days, runs = ingest_all(conn)
                print(f"Ingested market base days: {market_days}")
                print(f"Ingested dashboard runs: {len(runs)}")
        elif args.command == "summary":
            print_summary(conn)
        elif args.command == "health":
            print_health(conn)


if __name__ == "__main__":
    main()
