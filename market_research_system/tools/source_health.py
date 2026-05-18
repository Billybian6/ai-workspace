import json

from research_db import CACHE_DIR


HEALTH_JSON = CACHE_DIR / "data_source_health.json"

STATUS_ORDER = {
    "failed": 0,
    "partial": 1,
    "limited": 2,
    "fallback": 3,
    "cache": 4,
    "ok": 5,
}


def load_source_health(limit=40):
    if not HEALTH_JSON.exists():
        return []
    try:
        payload = json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("rows") or []
    rows = sorted(
        rows,
        key=lambda row: (
            STATUS_ORDER.get(row.get("status"), 2),
            row.get("indicator_key") or "",
            row.get("source") or "",
        ),
    )
    if limit:
        return rows[:limit]
    return rows


def summarize_source_health(rows):
    if not rows:
        return {"ok": 0, "partial": 0, "limited": 0, "failed": 0, "total": 0}
    result = {"ok": 0, "partial": 0, "limited": 0, "failed": 0, "total": len(rows)}
    for row in rows:
        status = row.get("status")
        if status in result:
            result[status] += 1
    return result
