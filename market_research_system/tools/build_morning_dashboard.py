import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from build_market_dashboard import (
    CACHE_DIR,
    HISTORY_DIR,
    INDEX_META,
    REPORT_DIR,
    STYLE_GROUPS,
    SYSTEM_ROOT,
    avg,
    build_divergence_signals,
    build_market_breadth,
    build_proxy_industries,
    build_risk_alerts,
    build_style_spreads,
    clamp,
    compute_composite,
    compute_index_series,
    filter_valid_congestion_dates,
    get_json,
    level,
    load_congestion_data,
    pct_rank,
    signed,
    try_fetch_industries,
)
from source_health import load_source_health, summarize_source_health


MORNING_CACHE_JSON = CACHE_DIR / "morning_dashboard.json"
MORNING_CACHE_JS = CACHE_DIR / "morning_dashboard_data.js"
MORNING_HISTORY_DIR = HISTORY_DIR / "morning"
PROJECTION_FACTOR = 1.85

INDEX_SECIDS = {
    "000016": "1.000016",
    "000300": "1.000300",
    "000905": "1.000905",
    "000852": "1.000852",
    "000464": "2.932000",
    "399006": "0.399006",
    "000688": "1.000688",
    "8841331": "1.000922",
    "800007": "47.800007",
}

EASTMONEY_INDEX_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get"
    "?fltt=2&invt=2"
    "&fields=f12,f14,f2,f3,f4,f5,f6,f17,f18,f20,f21,f62"
    "&secids={secids}"
)

EASTMONEY_ALL_A_INDEX_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get"
    "?fltt=2&invt=2"
    "&fields=f12,f14,f2,f3,f6,f20,f21"
    "&secids=0.399317"
)

EASTMONEY_STOCK_BREADTH_URL = (
    "https://push2delay.eastmoney.com/api/qt/clist/get"
    "?pn={page}&pz=100&po=1&np=1"
    "&ut=bd1d9ddb04089700cf9c27f6f7426281"
    "&fltt=2&invt=2&fid=f3"
    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    "&fields=f12,f14,f3,f6"
)


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def trade_date_text():
    return datetime.now().strftime("%Y-%m-%d")


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_optional(value, digits=1, suffix=""):
    if value is None:
        return "-"
    return f"{value:.{digits}f}{suffix}"


def fetch_live_indices():
    payload = get_json(EASTMONEY_INDEX_URL.format(secids=",".join(INDEX_SECIDS.values())))
    rows = payload.get("data", {}).get("diff", []) or []
    east_code_to_id = {
        "000016": "000016",
        "000300": "000300",
        "000905": "000905",
        "000852": "000852",
        "932000": "000464",
        "399006": "399006",
        "000688": "000688",
        "000922": "8841331",
        "800007": "800007",
    }
    result = {}
    for row in rows:
        code = east_code_to_id.get(str(row.get("f12")))
        if code:
            result[code] = row
    return result


def fetch_all_a_snapshot():
    index_payload = get_json(EASTMONEY_ALL_A_INDEX_URL, timeout=20)
    index_rows = index_payload.get("data", {}).get("diff", []) or []
    all_a_index = index_rows[0] if index_rows else {}
    amount = (safe_float(all_a_index.get("f6")) or 0) / 1e8

    first_page = get_json(EASTMONEY_STOCK_BREADTH_URL.format(page=1), timeout=30)
    first_data = first_page.get("data", {}) or {}
    total = first_data.get("total") or 0
    rows = list(first_data.get("diff", []) or [])
    total_pages = min(80, (total + 99) // 100) if total else 1

    if total_pages > 1:
        try:
            import requests

            def fetch_page(page):
                response = requests.get(
                    EASTMONEY_STOCK_BREADTH_URL.format(page=page),
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Referer": "https://quote.eastmoney.com/",
                    },
                    timeout=20,
                )
                response.raise_for_status()
                return response.json().get("data", {}).get("diff", []) or []

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(fetch_page, page) for page in range(2, total_pages + 1)]
                for future in as_completed(futures):
                    rows.extend(future.result())
        except Exception:
            pass

    pct_rows = [safe_float(row.get("f3")) for row in rows]
    pct_rows = [v for v in pct_rows if v is not None]
    up_count = len([v for v in pct_rows if v > 0])
    down_count = len([v for v in pct_rows if v < 0])
    flat_count = len(pct_rows) - up_count - down_count
    return {
        "stock_count": len(pct_rows),
        "amount": round(amount, 0),
        "index_name": all_a_index.get("f14") or "国证A指",
        "index_pct_chg": safe_float(all_a_index.get("f3")),
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "up_ratio": round(up_count / len(pct_rows) * 100, 1) if pct_rows else None,
    }


def latest_raw_row(congestion, code):
    last_date = congestion["ALL_DATES"][-1]
    return congestion["RAW_DATA"].get(code, {}).get(last_date)


def history_turnovers(congestion, code):
    rows = congestion["RAW_DATA"].get(code, {})
    return [row["turnover"] for row in rows.values() if row.get("turnover") is not None]


def build_morning_indices(congestion, live_rows, all_a_amount):
    previous_indices = compute_index_series(congestion)
    previous_by_id = {row["id"]: row for row in previous_indices}
    trade_date = trade_date_text()
    result = []

    for code, meta in INDEX_META.items():
        live = live_rows.get(code)
        base = latest_raw_row(congestion, code)
        previous = previous_by_id.get(code)
        if not base or not previous:
            continue

        if live:
            live_amount = (safe_float(live.get("f6")) or 0) / 1e8
            pct_chg = safe_float(live.get("f3"))
            close = safe_float(live.get("f2"))
            net_inflow = (safe_float(live.get("f62")) or 0) / 1e8 if live.get("f62") is not None else None
            quality = "realtime"
        else:
            live_amount = base["turnover"] / PROJECTION_FACTOR
            pct_chg = None
            close = None
            net_inflow = None
            quality = "cache"

        projected_turnover = live_amount * PROJECTION_FACTOR
        free_mv = base["free_mv"]
        congestion_score = projected_turnover / free_mv * 100 if free_mv else 0
        turnover_ratio = live_amount / all_a_amount * 100 if all_a_amount else 0
        percentile = pct_rank(history_turnovers(congestion, code), projected_turnover)
        if percentile is None:
            percentile = base.get("percentile", 0)
        derived = {
            "congestion": congestion_score,
            "turnover_ratio": turnover_ratio,
            "turnover_rate": congestion_score,
            "percentile": percentile,
            "turnover": projected_turnover,
        }
        score = compute_composite(derived)
        day_delta = score - previous["score"]
        amount_progress = live_amount / base["turnover"] * 100 if base["turnover"] else None
        projected_vs_last = projected_turnover / base["turnover"] * 100 if base["turnover"] else None
        baseline_mismatch = projected_vs_last is not None and projected_vs_last > 300
        if baseline_mismatch:
            day_delta = None
            amount_progress = None
            projected_vs_last = None
            quality = f"{quality}/baseline_mismatch"
        series = list(previous.get("series", []))
        series.append({"date": f"{trade_date} AM", "score": round(score, 1)})

        result.append({
            "id": code,
            "name": meta["name"],
            "group": meta["group"],
            "date": trade_date,
            "score": round(score, 1),
            "day_delta": round(day_delta, 1) if day_delta is not None else None,
            "range_delta": round(day_delta, 1) if day_delta is not None else None,
            "recent_delta": round(day_delta, 1) if day_delta is not None else None,
            "congestion": round(congestion_score, 2),
            "turnover_ratio": round(turnover_ratio, 2),
            "turnover_rate": round(congestion_score, 2),
            "percentile": round(percentile, 0),
            "turnover": round(projected_turnover, 0),
            "morning_amount": round(live_amount, 0),
            "amount_progress": round(amount_progress, 1) if amount_progress is not None else None,
            "projected_vs_last": round(projected_vs_last, 1) if projected_vs_last is not None else None,
            "pct_chg": round(pct_chg, 2) if pct_chg is not None else None,
            "close": close,
            "net_inflow": round(net_inflow, 2) if net_inflow is not None else None,
            "level": level(score),
            "quality": quality,
            "baseline_mismatch": baseline_mismatch,
            "coverage": f"{len(series)}/{len(congestion['ALL_DATES']) + 1}",
            "series": series,
        })

    return sorted(result, key=lambda x: x["score"], reverse=True), previous_indices


def build_morning_style_rotation(indices, previous_indices):
    previous_styles = build_previous_styles(previous_indices)
    previous_by_name = {row["name"]: row for row in previous_styles}
    by_id = {row["id"]: row for row in indices}
    rows = []
    for group, codes in STYLE_GROUPS.items():
        members = [by_id[c] for c in codes if c in by_id]
        if not members:
            continue
        score = avg([m["score"] for m in members])
        pct_chg = avg([m["pct_chg"] for m in members if m["pct_chg"] is not None])
        amount_progress = avg([m["amount_progress"] for m in members if m["amount_progress"] is not None])
        previous_score = previous_by_name.get(group, {}).get("score")
        member_delta = avg([m["day_delta"] for m in members if m["day_delta"] is not None])
        delta = member_delta if member_delta is not None else (score - previous_score if previous_score is not None else None)
        rows.append({
            "name": group,
            "members": "、".join(m["name"] for m in members),
            "score": round(score, 1),
            "day_delta": round(delta, 1) if delta is not None else None,
            "range_delta": round(delta, 1) if delta is not None else None,
            "pct_chg": round(pct_chg, 2) if pct_chg is not None else None,
            "amount_progress": round(amount_progress, 1) if amount_progress is not None else None,
            "level": level(score),
        })
    return sorted(rows, key=lambda x: x["score"], reverse=True), previous_styles


def build_previous_styles(previous_indices):
    by_id = {row["id"]: row for row in previous_indices}
    rows = []
    for group, codes in STYLE_GROUPS.items():
        members = [by_id[c] for c in codes if c in by_id]
        if members:
            score = avg([m["score"] for m in members])
            rows.append({
                "name": group,
                "members": "、".join(m["name"] for m in members),
                "score": round(score, 1),
                "level": level(score),
            })
    return sorted(rows, key=lambda x: x["score"], reverse=True)


def build_market_overview(congestion, all_a):
    last_date = congestion["ALL_DATES"][-1]
    last_market = congestion["MARKET_TOTAL"][last_date]
    amount = all_a.get("amount") or 0
    projected = amount * PROJECTION_FACTOR
    return {
        "last_trade_date": last_date,
        "all_a_amount": round(amount, 0),
        "last_full_day_amount": round(last_market["turnover"], 0),
        "amount_progress": round(amount / last_market["turnover"] * 100, 1) if last_market["turnover"] else None,
        "projected_full_day_amount": round(projected, 0),
        "projected_vs_last": round(projected / last_market["turnover"] * 100, 1) if last_market["turnover"] else None,
        "projection_factor": PROJECTION_FACTOR,
        "stock_count": all_a.get("stock_count"),
        "up_count": all_a.get("up_count"),
        "down_count": all_a.get("down_count"),
        "flat_count": all_a.get("flat_count"),
        "up_ratio": all_a.get("up_ratio"),
    }


def build_anomaly_signals(indices, industries):
    signals = []
    for row in sorted(indices, key=lambda x: (x.get("day_delta") or 0), reverse=True)[:3]:
        if row.get("day_delta") is not None and row["day_delta"] >= 5:
            signals.append({
                "type": "宽基升温",
                "level": "中",
                "target": row["name"],
                "message": f"早盘温度较昨日收盘升 {row['day_delta']:.1f} 分，成交进度 {fmt_optional(row.get('amount_progress'), 1, '%')}。",
            })
    for row in sorted(indices, key=lambda x: (x.get("pct_chg") if x.get("pct_chg") is not None else -99), reverse=True)[:3]:
        if row.get("pct_chg") is not None and row["pct_chg"] >= 1:
            signals.append({
                "type": "指数异动",
                "level": "中",
                "target": row["name"],
                "message": f"早盘涨跌幅 {row['pct_chg']:.2f}%，温度 {row['score']:.1f}。",
            })

    volume_breakouts = [
        row for row in industries
        if row.get("score", 0) >= 75 and (row.get("amount_pct") or 0) >= 80 and (row.get("ret_5d") or 0) >= 1.5
    ]
    for row in volume_breakouts[:6]:
        signals.append({
            "type": "板块放量异动",
            "level": "中",
            "target": row["name"],
            "message": f"热度 {row['score']:.1f}，涨跌 {row['ret_5d']:.2f}%，成交分位 {row['amount_pct']:.0f}%。",
        })

    hot_outflows = [
        row for row in industries
        if row.get("score", 0) >= 70 and row.get("net_inflow") is not None and row["net_inflow"] < 0
    ]
    for row in hot_outflows[:4]:
        signals.append({
            "type": "热度资金背离",
            "level": "中",
            "target": row["name"],
            "message": f"热度 {row['score']:.1f}，但主力净流入 {row['net_inflow']:.2f} 亿元。",
        })

    return signals[:14] or [{
        "type": "系统",
        "level": "低",
        "target": "市场",
        "message": "早盘未发现显著放量异动或风格切换信号。",
    }]


def build_style_switch_signals(styles, previous_styles, spreads):
    signals = []
    top = styles[0] if styles else None
    previous_top = previous_styles[0] if previous_styles else None
    if top and previous_top and top["name"] != previous_top["name"]:
        signals.append({
            "type": "风格切换",
            "level": "高",
            "target": top["name"],
            "message": f"风格首位由昨日的 {previous_top['name']} 切换至早盘 {top['name']}。",
        })
    for row in styles:
        if row.get("day_delta") is not None and row["day_delta"] >= 8:
            signals.append({
                "type": "风格升温",
                "level": "中",
                "target": row["name"],
                "message": f"{row['name']}较昨日收盘升温 {row['day_delta']:.1f} 分，成交进度 {fmt_optional(row.get('amount_progress'), 1, '%')}。",
            })
        if row.get("day_delta") is not None and row["day_delta"] <= -8:
            signals.append({
                "type": "风格降温",
                "level": "中",
                "target": row["name"],
                "message": f"{row['name']}较昨日收盘降温 {abs(row['day_delta']):.1f} 分。",
            })
    for row in spreads[:3]:
        if row["abs_value"] >= 25:
            signals.append({
                "type": "风格价差",
                "level": "中",
                "target": row["name"],
                "message": f"{row['direction']}，温差 {row['value']:.1f} 分。",
            })
    return signals[:10] or [{
        "type": "风格观察",
        "level": "低",
        "target": "风格",
        "message": "早盘风格排序较昨日未出现显著切换。",
    }]


def build_summary(indices, styles, industries, overview, breadth, style_switches, anomalies):
    top_index = indices[0]
    top_style = styles[0]
    top_industry = industries[0] if industries else None
    key_switch = style_switches[0] if style_switches else None
    headline = (
        f"午盘快照：全A成交 {overview['all_a_amount']:.0f} 亿，"
        f"按早盘节奏折算约为昨日 {overview['projected_vs_last']:.1f}%；"
        f"最热风格为 {top_style['name']}，板块扩散为 {breadth['status']['label']}。"
    )
    bullets = [
        f"{top_index['name']}早盘温度 {top_index['score']:.1f}，较昨日收盘 {signed(top_index['day_delta'])}，涨跌 {top_index['pct_chg'] if top_index['pct_chg'] is not None else '-'}%。",
        f"{top_style['name']}风格温度 {top_style['score']:.1f}，较昨日收盘 {signed(top_style['day_delta'])}。",
        f"全A上涨家数 {overview['up_count']}，下跌家数 {overview['down_count']}，上涨占比 {overview['up_ratio']}%。",
        f"行业/主题扩散分 {breadth['diffusion_score']:.1f}，热点数量 {breadth['hot_count']} 个，主力净流入合计 {breadth['total_net_inflow']} 亿元。",
    ]
    if top_industry:
        bullets.append(f"早盘热度最高方向为 {top_industry['name']}，状态：{top_industry['state']}，板块热度 {top_industry['score']:.1f}。")
    if key_switch:
        bullets.append(f"风格提示：{key_switch['message']}")
    if anomalies:
        bullets.append(f"异动提示：{anomalies[0]['target']}，{anomalies[0]['message']}")
    return {"headline": headline, "bullets": bullets}


def write_morning_report(dashboard):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"morning_{dashboard['trade_date']}.md"
    overview = dashboard["market_overview"]
    lines = [
        f"# 午盘投研快照 {dashboard['trade_date']}",
        "",
        f"生成时间：{dashboard['generated_at']}",
        f"基准收盘日：{overview['last_trade_date']}",
        "",
        "## 核心摘要",
        "",
        dashboard["summary"]["headline"],
        "",
    ]
    lines.extend([f"- {item}" for item in dashboard["summary"]["bullets"]])
    lines.extend(["", "## 早盘市场概览", ""])
    lines.extend([
        f"- 全A成交：{overview['all_a_amount']} 亿，昨日全日：{overview['last_full_day_amount']} 亿，早盘进度：{overview['amount_progress']}%。",
        f"- 折算全日成交：{overview['projected_full_day_amount']} 亿，约为昨日 {overview['projected_vs_last']}%。",
        f"- 上涨/下跌/平盘家数：{overview['up_count']} / {overview['down_count']} / {overview['flat_count']}。",
    ])
    lines.extend(["", "## 风格切换", ""])
    lines.extend([
        f"- [{row['level']}] {row['type']} - {row['target']}：{row['message']}"
        for row in dashboard["style_switch_signals"]
    ])
    lines.extend(["", "## 早盘异动", ""])
    lines.extend([
        f"- [{row['level']}] {row['type']} - {row['target']}：{row['message']}"
        for row in dashboard["anomaly_signals"]
    ])
    lines.extend(["", "## 宽基温度", ""])
    lines.extend([
        f"- {idx + 1}. {row['name']}：温度 {row['score']}，较昨日 {signed(row['day_delta'])}，涨跌 {row['pct_chg'] if row['pct_chg'] is not None else '-'}%，早盘成交 {row['morning_amount']} 亿。"
        for idx, row in enumerate(dashboard["morning_indices"])
    ])
    lines.extend(["", "## 行业/主题热力", ""])
    lines.extend([
        f"- {idx + 1}. {row['name']}：热度 {row['score']}，涨跌 {row['ret_5d'] if row['ret_5d'] is not None else '-'}%，主力净流入 {row['net_inflow'] if row['net_inflow'] is not None else '-'} 亿，状态：{row['state']}"
        for idx, row in enumerate(dashboard["industry_heatmap"][:12])
    ])
    lines.extend(["", "## 数据质量", ""])
    lines.extend([
        f"- {row['item']}：{row['status']}，{row['detail']}"
        for row in dashboard["data_quality"]
    ])
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_morning_dashboard():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    MORNING_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    raw_congestion = load_congestion_data()
    congestion = filter_valid_congestion_dates(raw_congestion)
    live_indices = fetch_live_indices()
    all_a = fetch_all_a_snapshot()
    overview = build_market_overview(congestion, all_a)
    indices, previous_indices = build_morning_indices(congestion, live_indices, overview["all_a_amount"])
    styles, previous_styles = build_morning_style_rotation(indices, previous_indices)
    industries, industry_source = try_fetch_industries()
    industry_quality = "ok" if industries else "proxy"
    if not industries:
        industries = build_proxy_industries(indices)
    breadth = build_market_breadth(industries)
    spreads = build_style_spreads(indices)
    divergences = build_divergence_signals(indices, industries, breadth, spreads)
    style_switches = build_style_switch_signals(styles, previous_styles, spreads)
    anomalies = build_anomaly_signals(indices, industries)
    alerts = build_risk_alerts(indices, industries, [], breadth, spreads)
    summary = build_summary(indices, styles, industries, overview, breadth, style_switches, anomalies)
    trade_date = trade_date_text()
    data_health = load_source_health()

    dashboard = {
        "version": "0.1.0",
        "session": "morning",
        "generated_at": now_text(),
        "trade_date": trade_date,
        "source": {
            "wide_index": "东方财富实时指数 + congestion_data.json估算",
            "industry": industry_source,
            "industry_quality": industry_quality,
            "projection_factor": PROJECTION_FACTOR,
            "raw_latest_date": raw_congestion["ALL_DATES"][-1],
            "close_base_date": congestion["ALL_DATES"][-1],
        },
        "summary": summary,
        "market_overview": overview,
        "morning_indices": indices,
        "style_rotation": styles,
        "previous_style_rotation": previous_styles,
        "style_spreads": spreads,
        "industry_heatmap": industries,
        "market_breadth": breadth,
        "style_switch_signals": style_switches,
        "anomaly_signals": anomalies,
        "divergence_signals": divergences,
        "risk_alerts": alerts,
        "data_health": data_health,
        "data_health_summary": summarize_source_health(data_health),
        "data_quality": [
            {"item": "实时指数", "status": "realtime", "detail": f"东方财富实时指数返回 {len(live_indices)} / {len(INDEX_META)} 个监控指数。"},
            {"item": "全A成交", "status": "realtime", "detail": f"基于东方财富 {overview['stock_count']} 只A股成交额汇总。"},
            {"item": "午盘折算", "status": "estimate", "detail": f"使用早盘成交 x {PROJECTION_FACTOR} 估算全日成交，仅用于午盘节奏判断。"},
            {"item": "行业热力", "status": industry_quality, "detail": industry_source},
            {"item": "微盘指数", "status": "mixed", "detail": "800007 Choice微盘股指数实时成交来自东方财富，拥挤分母沿用本地最近缓存。"},
            {"item": "收盘基准", "status": "ok", "detail": f"原始最新日 {raw_congestion['ALL_DATES'][-1]}；午盘比较基准为有效收盘日 {congestion['ALL_DATES'][-1]}。"},
        ],
    }
    report_path = write_morning_report(dashboard)
    snapshot_path = MORNING_HISTORY_DIR / f"morning_dashboard_{trade_date}.json"
    dashboard["report"] = {
        "morning_markdown": str(report_path.relative_to(SYSTEM_ROOT)).replace("\\", "/")
    }
    dashboard["history"] = {
        "snapshot": str(snapshot_path.relative_to(SYSTEM_ROOT)).replace("\\", "/")
    }

    snapshot_path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    MORNING_CACHE_JSON.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    with MORNING_CACHE_JS.open("w", encoding="utf-8") as f:
        f.write("window.MORNING_DASHBOARD_DATA = ")
        json.dump(dashboard, f, ensure_ascii=False)
        f.write(";\n")
    return dashboard


if __name__ == "__main__":
    result = build_morning_dashboard()
    print(json.dumps({
        "generated_at": result["generated_at"],
        "trade_date": result["trade_date"],
        "all_a_amount": result["market_overview"]["all_a_amount"],
        "top_style": result["style_rotation"][0]["name"],
        "top_industry": result["industry_heatmap"][0]["name"] if result["industry_heatmap"] else "-",
        "style_switch_signals": len(result["style_switch_signals"]),
        "anomaly_signals": len(result["anomaly_signals"]),
        "report": result["report"]["morning_markdown"],
    }, ensure_ascii=False, indent=2))
