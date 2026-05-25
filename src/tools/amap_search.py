"""amap POI 检索 tool。

Planner / Replanner 通过 search_pois() 拉候选 POI 列表。

设计要点：
- 片区 (area_anchor) → 中心点经纬度 → 半径过滤（不依赖 amap business_area，更稳）
- 约束 (SearchConstraints) → SQL where + post-filter 分两层
- 营业时间过滤目前是字符串 LIKE 启发式，W2 D2 再做严格解析
"""

from __future__ import annotations

import json
import sqlite3
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

# 允许 src/tools/ 直接 import 同级 src/loader.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import get_conn  # noqa: E402

from .types import (  # noqa: E402
    AMAP_CAT_MAP,
    POI,
    POICategory,
    SearchConstraints,
    haversine_km,
)


# ============================================================
# Area anchor 中心点解析
# ============================================================

# 7 个 UGC 片区的中心经纬度（手工 anchor，参考 manual_ugc_seed.jsonl 的关键 POI）
# 后续可改为：扫 UGC 该 anchor 下所有 poi_name 在 amap 中的经纬度，取均值
AREA_CENTERS: dict[str, tuple[float, float]] = {
    # lng, lat
    "五道营-雍和宫片区": (116.4166, 39.9474),   # 雍和宫附近
    "奥林匹克公园片区":   (116.3974, 40.0028),   # 鸟巢附近
    "王府井-东单片区":     (116.4174, 39.9094),
    "什刹海-鼓楼片区":     (116.3877, 39.9456),
    "天安门-故宫片区":     (116.3974, 39.9087),
    "景山-什刹海片区":     (116.3909, 39.9288),
    "东四-本地餐饮片区":   (116.4178, 39.9265),
}


# 从 amap POI 反推的 v3/扩展片区中心（data/area_centers_inferred.json）
# 启动时一次性加载，覆盖 428 个 area_anchor 中的 323 个（80.5% UGC）
_INFERRED_CENTERS: dict[str, tuple[float, float]] = {}


def _load_inferred_centers():
    if _INFERRED_CENTERS:
        return
    path = Path(__file__).resolve().parent.parent.parent / "data" / "area_centers_inferred.json"
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    for k, v in data.items():
        c = v.get("center")
        if c and len(c) == 2:
            _INFERRED_CENTERS[k] = (float(c[0]), float(c[1]))


@lru_cache(maxsize=512)
def resolve_area_center(area_anchor: str) -> Optional[tuple[float, float]]:
    """area_anchor → (lng, lat)。优先级：硬编码 > 反推映射 > 模糊匹配。"""
    if area_anchor in AREA_CENTERS:
        return AREA_CENTERS[area_anchor]
    _load_inferred_centers()
    if area_anchor in _INFERRED_CENTERS:
        return _INFERRED_CENTERS[area_anchor]
    # 模糊匹配（兼容老命名风格）
    for key, center in AREA_CENTERS.items():
        if any(part in key for part in area_anchor.split("-")) or area_anchor in key:
            return center
    return None


# ============================================================
# 主接口
# ============================================================

def search_pois(
    area_anchor: Optional[str] = None,
    category: POICategory = "all",
    constraints: Optional[SearchConstraints] = None,
    limit: int = 20,
) -> list[POI]:
    """检索 POI 候选集。

    Args:
        area_anchor: 7 个已知片区之一（见 KNOWN_AREA_ANCHORS）；用经纬度半径过滤
        category: 类目（scenic/food/landmark/museum/sports/shopping/all）
        constraints: 用户约束（步行半径 / 预算 / 评分 / 营业时间 / 亲子 ...）
        limit: 返回上限

    Returns:
        POI 列表，按 rating 倒序。
    """
    constraints = constraints or SearchConstraints()
    conn = get_conn()

    # 1) 类目 SQL where
    where: list[str] = []
    params: list = []
    if category != "all":
        cats = AMAP_CAT_MAP.get(category, [])
        if cats:
            placeholders = " OR ".join(["category_lv1 = ?"] * len(cats))
            where.append(f"({placeholders})")
            params.extend(cats)

    # 2) 评分门槛
    if constraints.min_rating > 0:
        where.append("rating >= ?")
        params.append(constraints.min_rating)

    # 3) 预算上限（avg_price 可能为 NULL，宽松：null 也保留）
    if constraints.budget_per_person is not None:
        where.append("(avg_price IS NULL OR avg_price <= ?)")
        params.append(constraints.budget_per_person)

    # 4) 必须有经纬度（半径过滤前置）
    if area_anchor:
        where.append("longitude IS NOT NULL AND latitude IS NOT NULL")

    sql = "SELECT * FROM pois"
    if where:
        sql += " WHERE " + " AND ".join(where)
    # 多取一些先做半径过滤，避免被分页截掉
    sql += f" ORDER BY rating DESC LIMIT {max(limit * 5, 200)}"

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    # 5) Post-filter: 半径
    candidates: list[POI] = []
    center = resolve_area_center(area_anchor) if area_anchor else None
    for r in rows:
        if center and r["longitude"] and r["latitude"]:
            dist = haversine_km(center[0], center[1], r["longitude"], r["latitude"])
            if dist > constraints.walk_radius_km:
                continue
        candidates.append(_row_to_poi(r))

    # 6) Post-filter: 营业时间（启发式 LIKE）
    if constraints.open_at:
        candidates = [p for p in candidates if _is_likely_open(p.open_time, constraints.open_at)]

    # 7) Post-filter: 亲子 / 饮食 flag — 启发式（M2 见 ugc_signals 融合后再精排）
    if constraints.has_child and constraints.child_age and constraints.child_age <= 6:
        # 偏向 typecode 不是酒吧 / 夜店 / 重辣的
        candidates = [p for p in candidates if not _looks_adult_only(p)]
    if "light_diet" in constraints.diet_flags:
        # 启发式：name 含 "烤鸭/麻辣/火锅/烤肉" 视为非轻食候选 → 降权但不剔除
        # 这里只做软过滤，硬过滤交给 ranking 层做
        pass

    return candidates[:limit]


# ============================================================
# 辅助
# ============================================================

def _row_to_poi(row: sqlite3.Row) -> POI:
    photos_raw = row["photos_json"] or "[]"
    try:
        photos_list = json.loads(photos_raw)
        photo_urls = [
            p.get("url") for p in photos_list if isinstance(p, dict) and p.get("url")
        ]
    except (json.JSONDecodeError, AttributeError):
        photo_urls = []

    return POI(
        id=row["id"],
        name=row["name"],
        category_lv1=row["category_lv1"],
        category_lv2=row["category_lv2"],
        category_lv3=row["category_lv3"],
        typecode=row["typecode"],
        district=row["district"],
        business_area=row["business_area"],
        address=row["address"],
        longitude=row["longitude"],
        latitude=row["latitude"],
        rating=row["rating"],
        avg_price=row["avg_price"],
        open_time=row["open_time"],
        phone=row["phone"],
        photos=photo_urls,
    )


def _is_likely_open(open_time_str: Optional[str], at_iso: str) -> bool:
    """启发式：用户要 14:00 来，则 open_time 字段含 14 / "08:30" / "全天" 视为营业。

    真实严格解析见 W2 D2；这一层只做粗筛。
    """
    if not open_time_str:
        return True  # 没数据时不排除
    s = open_time_str.lower()
    if any(kw in s for kw in ["全天", "24小时", "00:00-24:00", "00:00-23:59"]):
        return True
    # 从 ISO "2026-05-18T14:00" 取小时
    try:
        hour = int(at_iso.split("T")[1].split(":")[0])
    except (IndexError, ValueError):
        return True
    # open_time 形如 "08:30-17:30" 或 "周一至周日 08:30-17:30"——找 "HH:" 数字
    import re
    times = re.findall(r"(\d{2}):", s)
    if len(times) >= 2:
        try:
            start, end = int(times[0]), int(times[1])
            return start <= hour < end
        except ValueError:
            pass
    return True  # 解析失败放行


def _looks_adult_only(poi: POI) -> bool:
    """5 岁娃慎入：酒吧 / 夜店 / 清吧 / KTV。"""
    name = (poi.name or "").lower()
    cat3 = (poi.category_lv3 or "").lower()
    cat2 = (poi.category_lv2 or "").lower()
    blacklist = ["酒吧", "夜店", "清吧", "ktv", "电竞", "桌游", "lounge", "bar"]
    blob = f"{name} {cat2} {cat3}"
    return any(kw in blob for kw in blacklist)


# ============================================================
# Helper: 一行扫一遍，给 demo 用
# ============================================================

def list_known_areas() -> list[str]:
    return list(AREA_CENTERS.keys())
