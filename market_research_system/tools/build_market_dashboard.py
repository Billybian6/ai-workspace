import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd


SYSTEM_ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_ROOT = SYSTEM_ROOT.parent
CACHE_DIR = SYSTEM_ROOT / "data" / "cache"
CACHE_JSON = CACHE_DIR / "market_dashboard.json"
CACHE_JS = CACHE_DIR / "market_dashboard_data.js"
CONGESTION_JSON = WORKBENCH_ROOT / "congestion_data.json"
REPORT_DIR = SYSTEM_ROOT / "outputs" / "reports"
HISTORY_DIR = SYSTEM_ROOT / "data" / "history"


INDEX_META = {
    "000016": {"name": "上证50", "group": "大盘权重", "choice": "000016.SH"},
    "000300": {"name": "沪深300", "group": "大盘权重", "choice": "000300.SH"},
    "000905": {"name": "中证500", "group": "中盘", "choice": "000905.SH"},
    "000852": {"name": "中证1000", "group": "小盘", "choice": "000852.SH"},
    "000464": {"name": "中证2000", "group": "小盘", "choice": "931446.CSI"},
    "399006": {"name": "创业板指", "group": "成长科技", "choice": "399006.SZ"},
    "000688": {"name": "科创50", "group": "成长科技", "choice": "000688.SH"},
    "8841331": {"name": "中证红利", "group": "红利低波", "choice": "000922.SH"},
    "800007": {"name": "Choice微盘", "group": "微盘", "choice": "800007"},
}

STYLE_GROUPS = {
    "大盘权重": ["000016", "000300"],
    "中盘": ["000905"],
    "小盘": ["000852", "000464"],
    "成长科技": ["399006", "000688"],
    "红利低波": ["8841331"],
    "微盘": ["800007"],
}

INDUSTRY_CODES = {
    "801010.SI": "农林牧渔",
    "801030.SI": "基础化工",
    "801040.SI": "钢铁",
    "801050.SI": "有色金属",
    "801080.SI": "电子",
    "801110.SI": "家用电器",
    "801120.SI": "食品饮料",
    "801150.SI": "医药生物",
    "801160.SI": "公用事业",
    "801170.SI": "交通运输",
    "801180.SI": "房地产",
    "801730.SI": "电力设备",
    "801740.SI": "国防军工",
    "801750.SI": "计算机",
    "801760.SI": "通信",
    "801780.SI": "银行",
    "801790.SI": "非银金融",
    "801880.SI": "汽车",
    "801980.SI": "传媒",
}

EASTMONEY_BOARD_URL = (
    "https://push2delay.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz=120&po=1&np=1"
    "&ut=bd1d9ddb04089700cf9c27f6f7426281"
    "&fltt=2&invt=2&fid=f3&fs=m:90+t:2"
    "&fields=f12,f14,f2,f3,f4,f5,f6,f20,f21,f62,f128,f136,f140,f141,f152"
)


def load_congestion_data():
    if not CONGESTION_JSON.exists():
        raise FileNotFoundError(f"Missing {CONGESTION_JSON}")
    with CONGESTION_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def pct_rank(values, value):
    clean = [v for v in values if v is not None and not math.isnan(v)]
    if not clean:
        return None
    return sum(1 for v in clean if v <= value) / len(clean) * 100


def avg(values):
    clean = [v for v in values if v is not None and not math.isnan(v)]
    return sum(clean) / len(clean) if clean else None


def clamp(value, low=0, high=100):
    return max(low, min(high, value))


def get_json(url, timeout=20):
    try:
        import requests
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://quote.eastmoney.com/",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        pass

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def level(score):
    if score >= 70:
        return {"label": "高风险", "class": "danger"}
    if score >= 50:
        return {"label": "偏热", "class": "warning"}
    if score >= 30:
        return {"label": "中性", "class": "safe"}
    return {"label": "偏冷", "class": "info"}


def compute_composite(derived):
    congestion_score = min(derived["congestion"] / 12 * 100, 100)
    ratio_score = min(derived["turnover_ratio"] / 25 * 100, 100)
    return congestion_score * 0.5 + ratio_score * 0.25 + derived["percentile"] * 0.25


def compute_index_series(data):
    dates = data["ALL_DATES"]
    market = data["MARKET_TOTAL"]
    raw = data["RAW_DATA"]
    top5 = data["TOP5_RATIO"]
    result = []

    for code, rows in raw.items():
        meta = INDEX_META.get(code)
        if not meta:
            continue

        points = []
        for date in dates:
            r = rows.get(date)
            m = market.get(date)
            if not r or not m:
                continue
            derived = {
                "congestion": r["turnover"] / r["free_mv"] * 100 if r["free_mv"] else 0,
                "turnover_ratio": r["turnover"] / m["turnover"] * 100 if m["turnover"] else 0,
                "turnover_rate": r["turnover"] / r["free_mv"] * 100 if r["free_mv"] else 0,
                "percentile": r.get("percentile", 0),
                "turnover": r["turnover"],
                "top5": top5.get(date),
            }
            points.append({
                "date": date,
                "derived": derived,
                "score": compute_composite(derived),
            })

        if not points:
            continue
        last = points[-1]
        prev = points[-2] if len(points) > 1 else None
        first = points[0]
        recent5 = points[-5:]
        day_delta = last["score"] - prev["score"] if prev else 0
        range_delta = last["score"] - first["score"]
        recent_delta = last["score"] - avg([p["score"] for p in recent5])
        result.append({
            "id": code,
            "name": meta["name"],
            "group": meta["group"],
            "date": last["date"],
            "score": round(last["score"], 1),
            "day_delta": round(day_delta, 1),
            "range_delta": round(range_delta, 1),
            "recent_delta": round(recent_delta, 1),
            "congestion": round(last["derived"]["congestion"], 2),
            "turnover_ratio": round(last["derived"]["turnover_ratio"], 2),
            "turnover_rate": round(last["derived"]["turnover_rate"], 2),
            "percentile": round(last["derived"]["percentile"], 0),
            "turnover": round(last["derived"]["turnover"], 0),
            "level": level(last["score"]),
            "coverage": f"{len(points)}/{len(dates)}",
            "series": [{"date": p["date"], "score": round(p["score"], 1)} for p in points],
        })

    return sorted(result, key=lambda x: x["score"], reverse=True)


def build_style_rotation(indices):
    rows = []
    by_id = {row["id"]: row for row in indices}
    for group, codes in STYLE_GROUPS.items():
        members = [by_id[c] for c in codes if c in by_id]
        if not members:
            continue
        score = avg([m["score"] for m in members])
        day_delta = avg([m["day_delta"] for m in members])
        range_delta = avg([m["range_delta"] for m in members])
        rows.append({
            "name": group,
            "members": "、".join(m["name"] for m in members),
            "score": round(score, 1),
            "day_delta": round(day_delta, 1),
            "range_delta": round(range_delta, 1),
            "level": level(score),
        })
    return sorted(rows, key=lambda x: x["score"], reverse=True)


def build_market_state(data, indices, styles):
    last_date = data["ALL_DATES"][-1]
    market = data["MARKET_TOTAL"][last_date]
    top5 = data["TOP5_RATIO"][last_date]
    top = indices[0]
    avg_score = avg([row["score"] for row in indices])
    hot_count = len([row for row in indices if row["score"] >= 50])
    rising_count = len([row for row in indices if row["day_delta"] > 1])
    turnover_score = clamp((market["turnover"] - 15000) / 25000 * 100)
    crowding_score = avg_score
    concentration_score = clamp((top5 - 35) / 15 * 100)
    rotation_score = clamp((hot_count / len(indices)) * 100 if indices else 0)
    risk_score = avg([crowding_score, concentration_score, max(top["score"], 0)])

    return [
        {
            "id": "regime",
            "name": "市场温度",
            "score": round(avg_score, 1),
            "status": level(avg_score),
            "detail": f"宽基平均拥挤度 {avg_score:.1f}，{hot_count} 个指数处于偏热以上。",
        },
        {
            "id": "liquidity",
            "name": "流动性",
            "score": round(turnover_score, 1),
            "status": level(turnover_score),
            "detail": f"全A成交额 {market['turnover']:.0f} 亿，换手约 {market['turnover']/market['free_float_mv']*100:.2f}%。",
        },
        {
            "id": "concentration",
            "name": "成交集中度",
            "score": round(concentration_score, 1),
            "status": level(concentration_score),
            "detail": f"前5%个股成交占比 {top5:.1f}%，45%附近为拥挤交易临界区。",
        },
        {
            "id": "rotation",
            "name": "风格扩散",
            "score": round(rotation_score, 1),
            "status": level(rotation_score),
            "detail": f"{rising_count} 个宽基指数较前日升温，最热风格为 {styles[0]['name']}。",
        },
        {
            "id": "risk",
            "name": "风险温度",
            "score": round(risk_score, 1),
            "status": level(risk_score),
            "detail": f"当前最高拥挤方向为 {top['name']}，综合分 {top['score']:.1f}。",
        },
    ]


def try_fetch_industries():
    """Best-effort Choice fetch. Returns rows or an empty list."""
    eastmoney_rows, eastmoney_source = try_fetch_eastmoney_boards()
    if eastmoney_rows:
        return eastmoney_rows, eastmoney_source

    if not bool(int(str(os.environ.get("ENABLE_CHOICE_INDUSTRY_FETCH", "0")))):
        return [], "行业直连未启用，使用宽基代理热力"

    try:
        emquant_path = Path(r"C:\Users\bianzhengzhi\Desktop\EMQuantAPI_Python\EMQuantAPI_Python\python3")
        if emquant_path.exists():
            sys.path.append(str(emquant_path))
        from EmQuantAPI import c
    except Exception:
        return [], "EmQuantAPI unavailable"

    try:
        res = c.start()
        if getattr(res, "ErrorCode", -1) != 0:
            return [], f"Choice login failed: {getattr(res, 'ErrorMsg', '')}"

        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
        rows = []
        for code, name in INDUSTRY_CODES.items():
            df = c.csd(code, "CLOSE,AMOUNT", start, end, "Ispandas=1")
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            df = df.sort_values("DATES")
            close = df["CLOSE"].astype(float).tolist()
            amount = df["AMOUNT"].astype(float).tolist()
            if len(close) < 21:
                continue
            ret_5d = close[-1] / close[-6] * 100 - 100 if len(close) >= 6 and close[-6] else 0
            ret_20d = close[-1] / close[-21] * 100 - 100 if close[-21] else 0
            amount_yi = amount[-1] / 1e8
            amount_pct = pct_rank([v / 1e8 for v in amount], amount_yi) or 0
            momentum_score = clamp((ret_20d + 10) / 25 * 100)
            heat = momentum_score * 0.55 + amount_pct * 0.45
            rows.append({
                "id": code,
                "name": name,
                "score": round(heat, 1),
                "ret_5d": round(ret_5d, 2),
                "ret_20d": round(ret_20d, 2),
                "amount": round(amount_yi, 0),
                "amount_pct": round(amount_pct, 0),
                "state": classify_industry(heat, ret_20d, amount_pct),
                "quality": "ok",
            })
        c.stop()
        return sorted(rows, key=lambda x: x["score"], reverse=True), "Choice/EmQuant"
    except Exception as exc:
        try:
            c.stop()
        except Exception:
            pass
        return [], f"Choice fetch failed: {exc}"


def try_fetch_eastmoney_boards():
    try:
        payload = get_json(EASTMONEY_BOARD_URL)
        boards = payload.get("data", {}).get("diff", []) or []
        if not boards:
            return [], "东方财富板块API无数据"

        amount_values = [float(row.get("f6") or 0) / 1e8 for row in boards]
        inflow_values = [float(row.get("f62") or 0) / 1e8 for row in boards]
        rows = []
        for row in boards:
            pct_chg = float(row.get("f3") or 0)
            amount_yi = float(row.get("f6") or 0) / 1e8
            net_inflow_yi = float(row.get("f62") or 0) / 1e8
            amount_rank = pct_rank(amount_values, amount_yi) or 0
            inflow_rank = pct_rank(inflow_values, net_inflow_yi) or 0
            gain_score = clamp((pct_chg + 3) / 8 * 100)
            heat = gain_score * 0.45 + amount_rank * 0.35 + inflow_rank * 0.20
            rows.append({
                "id": row.get("f12"),
                "name": row.get("f14"),
                "score": round(heat, 1),
                "ret_5d": round(pct_chg, 2),
                "ret_20d": None,
                "amount": round(amount_yi, 0),
                "amount_pct": round(amount_rank, 0),
                "net_inflow": round(net_inflow_yi, 2),
                "net_inflow_pct": round(inflow_rank, 0),
                "leader": row.get("f128") or "-",
                "leader_change": round(float(row.get("f136") or 0), 2),
                "state": classify_board(heat, pct_chg, amount_rank, net_inflow_yi),
                "quality": "ok",
            })
        return sorted(rows, key=lambda x: x["score"], reverse=True)[:80], "东方财富板块API"
    except Exception as exc:
        return [], f"东方财富板块API失败: {exc}"


def classify_board(score, pct_chg, amount_pct, net_inflow):
    if pct_chg > 2 and amount_pct >= 70 and net_inflow > 0:
        return "放量领涨"
    if pct_chg > 1 and amount_pct < 60:
        return "温和走强"
    if pct_chg < -1 and amount_pct >= 70:
        return "放量走弱"
    if score >= 75:
        return "高热度"
    if score <= 30:
        return "低热度"
    return "中性观察"


def classify_industry(score, ret_20d, amount_pct):
    if ret_20d > 5 and amount_pct < 70:
        return "强趋势低拥挤"
    if ret_20d > 5 and amount_pct >= 70:
        return "强趋势高热度"
    if ret_20d < -3 and amount_pct >= 70:
        return "放量走弱"
    if score >= 70:
        return "高热度"
    if score <= 30:
        return "低热度"
    return "中性观察"


def build_proxy_industries(indices):
    proxy = [
        ("科技成长", "399006", 0.8),
        ("科创硬科技", "000688", 1.0),
        ("红利低波", "8841331", 0.6),
        ("大金融权重", "000300", 0.55),
        ("中小盘制造", "000905", 0.7),
        ("小微盘主题", "800007", 1.0),
        ("地产链观察", "000852", 0.45),
        ("周期资源观察", "000464", 0.5),
    ]
    by_id = {row["id"]: row for row in indices}
    rows = []
    for name, ref, beta in proxy:
        base = by_id.get(ref)
        if not base:
            continue
        score = clamp(base["score"] * beta + 15)
        rows.append({
            "id": f"proxy_{ref}",
            "name": name,
            "score": round(score, 1),
            "ret_5d": None,
            "ret_20d": None,
            "amount": None,
            "amount_pct": round(base["percentile"], 0),
            "net_inflow": None,
            "net_inflow_pct": None,
            "leader": "-",
            "leader_change": None,
            "state": "代理观察",
            "quality": "proxy",
        })
    return sorted(rows, key=lambda x: x["score"], reverse=True)


def as_number(value):
    if isinstance(value, (int, float)) and not math.isnan(value):
        return value
    return None


def breadth_status(score):
    if score >= 70:
        return {"label": "强扩散", "class": "danger"}
    if score >= 55:
        return {"label": "温和扩散", "class": "warning"}
    if score >= 40:
        return {"label": "结构分化", "class": "safe"}
    return {"label": "扩散收缩", "class": "info"}


def build_market_breadth(industries):
    total = len(industries)
    if not total:
        return {
            "coverage": 0,
            "diffusion_score": 0,
            "status": breadth_status(0),
            "positive_count": 0,
            "negative_count": 0,
            "flat_count": 0,
            "positive_ratio": 0,
            "hot_count": 0,
            "hot_ratio": 0,
            "cold_count": 0,
            "net_inflow_positive_ratio": None,
            "total_net_inflow": None,
            "dispersion_score": None,
            "top5_amount_share": None,
            "top_boards": [],
            "bottom_boards": [],
            "note": "行业/主题样本为空，扩散判断暂不可用。",
        }

    ret_rows = [row for row in industries if as_number(row.get("ret_5d")) is not None]
    if ret_rows:
        positive_count = len([row for row in ret_rows if row["ret_5d"] > 0])
        negative_count = len([row for row in ret_rows if row["ret_5d"] < 0])
        flat_count = len(ret_rows) - positive_count - negative_count
        positive_ratio = positive_count / len(ret_rows) * 100
    else:
        positive_count = len([row for row in industries if row["score"] >= 50])
        negative_count = len([row for row in industries if row["score"] < 40])
        flat_count = total - positive_count - negative_count
        positive_ratio = positive_count / total * 100

    hot_count = len([row for row in industries if row["score"] >= 70])
    cold_count = len([row for row in industries if row["score"] <= 30])
    hot_ratio = hot_count / total * 100

    inflow_rows = [row for row in industries if as_number(row.get("net_inflow")) is not None]
    if inflow_rows:
        inflow_positive_ratio = len([row for row in inflow_rows if row["net_inflow"] > 0]) / len(inflow_rows) * 100
        total_net_inflow = sum(row["net_inflow"] for row in inflow_rows)
    else:
        inflow_positive_ratio = None
        total_net_inflow = None

    amount_pairs = [
        (row, as_number(row.get("amount")))
        for row in industries
        if as_number(row.get("amount")) is not None and row["amount"] > 0
    ]
    amount_total = sum(value for _, value in amount_pairs)
    if amount_total:
        top5_amount = sum(value for _, value in sorted(amount_pairs, key=lambda x: x[1], reverse=True)[:5])
        top5_amount_share = top5_amount / amount_total * 100
    else:
        top5_amount_share = None

    sorted_by_score = sorted(industries, key=lambda x: x["score"], reverse=True)
    top_avg = avg([row["score"] for row in sorted_by_score[:10]])
    bottom_avg = avg([row["score"] for row in sorted_by_score[-10:]])
    dispersion = top_avg - bottom_avg if top_avg is not None and bottom_avg is not None else None
    concentration_penalty = (100 - top5_amount_share) if top5_amount_share is not None else 55
    inflow_component = inflow_positive_ratio if inflow_positive_ratio is not None else 50
    diffusion_score = (
        positive_ratio * 0.45
        + hot_ratio * 0.25
        + inflow_component * 0.20
        + concentration_penalty * 0.10
    )

    if top5_amount_share is not None and top5_amount_share >= 45:
        note = "成交明显集中在头部方向，扩散质量需要打折观察。"
    elif positive_ratio >= 60 and hot_count >= 5:
        note = "上涨方向较多且热点数量充足，市场扩散处在较活跃状态。"
    elif positive_ratio < 45:
        note = "板块上涨覆盖不足，指数热度需要确认是否有更广泛跟随。"
    else:
        note = "板块表现分化，适合继续跟踪强势方向能否外溢。"

    return {
        "coverage": total,
        "diffusion_score": round(diffusion_score, 1),
        "status": breadth_status(diffusion_score),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "flat_count": flat_count,
        "positive_ratio": round(positive_ratio, 1),
        "hot_count": hot_count,
        "hot_ratio": round(hot_ratio, 1),
        "cold_count": cold_count,
        "net_inflow_positive_ratio": round(inflow_positive_ratio, 1) if inflow_positive_ratio is not None else None,
        "total_net_inflow": round(total_net_inflow, 2) if total_net_inflow is not None else None,
        "dispersion_score": round(dispersion, 1) if dispersion is not None else None,
        "top5_amount_share": round(top5_amount_share, 1) if top5_amount_share is not None else None,
        "top_boards": sorted_by_score[:5],
        "bottom_boards": sorted(industries, key=lambda x: x["score"])[:5],
        "note": note,
    }


def build_style_spreads(indices):
    by_id = {row["id"]: row for row in indices}

    def basket(codes):
        return avg([by_id[code]["score"] for code in codes if code in by_id])

    def spread_row(name, long_codes, short_codes, positive_label, negative_label):
        long_score = basket(long_codes)
        short_score = basket(short_codes)
        if long_score is None or short_score is None:
            return None
        value = long_score - short_score
        abs_value = abs(value)
        if abs_value >= 25:
            status = {"label": "极端分化", "class": "danger"}
        elif abs_value >= 12:
            status = {"label": "明显占优", "class": "warning"}
        else:
            status = {"label": "相对均衡", "class": "safe"}
        if value > 1:
            direction = positive_label
        elif value < -1:
            direction = negative_label
        else:
            direction = "相对均衡"
        return {
            "name": name,
            "value": round(value, 1),
            "abs_value": round(abs_value, 1),
            "direction": direction,
            "status": status,
            "long_score": round(long_score, 1),
            "short_score": round(short_score, 1),
        }

    rows = [
        spread_row("成长 - 红利", ["399006", "000688"], ["8841331"], "成长占优", "红利占优"),
        spread_row("小盘 - 大盘", ["000852", "000464", "800007"], ["000016", "000300"], "小盘占优", "大盘占优"),
        spread_row("中盘 - 大盘", ["000905"], ["000016", "000300"], "中盘占优", "大盘占优"),
        spread_row("微盘 - 中小盘", ["800007"], ["000905", "000852", "000464"], "微盘占优", "微盘落后"),
        spread_row("创业板 - 科创50", ["399006"], ["000688"], "创业板占优", "科创50占优"),
    ]
    return sorted([row for row in rows if row], key=lambda x: x["abs_value"], reverse=True)


def build_divergence_signals(indices, industries, breadth, spreads):
    signals = []
    by_id = {row["id"]: row for row in indices}
    top_index = indices[0] if indices else None
    top_industry = industries[0] if industries else None
    avg_index_score = avg([row["score"] for row in indices])

    if top_index and top_index["score"] >= 70 and breadth["diffusion_score"] < 55:
        signals.append({
            "type": "拥挤未扩散",
            "level": "高",
            "title": "宽基高热但板块扩散不足",
            "message": f"{top_index['name']}拥挤度 {top_index['score']:.1f}，板块扩散分仅 {breadth['diffusion_score']:.1f}，需要警惕少数方向拉动。",
            "target": top_index["name"],
        })

    if breadth["positive_ratio"] >= 65 and avg_index_score is not None and avg_index_score < 50:
        signals.append({
            "type": "扩散领先",
            "level": "中",
            "title": "板块扩散领先于宽基温度",
            "message": f"行业上涨占比 {breadth['positive_ratio']:.1f}%，但宽基均温 {avg_index_score:.1f}，可能处在结构行情向指数传导阶段。",
            "target": "行业/主题",
        })

    if top_industry and top_industry.get("score", 0) >= 75 and as_number(top_industry.get("net_inflow")) is not None and top_industry["net_inflow"] < 0:
        signals.append({
            "type": "热度资金背离",
            "level": "中",
            "title": "热点热度较高但资金净流出",
            "message": f"{top_industry['name']}热度 {top_industry['score']:.1f}，主力净流入 {top_industry['net_inflow']:.2f} 亿元，需观察持续性。",
            "target": top_industry["name"],
        })

    if breadth["total_net_inflow"] is not None and breadth["total_net_inflow"] < 0 and breadth["positive_ratio"] >= 55:
        signals.append({
            "type": "价格资金背离",
            "level": "中",
            "title": "上涨覆盖尚可但资金合计流出",
            "message": f"行业上涨占比 {breadth['positive_ratio']:.1f}%，主力净流入合计 {breadth['total_net_inflow']:.2f} 亿元。",
            "target": "行业/主题",
        })

    for row in spreads[:2]:
        if row["abs_value"] >= 25:
            signals.append({
                "type": "风格极端",
                "level": "中",
                "title": f"{row['name']}温差处在极端区间",
                "message": f"{row['direction']}，温差 {row['value']:.1f} 分。极端价差后要跟踪均值回归或强化突破。",
                "target": row["name"],
            })

    micro = by_id.get("800007")
    small_basket = avg([by_id[code]["score"] for code in ("000905", "000852", "000464") if code in by_id])
    if micro and small_basket is not None and small_basket - micro["score"] >= 18:
        signals.append({
            "type": "微盘背离",
            "level": "中",
            "title": "微盘显著弱于中小盘",
            "message": f"Choice微盘温度 {micro['score']:.1f}，中小盘篮子 {small_basket:.1f}，说明风险偏好并未完全下沉。",
            "target": "Choice微盘",
        })

    if not signals:
        signals.append({
            "type": "系统",
            "level": "低",
            "title": "暂无显著背离",
            "message": "宽基、行业扩散与风格价差尚未出现需要单独降权的背离信号。",
            "target": "市场",
        })
    return signals[:8]


def build_risk_alerts(indices, industries, state, breadth=None, spreads=None):
    alerts = []
    for row in indices:
        if row["score"] >= 70:
            alerts.append({
                "type": "高拥挤",
                "target": row["name"],
                "level": "高",
                "message": f"综合拥挤度 {row['score']:.1f}，换手分位 {row['percentile']:.0f}%。",
            })
        if row["day_delta"] >= 4:
            alerts.append({
                "type": "快速升温",
                "target": row["name"],
                "level": "中",
                "message": f"较前日升温 {row['day_delta']:.1f} 分，需观察成交是否继续扩张。",
            })
    for row in industries[:5]:
        if row["score"] >= 75:
            alerts.append({
                "type": "行业热度",
                "target": row["name"],
                "level": "中",
                "message": f"行业热度 {row['score']:.1f}，状态：{row['state']}。",
            })
    if breadth:
        if breadth["diffusion_score"] < 40:
            alerts.append({
                "type": "扩散收缩",
                "target": "行业/主题",
                "level": "中",
                "message": f"板块扩散分 {breadth['diffusion_score']:.1f}，上涨覆盖 {breadth['positive_ratio']:.1f}%，指数强度需要降权。",
            })
        if breadth.get("top5_amount_share") is not None and breadth["top5_amount_share"] >= 45:
            alerts.append({
                "type": "成交集中",
                "target": "行业/主题",
                "level": "中",
                "message": f"前五大成交方向占比 {breadth['top5_amount_share']:.1f}%，热点集中度偏高。",
            })
    for row in (spreads or [])[:2]:
        if row["abs_value"] >= 25:
            alerts.append({
                "type": "风格分化",
                "target": row["name"],
                "level": "中",
                "message": f"{row['direction']}，温差 {row['value']:.1f} 分，需跟踪是否触发再平衡。",
            })
    if not alerts:
        alerts.append({
            "type": "系统",
            "target": "市场",
            "level": "低",
            "message": "未发现极端拥挤或快速升温信号。",
        })
    return alerts[:10]


def build_summary(indices, styles, state, industries, breadth, spreads, divergences):
    top_index = indices[0]
    top_style = styles[0]
    top_industry = industries[0] if industries else None
    top_spread = spreads[0] if spreads else None
    key_divergence = next((row for row in divergences if row["level"] in ("高", "中")), None)
    risk_card = next(row for row in state if row["id"] == "risk")
    headline = (
        f"市场风险温度 {risk_card['score']:.1f}，最热宽基为 {top_index['name']}，"
        f"最热风格为 {top_style['name']}，板块扩散为 {breadth['status']['label']}。"
    )
    bullets = [
        f"{top_index['name']}综合拥挤度 {top_index['score']:.1f}，较前日 {signed(top_index['day_delta'])}。",
        f"{top_style['name']}组均值 {top_style['score']:.1f}，组内包含 {top_style['members']}。",
        f"行业/主题上涨覆盖 {breadth['positive_ratio']:.1f}%，热点数量 {breadth['hot_count']} 个，扩散分 {breadth['diffusion_score']:.1f}。",
    ]
    if top_industry:
        bullets.append(f"行业/主题热度最高为 {top_industry['name']}，状态：{top_industry['state']}。")
    if top_spread:
        bullets.append(f"最大风格温差为 {top_spread['name']}：{top_spread['direction']}，温差 {top_spread['value']:.1f} 分。")
    if key_divergence:
        bullets.append(f"关键背离：{key_divergence['title']}。")
    bullets.append("若高拥挤方向继续放量但价格动量钝化，应提高对退潮信号的敏感度。")
    return {"headline": headline, "bullets": bullets}


def signed(value):
    if value is None:
        return "-"
    if abs(value) < 0.05:
        return "持平"
    return f"{'升' if value > 0 else '降'}{abs(value):.1f}"


def write_history_snapshot(dashboard):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = HISTORY_DIR / f"market_dashboard_{dashboard['trade_date']}.json"
    snapshot_path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot_path


def write_daily_report(dashboard):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"daily_{dashboard['trade_date']}.md"
    top_indices = dashboard["wide_indices"][:5]
    top_styles = dashboard["style_rotation"][:3]
    top_industries = dashboard["industry_heatmap"][:10]
    alerts = dashboard["risk_alerts"]
    breadth = dashboard["market_breadth"]
    spreads = dashboard["style_spreads"][:5]
    divergences = dashboard["divergence_signals"]

    lines = [
        f"# 市场投研日报 {dashboard['trade_date']}",
        "",
        f"生成时间：{dashboard['generated_at']}",
        "",
        "## 核心摘要",
        "",
        dashboard["summary"]["headline"],
        "",
    ]
    lines.extend([f"- {item}" for item in dashboard["summary"]["bullets"]])
    lines.extend(["", "## 市场状态", ""])
    lines.extend([
        f"- {row['name']}：{row['score']}，{row['status']['label']}。{row['detail']}"
        for row in dashboard["market_state"]
    ])
    lines.extend(["", "## 市场宽度与扩散", ""])
    lines.extend([
        f"- 扩散分：{breadth['diffusion_score']}，状态：{breadth['status']['label']}。",
        f"- 上涨覆盖：{breadth['positive_count']}/{breadth['coverage']}，占比 {breadth['positive_ratio']}%；热点数量：{breadth['hot_count']}。",
        f"- 主力净流入合计：{breadth['total_net_inflow'] if breadth['total_net_inflow'] is not None else '-'} 亿元；前五成交占比：{breadth['top5_amount_share'] if breadth['top5_amount_share'] is not None else '-'}%。",
        f"- 解释：{breadth['note']}",
    ])
    lines.extend(["", "## 风格价差", ""])
    lines.extend([
        f"- {row['name']}：{row['direction']}，温差 {row['value']} 分，状态：{row['status']['label']}"
        for row in spreads
    ])
    lines.extend(["", "## 背离信号", ""])
    lines.extend([
        f"- [{row['level']}] {row['title']}：{row['message']}"
        for row in divergences
    ])
    lines.extend(["", "## 风格轮动", ""])
    lines.extend([
        f"- {row['name']}：{row['score']}，较前日 {signed(row['day_delta'])}，成员：{row['members']}"
        for row in top_styles
    ])
    lines.extend(["", "## 宽基拥挤排名", ""])
    lines.extend([
        f"- {idx + 1}. {row['name']}：{row['score']}，拥挤度 {row['congestion']}%，换手分位 {row['percentile']}%"
        for idx, row in enumerate(top_indices)
    ])
    lines.extend(["", "## 行业/主题热力", ""])
    lines.extend([
        f"- {idx + 1}. {row['name']}：热度 {row['score']}，涨跌 {row['ret_5d'] if row['ret_5d'] is not None else '-'}%，状态：{row['state']}"
        for idx, row in enumerate(top_industries)
    ])
    lines.extend(["", "## 风险提示", ""])
    lines.extend([
        f"- [{row['level']}] {row['type']} - {row['target']}：{row['message']}"
        for row in alerts
    ])
    lines.extend(["", "## 数据质量", ""])
    lines.extend([
        f"- {row['item']}：{row['status']}，{row['detail']}"
        for row in dashboard["data_quality"]
    ])
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_dashboard():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    congestion = load_congestion_data()
    indices = compute_index_series(congestion)
    styles = build_style_rotation(indices)
    state = build_market_state(congestion, indices, styles)
    industries, industry_source = try_fetch_industries()
    industry_quality = "ok" if industries else "proxy"
    if not industries:
        industries = build_proxy_industries(indices)
        if industry_source == "Choice/EmQuant":
            industry_source = "Choice未返回有效行业数据，使用宽基代理热力"
    breadth = build_market_breadth(industries)
    spreads = build_style_spreads(indices)
    divergences = build_divergence_signals(indices, industries, breadth, spreads)
    alerts = build_risk_alerts(indices, industries, state, breadth, spreads)
    summary = build_summary(indices, styles, state, industries, breadth, spreads, divergences)
    trade_date = congestion["ALL_DATES"][-1]

    dashboard = {
        "version": "0.4.0",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": trade_date,
        "source": {
            "wide_index": "congestion_data.json",
            "industry": industry_source,
            "industry_quality": industry_quality,
        },
        "summary": summary,
        "market_state": state,
        "wide_indices": indices,
        "style_rotation": styles,
        "market_breadth": breadth,
        "style_spreads": spreads,
        "divergence_signals": divergences,
        "industry_heatmap": industries,
        "risk_alerts": alerts,
        "data_quality": [
            {"item": "宽基指数", "status": "ok", "detail": f"{len(indices)} 个指数，覆盖 {trade_date}。"},
            {"item": "行业热力", "status": industry_quality, "detail": industry_source},
            {"item": "市场宽度", "status": industry_quality, "detail": f"基于 {breadth['coverage']} 个行业/主题样本计算扩散、集中度与资金覆盖。"},
            {"item": "微盘指数", "status": "cache", "detail": "使用 800007 Choice微盘股指数；外部接口失败时读取本地缓存。"},
            {"item": "历史分位", "status": "mixed", "detail": "宽基更新脚本支持近3年分位；缓存页保留最新生成结果。"},
            {"item": "历史快照", "status": "ok", "detail": f"按交易日写入 data/history/market_dashboard_{trade_date}.json。"},
        ],
    }
    report_path = write_daily_report(dashboard)
    dashboard["report"] = {
        "daily_markdown": str(report_path.relative_to(SYSTEM_ROOT)).replace("\\", "/")
    }
    snapshot_path = HISTORY_DIR / f"market_dashboard_{dashboard['trade_date']}.json"
    dashboard["history"] = {
        "snapshot": str(snapshot_path.relative_to(SYSTEM_ROOT)).replace("\\", "/")
    }
    write_history_snapshot(dashboard)

    with CACHE_JSON.open("w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)
    with CACHE_JS.open("w", encoding="utf-8") as f:
        f.write("window.MARKET_DASHBOARD_DATA = ")
        json.dump(dashboard, f, ensure_ascii=False)
        f.write(";\n")
    return dashboard


if __name__ == "__main__":
    result = build_dashboard()
    print(json.dumps({
        "generated_at": result["generated_at"],
        "trade_date": result["trade_date"],
        "indices": len(result["wide_indices"]),
        "industries": len(result["industry_heatmap"]),
        "breadth_score": result["market_breadth"]["diffusion_score"],
        "divergence_signals": len(result["divergence_signals"]),
        "industry_source": result["source"]["industry"],
    }, ensure_ascii=False, indent=2))
