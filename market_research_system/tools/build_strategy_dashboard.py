import json
import math
import copy
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from build_market_dashboard import (
    CACHE_DIR,
    HISTORY_DIR,
    REPORT_DIR,
    SYSTEM_ROOT,
    avg,
    build_market_breadth,
    try_fetch_industries,
)
from source_health import load_source_health, summarize_source_health


STRATEGY_CACHE_JSON = CACHE_DIR / "strategy_dashboard.json"
STRATEGY_CACHE_JS = CACHE_DIR / "strategy_dashboard_data.js"
STRATEGY_HISTORY_DIR = HISTORY_DIR / "strategy"

INDEX_UNIVERSE = {
    "000016": {"name": "上证50", "secid": "1.000016", "style": "大盘权重"},
    "000300": {"name": "沪深300", "secid": "1.000300", "style": "大盘宽基"},
    "000905": {"name": "中证500", "secid": "1.000905", "style": "中盘"},
    "000852": {"name": "中证1000", "secid": "1.000852", "style": "小盘"},
    "000464": {"name": "中证2000", "secid": "2.932000", "style": "小盘"},
    "399006": {"name": "创业板指", "secid": "0.399006", "style": "成长"},
    "000688": {"name": "科创50", "secid": "1.000688", "style": "硬科技"},
    "8841331": {"name": "中证红利", "secid": "1.000922", "style": "红利低波"},
    "800007": {"name": "Choice微盘", "secid": "47.800007", "style": "微盘"},
}

ETF_UNIVERSE = {
    "510050": {"name": "上证50ETF", "secid": "1.510050", "bucket": "宽基/大盘"},
    "510300": {"name": "沪深300ETF", "secid": "1.510300", "bucket": "宽基/大盘"},
    "510500": {"name": "中证500ETF", "secid": "1.510500", "bucket": "宽基/中盘"},
    "512100": {"name": "中证1000ETF", "secid": "1.512100", "bucket": "宽基/小盘"},
    "159915": {"name": "创业板ETF", "secid": "0.159915", "bucket": "成长"},
    "588000": {"name": "科创50ETF", "secid": "1.588000", "bucket": "硬科技"},
    "510880": {"name": "红利ETF", "secid": "1.510880", "bucket": "红利低波"},
    "512880": {"name": "证券ETF", "secid": "1.512880", "bucket": "金融弹性"},
    "512480": {"name": "半导体ETF", "secid": "1.512480", "bucket": "科技主题"},
    "515790": {"name": "光伏ETF", "secid": "1.515790", "bucket": "新能源"},
}

MACRO_ETF_UNIVERSE = {
    "511010": {"name": "国债ETF", "secid": "1.511010", "bucket": "债券"},
    "511260": {"name": "十年国债ETF", "secid": "1.511260", "bucket": "债券"},
    "518880": {"name": "黄金ETF", "secid": "1.518880", "bucket": "黄金"},
    "159934": {"name": "黄金ETF", "secid": "0.159934", "bucket": "黄金"},
    "159985": {"name": "豆粕ETF", "secid": "0.159985", "bucket": "商品"},
    "159980": {"name": "有色ETF", "secid": "0.159980", "bucket": "商品"},
}

TRENDS_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    "?secid={secid}"
    "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
    "&ndays=5&iscr=0&iscca=0"
)

ETF_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get"
    "?fltt=2&invt=2"
    "&fields=f12,f14,f2,f3,f6,f62"
    "&secids={secids}"
)

STOCK_BREADTH_URL = (
    "https://push2delay.eastmoney.com/api/qt/clist/get"
    "?pn={page}&pz=100&po=1&np=1"
    "&ut=bd1d9ddb04089700cf9c27f6f7426281"
    "&fltt=2&invt=2&fid=f3"
    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    "&fields=f12,f14,f3,f6"
)


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_text():
    return datetime.now().strftime("%Y-%m-%d")


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


def strategy_get_json(url, timeout=8, retries=2):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/",
    }
    last_error = None
    for attempt in range(retries):
        try:
            import requests

            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
        time.sleep(0.5 + attempt * 0.5)
    raise last_error


def load_previous_cache():
    if not STRATEGY_CACHE_JSON.exists():
        return {}
    try:
        return json.loads(STRATEGY_CACHE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def round_optional(value, digits=2):
    return round(value, digits) if value is not None else None


def clamp(value, low=0, high=100):
    return max(low, min(high, value))


def parse_trend_line(line):
    parts = line.split(",")
    if len(parts) < 4:
        return None
    dt = parts[0]
    price = to_float(parts[2]) or to_float(parts[1])
    amount = to_float(parts[6]) if len(parts) > 6 else None
    if not price or price <= 0:
        return None
    return {
        "datetime": dt,
        "date": dt[:10],
        "time": dt[11:16],
        "price": price,
        "amount": amount / 1e8 if amount is not None else None,
    }


def fetch_index_trends(code, meta):
    payload = strategy_get_json(TRENDS_URL.format(secid=meta["secid"]), timeout=8, retries=2)
    raw_rows = payload.get("data", {}).get("trends", []) or []
    points = [p for p in (parse_trend_line(line) for line in raw_rows) if p]
    if not points:
        return {
            "id": code,
            "name": meta["name"],
            "style": meta["style"],
            "quality": "empty",
            "days": [],
            "points": [],
            "metrics": empty_trend_metrics(),
        }

    start_price = points[0]["price"]
    previous = None
    signed_moves = []
    abs_moves = []
    for point in points:
        point["return"] = (point["price"] / start_price - 1) * 100
        if previous is not None:
            move = point["price"] - previous
            signed_moves.append(move)
            abs_moves.append(abs(move))
        previous = point["price"]

    grouped = defaultdict(list)
    for point in points:
        grouped[point["date"]].append(point)

    days = []
    day_returns = []
    for date, rows in grouped.items():
        day_start = rows[0]["price"]
        day_end = rows[-1]["price"]
        day_return = (day_end / day_start - 1) * 100
        day_returns.append(day_return)
        days.append({
            "date": date,
            "start": round(day_start, 2),
            "end": round(day_end, 2),
            "return": round(day_return, 2),
            "range": round((max(p["price"] for p in rows) / min(p["price"] for p in rows) - 1) * 100, 2),
            "amount": round(sum((p["amount"] or 0) for p in rows), 0),
        })

    total_return = (points[-1]["price"] / points[0]["price"] - 1) * 100
    path_return = sum(abs(m) for m in signed_moves) / points[0]["price"] * 100 if signed_moves else 0
    efficiency = abs(total_return) / path_return * 100 if path_return else 0
    direction = 1 if total_return >= 0 else -1
    direction_hits = len([m for m in signed_moves if (m >= 0 and direction > 0) or (m <= 0 and direction < 0)])
    minute_consistency = direction_hits / len(signed_moves) * 100 if signed_moves else 0
    day_hits = len([v for v in day_returns if (v >= 0 and direction > 0) or (v <= 0 and direction < 0)])
    day_consistency = day_hits / len(day_returns) * 100 if day_returns else 0
    cta_score = clamp(efficiency * 0.48 + minute_consistency * 0.22 + day_consistency * 0.18 + min(abs(total_return) * 10, 100) * 0.12)

    metrics = {
        "total_return": round(total_return, 2),
        "path_return": round(path_return, 2),
        "trend_efficiency": round(efficiency, 1),
        "minute_consistency": round(minute_consistency, 1),
        "day_consistency": round(day_consistency, 1),
        "cta_score": round(cta_score, 1),
        "label": trend_label(cta_score, total_return),
    }

    return {
        "id": code,
        "name": meta["name"],
        "style": meta["style"],
        "quality": "ok",
        "days": days,
        "points": [
            {
                "datetime": p["datetime"],
                "date": p["date"],
                "time": p["time"],
                "return": round(p["return"], 3),
            }
            for p in points
        ],
        "metrics": metrics,
    }


def empty_trend_metrics():
    return {
        "total_return": None,
        "path_return": None,
        "trend_efficiency": 0,
        "minute_consistency": 0,
        "day_consistency": 0,
        "cta_score": 0,
        "label": "无数据",
    }


def trend_label(score, total_return):
    prefix = "上行" if total_return >= 0 else "下行"
    if score >= 70:
        return f"{prefix}趋势连贯"
    if score >= 50:
        return f"{prefix}趋势可用"
    if score >= 35:
        return "趋势一般"
    return "震荡噪声"


def cached_trend_row(code, previous_cache, error):
    cached = {
        row.get("id"): row
        for row in previous_cache.get("index_trends", [])
        if row.get("id")
    }.get(code)
    if cached and cached.get("points"):
        row = copy.deepcopy(cached)
        row["quality"] = "cache"
        row["quality_detail"] = f"本次接口失败，沿用上一份缓存: {error}"
        return row
    meta = INDEX_UNIVERSE[code]
    return {
        "id": code,
        "name": meta["name"],
        "style": meta["style"],
        "quality": f"failed: {error}",
        "days": [],
        "points": [],
        "metrics": empty_trend_metrics(),
    }


def fetch_all_index_trends(previous_cache=None):
    previous_cache = previous_cache or {}

    def fetch_one(code, meta):
        last_error = None
        for attempt in range(2):
            try:
                return fetch_index_trends(code, meta)
            except Exception as exc:
                last_error = exc
                time.sleep(0.8 + attempt)
        return cached_trend_row(code, previous_cache, last_error)

    by_code = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(fetch_one, code, meta): code
            for code, meta in INDEX_UNIVERSE.items()
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                by_code[code] = future.result()
            except Exception as exc:
                by_code[code] = cached_trend_row(code, previous_cache, exc)
    rows = [by_code[code] for code in INDEX_UNIVERSE if code in by_code]
    return sorted(rows, key=lambda x: x["metrics"]["cta_score"], reverse=True)


def fetch_etf_universe(universe):
    try:
        payload = strategy_get_json(ETF_URL.format(secids=",".join(meta["secid"] for meta in universe.values())), timeout=8, retries=2)
        rows = payload.get("data", {}).get("diff", []) or []
    except Exception:
        return []
    code_to_meta = {code: meta for code, meta in universe.items()}
    result = []
    for row in rows:
        code = str(row.get("f12"))
        meta = code_to_meta.get(code)
        if not meta:
            continue
        amount = to_float(row.get("f6"))
        net = to_float(row.get("f62"))
        pct = to_float(row.get("f3"))
        result.append({
            "id": code,
            "name": meta["name"],
            "bucket": meta["bucket"],
            "pct_chg": round(pct, 2) if pct is not None else None,
            "amount": round(amount / 1e8, 0) if amount is not None else None,
            "net_inflow": round(net / 1e8, 2) if net is not None else None,
        })
    return sorted(result, key=lambda x: x["amount"] or 0, reverse=True)


def fetch_etfs():
    return fetch_etf_universe(ETF_UNIVERSE)


def fetch_macro_assets():
    return fetch_etf_universe(MACRO_ETF_UNIVERSE)


def fetch_market_microstructure(previous_cache=None):
    try:
        first_page = strategy_get_json(STOCK_BREADTH_URL.format(page=1), timeout=8, retries=2)
    except Exception:
        cached = (previous_cache or {}).get("microstructure")
        if cached:
            cached = copy.deepcopy(cached)
            cached["quality"] = "cache"
            return cached
        return {"sample_count": 0, "quality": "failed"}
    first_data = first_page.get("data", {}) or {}
    total = first_data.get("total") or 0
    rows = list(first_data.get("diff", []) or [])
    total_pages = min(80, (total + 99) // 100) if total else 1
    if total_pages > 1:
        import requests

        def fetch_page(page):
            response = requests.get(
                STOCK_BREADTH_URL.format(page=page),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": "https://quote.eastmoney.com/",
                },
                timeout=8,
            )
            response.raise_for_status()
            return response.json().get("data", {}).get("diff", []) or []

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(fetch_page, page) for page in range(2, total_pages + 1)]
            for future in as_completed(futures):
                try:
                    rows.extend(future.result())
                except Exception:
                    pass

    pct_values = [to_float(row.get("f3")) for row in rows]
    pct_values = [v for v in pct_values if v is not None]
    up = len([v for v in pct_values if v > 0])
    down = len([v for v in pct_values if v < 0])
    limit_up = len([v for v in pct_values if v >= 9.8])
    limit_down = len([v for v in pct_values if v <= -9.8])
    strong = len([v for v in pct_values if v >= 5])
    weak = len([v for v in pct_values if v <= -5])
    return {
        "sample_count": len(pct_values),
        "up_count": up,
        "down_count": down,
        "up_ratio": round(up / len(pct_values) * 100, 1) if pct_values else None,
        "limit_up_est": limit_up,
        "limit_down_est": limit_down,
        "limit_up_ratio": round(limit_up / len(pct_values) * 100, 2) if pct_values else None,
        "limit_down_ratio": round(limit_down / len(pct_values) * 100, 2) if pct_values else None,
        "strong_count": strong,
        "weak_count": weak,
        "quality": "ok",
    }


def trend_return_map(index_trends):
    return {
        row["id"]: row["metrics"]["total_return"]
        for row in index_trends
        if row.get("metrics", {}).get("total_return") is not None
    }


def name_map(index_trends):
    return {row["id"]: row["name"] for row in index_trends}


def safe_avg(values, default=None):
    value = avg([v for v in values if v is not None])
    return value if value is not None else default


def strategy_level(score):
    if score >= 70:
        return {"label": "友好", "class": "safe"}
    if score >= 50:
        return {"label": "中性", "class": "warning"}
    return {"label": "不友好", "class": "danger"}


def quant_comment(score, excess):
    if excess is None:
        return "指数分时数据不足，暂用市场宽度和尾部风险代理判断。"
    if score >= 70 and excess > 0:
        return f"基准相对核心宽基超额 {excess:.2f}%，宽度与小微盘环境对指增更友好。"
    if score >= 50:
        return f"基准相对核心宽基超额 {excess:.2f}%，指增环境中性，更多看模型风格暴露。"
    return f"基准相对核心宽基超额 {excess:.2f}%，叠加宽度或尾部风险约束，指增环境偏难。"


def build_quant_view(index_trends, microstructure, breadth):
    returns = trend_return_map(index_trends)
    names = name_map(index_trends)
    core_return = safe_avg([returns.get("000016"), returns.get("000300")], 0)
    small_return = safe_avg([returns.get("000852"), returns.get("000464"), returns.get("800007")], 0)
    dispersion_values = [v for v in returns.values() if v is not None]
    dispersion = (max(dispersion_values) - min(dispersion_values)) if dispersion_values else 0
    up_ratio = microstructure.get("up_ratio") or 50
    sample = microstructure.get("sample_count") or 1
    strong = microstructure.get("strong_count") or 0
    weak = microstructure.get("weak_count") or 0
    weak_pressure = weak / sample * 100 if sample else 0
    limit_down = microstructure.get("limit_down_ratio") or 0
    small_premium = small_return - core_return
    base = clamp(
        45
        + dispersion * 5
        + (up_ratio - 50) * 0.35
        + ((breadth.get("diffusion_score") or 50) - 50) * 0.25
        + small_premium * 3
        - limit_down * 8
        - weak_pressure * 0.25
    )

    configs = [
        ("500指增", "000905", ["000300"], "中盘alpha"),
        ("1000指增", "000852", ["000300", "000905"], "小盘alpha"),
        ("科创指增", "000688", ["000300", "399006"], "硬科技alpha"),
        ("小微盘量化", "800007", ["000852", "000905"], "小微盘beta"),
    ]
    rows = []
    for label, benchmark_id, hedge_ids, style in configs:
        benchmark_return = returns.get(benchmark_id)
        hedge_return = safe_avg([returns.get(item) for item in hedge_ids])
        excess = benchmark_return - hedge_return if benchmark_return is not None and hedge_return is not None else None
        score = clamp(base + (excess or 0) * 8 + (benchmark_return or 0) * 2)
        rows.append({
            "name": label,
            "style": style,
            "benchmark": names.get(benchmark_id, benchmark_id),
            "return_5d": round_optional(benchmark_return),
            "excess_vs_core": round_optional(excess),
            "score": round(score, 1),
            "level": strategy_level(score),
            "comment": quant_comment(score, excess),
        })

    overall = safe_avg([row["score"] for row in rows], 0)
    return {
        "score": round(overall, 1),
        "level": strategy_level(overall),
        "comment": f"小微盘相对核心宽基 {small_premium:.2f}%，全A上涨占比 {up_ratio:.1f}%，跌停估算占比 {limit_down:.2f}%。",
        "diagnostics": {
            "small_premium": round(small_premium, 2),
            "dispersion": round(dispersion, 2),
            "up_ratio": round(up_ratio, 1),
            "weak_pressure": round(weak_pressure, 2),
            "method": "公开行情源暂以指数相对收益、全A宽度、强弱家数和尾部风险代理指增环境；接入Choice后可升级为成分股涨跌分布和等权/市值加权超额。",
        },
        "rows": rows,
    }


def bucket_avg(rows, bucket):
    values = [row.get("pct_chg") for row in rows if row.get("bucket") == bucket and row.get("pct_chg") is not None]
    return safe_avg(values)


def macro_signal(asset, value):
    if value is None:
        return "数据不足"
    if asset == "股票":
        return "风险偏好抬升" if value > 0.3 else "风险偏好回落" if value < -0.3 else "震荡"
    if asset == "债券":
        return "利率下行/避险" if value > 0.1 else "利率上行压力" if value < -0.1 else "低波动"
    if asset == "黄金":
        return "避险或实际利率交易" if value > 0.3 else "避险降温" if value < -0.3 else "震荡"
    return "商品走强" if value > 0.3 else "商品走弱" if value < -0.3 else "震荡"


def build_macro_view(index_trends, macro_assets):
    returns = trend_return_map(index_trends)
    equity_5d = safe_avg([returns.get("000016"), returns.get("000300"), returns.get("8841331"), returns.get("399006")])
    bond_day = bucket_avg(macro_assets, "债券")
    gold_day = bucket_avg(macro_assets, "黄金")
    commodity_day = bucket_avg(macro_assets, "商品")
    comparable = [v for v in [equity_5d, bond_day, gold_day, commodity_day] if v is not None]
    cross_asset_dispersion = max(comparable) - min(comparable) if len(comparable) >= 2 else 0
    score = clamp(45 + cross_asset_dispersion * 7 + abs(equity_5d or 0) * 3 + max(gold_day or 0, 0) * 4 + max(bond_day or 0, 0) * 2)
    if equity_5d is not None and equity_5d > 0.5 and (gold_day or 0) <= 0:
        regime = "偏风险资产"
    elif equity_5d is not None and equity_5d < -0.5 and ((gold_day or 0) > 0 or (bond_day or 0) > 0):
        regime = "偏防御/避险"
    elif cross_asset_dispersion >= 1:
        regime = "资产分化"
    else:
        regime = "股债商震荡"

    rows = [
        {"asset": "股票", "proxy": "宽基指数均值", "horizon": "5日", "change": round_optional(equity_5d), "signal": macro_signal("股票", equity_5d)},
        {"asset": "债券", "proxy": "国债ETF篮子", "horizon": "当日", "change": round_optional(bond_day), "signal": macro_signal("债券", bond_day)},
        {"asset": "黄金", "proxy": "黄金ETF篮子", "horizon": "当日", "change": round_optional(gold_day), "signal": macro_signal("黄金", gold_day)},
        {"asset": "商品", "proxy": "商品ETF篮子", "horizon": "当日", "change": round_optional(commodity_day), "signal": macro_signal("商品", commodity_day)},
    ]
    return {
        "score": round(score, 1),
        "level": strategy_level(score),
        "regime": regime,
        "comment": f"当前宏观代理为{regime}，股债商离散约 {cross_asset_dispersion:.2f}%。债券部分先用国债ETF日内代理，后续可接入中债/国债期货波动率。",
        "dispersion": round(cross_asset_dispersion, 2),
        "rows": rows,
        "assets": macro_assets,
    }


def build_strategy_scores(index_trends, industries, etfs, microstructure, quant_view, macro_view):
    cta_score = avg([row["metrics"]["cta_score"] for row in index_trends if row.get("quality") in ("ok", "cache")]) or 0
    returns = [row["metrics"]["total_return"] for row in index_trends if row["metrics"]["total_return"] is not None]
    dispersion = max(returns) - min(returns) if returns else 0
    breadth = build_market_breadth(industries)
    option_score = clamp((dispersion * 8) + (100 - (microstructure.get("up_ratio") or 50)) * 0.25 + breadth["dispersion_score"] * 0.5)
    low_vol_score = clamp(100 - cta_score * 0.35 - dispersion * 6 - max((microstructure.get("limit_up_ratio") or 0) * 5, (microstructure.get("limit_down_ratio") or 0) * 7))
    etf_amount = sum((row.get("amount") or 0) for row in etfs)
    etf_net = sum((row.get("net_inflow") or 0) for row in etfs if row.get("net_inflow") is not None)
    fund_flow_score = clamp(50 + etf_net / 10)

    return {
        "cta": strategy_row("CTA/趋势", cta_score, cta_comment(cta_score, index_trends)),
        "quant": strategy_row("量化/指增", quant_view["score"], quant_view["comment"]),
        "option": strategy_row("期权/波动", option_score, option_comment(option_score, dispersion, microstructure)),
        "low_vol": strategy_row("低波/FOF", low_vol_score, low_vol_comment(low_vol_score, breadth, microstructure)),
        "macro": strategy_row("宏观/股债商", macro_view["score"], macro_view["comment"]),
        "etf_flow": strategy_row("ETF资金", fund_flow_score, f"监控ETF合计成交 {etf_amount:.0f} 亿，净流入 {etf_net:.1f} 亿。"),
    }


def strategy_row(name, score, comment):
    level = strategy_level(score)
    return {"name": name, "score": round(score, 1), "level": level, "comment": comment}


def cta_comment(score, rows):
    top = rows[0] if rows else None
    if not top:
        return "指数分时数据不足，暂不判断趋势策略环境。"
    if score >= 70:
        return f"{top['name']}趋势最连贯，过去5日路径效率 {top['metrics']['trend_efficiency']}%，趋势类策略环境偏友好。"
    if score >= 50:
        return f"{top['name']}相对更有趋势，但整体仍有噪声，CTA表现可能分化。"
    return "主要指数趋势效率偏低，分时路径更偏震荡，CTA容易遇到来回打脸。"


def option_comment(score, dispersion, micro):
    if score >= 70:
        return f"指数分化和个股尾部波动较高，波动/方向结构机会更活跃；指数间5日收益离散约 {dispersion:.2f}%。"
    if score >= 50:
        return f"波动环境中性，指数间5日收益离散约 {dispersion:.2f}%，适合看结构而不是单纯卖波。"
    return "波动和分化都不强，期权策略更依赖定价偏差和精细择时。"


def low_vol_comment(score, breadth, micro):
    if score >= 70:
        return f"市场扩散和尾部冲击都较温和，低波/FOF组合环境相对舒适；板块扩散 {breadth['diffusion_score']}。"
    if score >= 50:
        return f"低波环境中性，需关注热点扩散和成交集中度；板块扩散 {breadth['diffusion_score']}。"
    return "趋势和结构分化偏强，低波组合可能面临风格偏离和回撤压力。"


def build_summary(index_trends, strategy_scores, industries, etfs, microstructure, quant_view, macro_view):
    top_trend = index_trends[0] if index_trends else None
    top_industry = industries[0] if industries else None
    top_etf = etfs[0] if etfs else None
    headline = (
        f"策略研究快照：CTA环境 {strategy_scores['cta']['level']['label']}，"
        f"量化环境 {strategy_scores['quant']['level']['label']}，"
        f"低波环境 {strategy_scores['low_vol']['level']['label']}，"
        f"宏观环境 {strategy_scores['macro']['level']['label']}。"
    )
    bullets = []
    if top_trend:
        bullets.append(f"过去5日趋势最连贯的是 {top_trend['name']}，CTA趋势分 {top_trend['metrics']['cta_score']}，5日收益 {top_trend['metrics']['total_return']}%。")
    if top_industry:
        bullets.append(f"主线强度最高方向为 {top_industry['name']}，板块热度 {top_industry['score']}，主力净流入 {top_industry.get('net_inflow')} 亿。")
    if top_etf:
        bullets.append(f"ETF成交最活跃为 {top_etf['name']}，成交 {top_etf['amount']} 亿，净流入 {top_etf['net_inflow']} 亿。")
    bullets.append(f"量化指增代理：小微盘相对核心宽基 {quant_view['diagnostics']['small_premium']}%，全A上涨占比 {quant_view['diagnostics']['up_ratio']}%。")
    bullets.append(f"宏观股债商代理：{macro_view['regime']}，资产离散 {macro_view['dispersion']}%。")
    bullets.append(f"全A上涨占比 {microstructure.get('up_ratio')}%，涨停估算 {microstructure.get('limit_up_est')} 家，跌停估算 {microstructure.get('limit_down_est')} 家。")
    bullets.append("页面不展示个股明细，只保留板块、指数、ETF和市场微结构指标。")
    return {"headline": headline, "bullets": bullets}


def build_strategy_dashboard():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STRATEGY_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    previous_cache = load_previous_cache()
    index_trends = fetch_all_index_trends(previous_cache)
    industries, industry_source = try_fetch_industries()
    industry_quality = "ok" if industries else "proxy"
    industries = industries or previous_cache.get("industry_rotation", [])
    if not industries:
        industry_quality = "proxy"
    etfs = fetch_etfs()
    if not etfs:
        etfs = previous_cache.get("etf_tracking", [])
    macro_assets = fetch_macro_assets()
    if not macro_assets:
        macro_assets = previous_cache.get("macro_assets", [])
    microstructure = fetch_market_microstructure(previous_cache)
    breadth = build_market_breadth(industries)
    quant_view = build_quant_view(index_trends, microstructure, breadth)
    macro_view = build_macro_view(index_trends, macro_assets)
    scores = build_strategy_scores(index_trends, industries, etfs, microstructure, quant_view, macro_view)
    summary = build_summary(index_trends, scores, industries, etfs, microstructure, quant_view, macro_view)
    trade_date = today_text()
    data_health = load_source_health()

    dashboard = {
        "version": "0.2.0",
        "generated_at": now_text(),
        "trade_date": trade_date,
        "source": {
            "index_intraday": "东方财富 trends2 最近5日分时",
            "historical_intraday": "当前版本仅缓存最近5日；任意历史回看需要后续每日沉淀或接入Choice分钟线",
            "industry": industry_source,
            "industry_quality": industry_quality,
        },
        "summary": summary,
        "strategy_scores": scores,
        "quant_view": quant_view,
        "macro_view": macro_view,
        "index_trends": index_trends,
        "industry_rotation": industries,
        "market_breadth": breadth,
        "etf_tracking": etfs,
        "macro_assets": macro_assets,
        "microstructure": microstructure,
        "data_health": data_health,
        "data_health_summary": summarize_source_health(data_health),
        "data_quality": [
            {"item": "指数分时", "status": "ok" if any(r["quality"] == "ok" for r in index_trends) else "cache", "detail": f"{len([r for r in index_trends if r['quality'] == 'ok'])}/{len(INDEX_UNIVERSE)} 个指数返回最近5日分时，{len([r for r in index_trends if r['quality'] == 'cache'])} 个使用缓存。"},
            {"item": "历史回看", "status": "limited", "detail": "公开接口优先支持最近5日分时；2025年以来任意日期建议后续做本地分钟线沉淀。"},
            {"item": "行业轮动", "status": industry_quality, "detail": industry_source},
            {"item": "ETF跟踪", "status": "ok" if etfs else "fallback", "detail": f"{len(etfs)} 个常用ETF样本。"},
            {"item": "宏观资产", "status": "ok" if macro_assets else "fallback", "detail": f"{len(macro_assets)} 个债券、黄金和商品ETF代理样本。"},
            {"item": "个股明细", "status": "hidden", "detail": "仅使用全市场聚合统计，不展示个股名称。"},
        ],
    }
    snapshot_path = STRATEGY_HISTORY_DIR / f"strategy_dashboard_{trade_date}.json"
    dashboard["history"] = {
        "snapshot": str(snapshot_path.relative_to(SYSTEM_ROOT)).replace("\\", "/")
    }
    snapshot_path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    STRATEGY_CACHE_JSON.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    with STRATEGY_CACHE_JS.open("w", encoding="utf-8") as f:
        f.write("window.STRATEGY_DASHBOARD_DATA = ")
        json.dump(dashboard, f, ensure_ascii=False)
        f.write(";\n")
    return dashboard


if __name__ == "__main__":
    result = build_strategy_dashboard()
    print(json.dumps({
        "generated_at": result["generated_at"],
        "trade_date": result["trade_date"],
        "top_trend": result["index_trends"][0]["name"] if result["index_trends"] else "-",
        "cta_score": result["strategy_scores"]["cta"]["score"],
        "industries": len(result["industry_rotation"]),
        "etfs": len(result["etf_tracking"]),
    }, ensure_ascii=False, indent=2))
