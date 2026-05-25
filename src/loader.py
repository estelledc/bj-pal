"""把 amap POI + UGC aspects + 路线缓存灌进 SQLite，建索引。

W1 D1 交付物。一次性跑，~5s 完成。

使用：
    python3 src/loader.py             # 重建整个 db
    python3 src/loader.py --check     # 只读统计

接口（其他模块通过 query_* 调用）：
    query_pois(keyword: str, category: str | None) -> list[dict]
    query_ugc(area_anchor: str | None, poi_name: str | None) -> list[dict]
    query_routes(scene_id: str, mode: str | None) -> list[dict]
"""

from __future__ import annotations  # 兼容 Python 3.9 的 PEP 604 语法

import argparse
import gzip
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB = ROOT / "bj_pal.db"

POI_FILE = DATA / "amap" / "merged" / "amap_beijing_pois_with_food_merged_20260503.jsonl"
UGC_FILE = DATA / "ugc" / "aspects.jsonl"  # 三套合并后的全量 UGC（manual_seed + v2 + v3）
ROUTES_FILE = DATA / "amap" / "routes" / "amap_beijing_route_planning_ugc_eval_v3_20260503.jsonl"
ROUTES_EXPANDED_FILE = DATA / "amap" / "routes" / "expanded_v2.jsonl"  # Task 1.4


SCHEMA = """
CREATE TABLE IF NOT EXISTS pois (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category_lv1 TEXT,
    category_lv2 TEXT,
    category_lv3 TEXT,
    typecode TEXT,
    district TEXT,
    business_area TEXT,
    address TEXT,
    longitude REAL,
    latitude REAL,
    rating REAL,
    avg_price REAL,
    open_time TEXT,
    phone TEXT,
    photos_json TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_pois_cat1 ON pois(category_lv1);
CREATE INDEX IF NOT EXISTS idx_pois_district ON pois(district);
CREATE INDEX IF NOT EXISTS idx_pois_lnglat ON pois(longitude, latitude);

CREATE TABLE IF NOT EXISTS ugc_aspects (
    record_id TEXT PRIMARY KEY,
    area_anchor TEXT,
    poi_name TEXT,
    aspect_type TEXT,
    sentiment TEXT,
    confidence REAL,
    time_bucket TEXT,
    needs_review INTEGER,
    evidence_summary TEXT,
    normalized_value_json TEXT,
    raw_json TEXT,
    weekend_afternoon_intensity REAL DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_ugc_anchor ON ugc_aspects(area_anchor);
CREATE INDEX IF NOT EXISTS idx_ugc_poi ON ugc_aspects(poi_name);
CREATE INDEX IF NOT EXISTS idx_ugc_aspect ON ugc_aspects(aspect_type);

CREATE TABLE IF NOT EXISTS routes (
    cache_key TEXT PRIMARY KEY,
    scene_id TEXT,
    leg_id TEXT,
    mode TEXT,
    distance_m INTEGER,
    duration_s INTEGER,
    summary TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_routes_scene ON routes(scene_id);
CREATE INDEX IF NOT EXISTS idx_routes_leg ON routes(leg_id);
"""


def _open_jsonl(path: Path, required: bool = True):
    """打开 jsonl 或 jsonl.gz，按行 yield 解析后的 dict。

    required=False 时缺失文件不抛错，仅 stderr 警告 + 返回空 generator。
    用于可选数据源。
    """
    if not path.exists():
        if not required:
            print(f"[warn] 数据文件缺失但非必需，跳过：{path}", file=sys.stderr)
            return
        raise FileNotFoundError(f"数据文件缺失：{path}")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[warn] {path.name}:{line_num} JSON 解析失败：{e}", file=sys.stderr)


def load_pois(conn: sqlite3.Connection) -> int:
    """加载 amap POI 到 pois 表，返回行数。"""
    rows = []
    for poi in _open_jsonl(POI_FILE):
        photos = poi.get("photos") or []
        # photos 字段在 amap 数据里有时是 list，有时是 [] 空字符串——统一存 JSON
        photos_json = json.dumps(photos, ensure_ascii=False) if photos else "[]"
        # amap 字段经常是 list/None/空字符串 混杂——所有字符串字段都过 _flatten_str
        rows.append((
            _flatten_str(poi.get("provider_poi_id") or poi.get("name")),  # id
            _flatten_str(poi.get("name")) or "",
            _flatten_str(poi.get("category_lv1")),
            _flatten_str(poi.get("category_lv2")),
            _flatten_str(poi.get("category_lv3")),
            _flatten_str(poi.get("typecode")),
            _flatten_str(poi.get("district")),
            _flatten_str(poi.get("business_area")),
            _flatten_str(poi.get("address")),
            _coerce_float(poi.get("longitude")),
            _coerce_float(poi.get("latitude")),
            _coerce_float(poi.get("rating")),
            _coerce_float(poi.get("avg_price")),
            _extract_open_time(poi),
            _flatten_str(poi.get("phone")),
            photos_json,
            json.dumps(poi.get("raw_poi") or {}, ensure_ascii=False),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO pois VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def load_ugc(conn: sqlite3.Connection) -> int:
    """加载 ugc/aspects.jsonl（三套合并后的全量 UGC）。"""
    rows = []
    for u in _open_jsonl(UGC_FILE):
        rows.append(_ugc_row(u))
    conn.executemany(
        "INSERT OR REPLACE INTO ugc_aspects VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def _ugc_row(u: dict) -> tuple:
    """单条 UGC dict → 表行 12 列。intensity 缺失时按 time_bucket + evidence 算。"""
    intensity = u.get("weekend_afternoon_intensity")
    if intensity is None:
        # 兜底现算，避免 manual_v1 等老数据在 rebuild 后 intensity 丢失
        intensity = _compute_intensity_inline(
            u.get("time_bucket") or "general",
            u.get("evidence_summary") or "",
        )
    return (
        u.get("record_id"),
        u.get("area_anchor"),
        u.get("poi_name"),
        u.get("aspect_type"),
        u.get("sentiment"),
        float(u.get("confidence") or 0.0),
        u.get("time_bucket"),
        1 if u.get("needs_review") else 0,
        u.get("evidence_summary"),
        json.dumps(u.get("normalized_value") or {}, ensure_ascii=False),
        json.dumps(u, ensure_ascii=False),
        float(intensity),
    )


# 内嵌 compute_intensity（与 src/etl/add_time_bucket_intensity.py 同步）
_BUCKET_BASE = {
    "weekend_afternoon": 1.0, "general": 0.5, "holiday": 0.6,
    "meal_time": 0.4, "evening": 0.2, "weekday_dinner": 0.1, "unknown": 0.3,
}
_KW_WEEKEND = ("周末", "周六", "周日", "节假日", "假期")
_KW_AFTER = ("下午", "午后", "14:", "15:", "16:", "17:", "13:",
             "citywalk", "city walk", "city-walk")
_KW_NEG = ("工作日", "早上", "早晨", "清晨", "凌晨", "深夜", "宵夜",
           "夜场", "夜店", "晚餐", "9:00", "10:00", "11:",
           "20:", "21:", "22:", "23:")


def _compute_intensity_inline(time_bucket: str, evidence_summary: str) -> float:
    base = _BUCKET_BASE.get(time_bucket, 0.4)
    if time_bucket == "weekend_afternoon":
        return 1.0
    if not evidence_summary:
        return base
    has_weekend = any(k in evidence_summary for k in _KW_WEEKEND)
    has_after = any(k in evidence_summary for k in _KW_AFTER)
    has_neg = any(k in evidence_summary for k in _KW_NEG)
    score = base
    if time_bucket in ("general", "holiday", "meal_time", "unknown"):
        if has_weekend and has_after:
            score = min(0.9, score + 0.40)
        elif has_weekend or has_after:
            score = min(0.75, score + 0.25)
    if has_neg:
        score = max(0.05, score - 0.20)
    if time_bucket in ("evening", "weekday_dinner"):
        if has_after and not has_neg:
            score = min(0.5, score + 0.15)
    return round(score, 3)


def load_routes(conn: sqlite3.Connection) -> int:
    """加载 amap routes + expanded_v2 estimated routes（Task 1.4）。"""
    rows = []
    for r in _open_jsonl(ROUTES_FILE):
        rows.append(_route_row(r))
    if ROUTES_EXPANDED_FILE.exists():
        for r in _open_jsonl(ROUTES_EXPANDED_FILE):
            rows.append(_route_row(r))
    conn.executemany(
        "INSERT OR REPLACE INTO routes VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def _route_row(r: dict) -> tuple:
    s = r.get("summary") or {}
    return (
        r.get("cache_key"),
        r.get("scene_id"),
        r.get("leg_id"),
        r.get("mode"),
        int(s.get("distance_m") or 0),
        int(s.get("duration_s") or 0),
        s.get("summary"),
        json.dumps(r, ensure_ascii=False),
    )


def _coerce_float(val):
    """amap 字段经常是空字符串 / 空 list / null / 数字字符串混杂——统一到 float 或 None。"""
    if val is None or val == "" or val == []:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _flatten_str(val) -> str | None:
    """amap 字段经常是 list / 空 list / 空字符串 / 真实字符串混杂——统一到字符串或 None。"""
    if val is None or val == [] or val == "":
        return None
    if isinstance(val, list):
        return ";".join(str(x) for x in val if x)
    return str(val)


def _extract_open_time(poi: dict) -> str | None:
    biz = (poi.get("raw_poi") or {}).get("biz_ext") or {}
    return _flatten_str(biz.get("open_time")) or _flatten_str(biz.get("opentime2"))


# ============================================================
# 查询接口（其他模块用）
# ============================================================

def get_conn() -> sqlite3.Connection:
    if not DB.exists():
        raise FileNotFoundError(f"DB 不存在，先跑 `python3 src/loader.py`：{DB}")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def query_pois(
    keyword: str | None = None,
    category: str | None = None,
    district: str | None = None,
    min_rating: float | None = None,
    limit: int = 20,
) -> list[dict]:
    """关键词模糊匹配 + 类目/区/评分过滤。

    keyword: 在 name / address / business_area 三个字段里 LIKE 匹配
    category: 高德 category_lv1 类目（"风景名胜" / "餐饮服务" / "购物服务" / ...）
    district: 区名（"东城区" / "海淀区" / ...）
    """
    conn = get_conn()
    where, params = [], []
    if keyword:
        where.append("(name LIKE ? OR address LIKE ? OR business_area LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])
    if category:
        where.append("category_lv1 LIKE ?")
        params.append(f"%{category}%")
    if district:
        where.append("district = ?")
        params.append(district)
    if min_rating is not None:
        where.append("rating >= ?")
        params.append(min_rating)
    sql = "SELECT * FROM pois"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rating DESC NULLS LAST LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_ugc(
    area_anchor: str | None = None,
    poi_name: str | None = None,
    aspect_types: list[str] | None = None,
    min_confidence: float = 0.0,
) -> list[dict]:
    conn = get_conn()
    where, params = [], []
    if area_anchor:
        where.append("area_anchor LIKE ?")
        params.append(f"%{area_anchor}%")
    if poi_name:
        where.append("poi_name LIKE ?")
        params.append(f"%{poi_name}%")
    if aspect_types:
        placeholders = ",".join(["?"] * len(aspect_types))
        where.append(f"aspect_type IN ({placeholders})")
        params.extend(aspect_types)
    if min_confidence > 0:
        where.append("confidence >= ?")
        params.append(min_confidence)
    sql = "SELECT * FROM ugc_aspects"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY confidence DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_routes(
    scene_id: str | None = None,
    leg_id: str | None = None,
    mode: str | None = None,
) -> list[dict]:
    conn = get_conn()
    where, params = [], []
    if scene_id:
        where.append("scene_id = ?")
        params.append(scene_id)
    if leg_id:
        where.append("leg_id = ?")
        params.append(leg_id)
    if mode:
        where.append("mode = ?")
        params.append(mode)
    sql = "SELECT * FROM routes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# 重建入口
# ============================================================

def rebuild():
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    t0 = time.time()
    n_poi = load_pois(conn)
    n_ugc = load_ugc(conn)
    n_route = load_routes(conn)
    conn.commit()
    conn.close()
    dt = time.time() - t0
    print(f"[ok] 建库完成 {dt:.2f}s")
    print(f"     pois={n_poi}  ugc_aspects={n_ugc}  routes={n_route}")
    print(f"     db={DB} ({DB.stat().st_size / 1024 / 1024:.1f} MB)")


def check():
    conn = get_conn()
    for table in ("pois", "ugc_aspects", "routes"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:15} {n}")
    print("\n--- pois 类目 lv1 分布 ---")
    for cat, n in conn.execute(
        "SELECT category_lv1, COUNT(*) c FROM pois GROUP BY category_lv1 ORDER BY c DESC LIMIT 10"
    ):
        print(f"  {cat or '(null)':15} {n}")
    print("\n--- ugc area_anchor 分布 ---")
    for anchor, n in conn.execute(
        "SELECT area_anchor, COUNT(*) c FROM ugc_aspects GROUP BY area_anchor ORDER BY c DESC"
    ):
        print(f"  {anchor or '(null)':30} {n}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只查统计，不重建")
    args = parser.parse_args()
    if args.check:
        check()
    else:
        rebuild()
        print()
        check()
