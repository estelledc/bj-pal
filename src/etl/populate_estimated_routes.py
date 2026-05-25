"""Task 1.4 routes 扩展到 100+ 条（不依赖 amap key）。

策略：
- 选片区核心高评 POI top 25
- 每个 POI 与"地理最近 3 个 POI"配对生成 leg（去重后 ~60 unique）
- 每个 leg 算 4 模式（walking/bicycling/driving/transit）的距离 + 时间
- 入 routes 表，source="estimated_v2"，cache_key 区分原 amap cache

这不是"真实路网"，是"客观距离 + 标准速度"估算。
evidence 字段标注 method="haversine_x_detour"，**不冒充真实 amap 路由**。

跑法：python3 src/etl/populate_estimated_routes.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import get_conn  # noqa: E402
from tools.types import haversine_km  # noqa: E402

DETOUR = 1.3
SPEED_KMH = {"walking": 5.0, "bicycling": 15.0, "driving": 25.0, "transit": 18.0}
TRANSIT_WAIT_MIN = 5
DATASET_VERSION = "estimated_v2_haversine"


def fetch_seed_pois(limit: int = 25, strategy: str = "by_area") -> list[dict]:
    """选 leg 起终点 POI。

    strategy:
        "by_area" — 每个 business_area 取 rating 最高的 1 个（多样性优先）
        "by_rating" — 全市按 rating top N（数量优先）
        "mixed" — 先 by_area 各 1 个，再用 by_rating 补到 limit
    """
    conn = get_conn()
    cur = conn.cursor()
    base = """
        SELECT id, name, business_area, longitude, latitude, rating, category_lv1
        FROM pois
        WHERE rating >= 4.3
          AND longitude IS NOT NULL AND latitude IS NOT NULL
          AND business_area IS NOT NULL AND business_area != ''
    """
    if strategy == "by_rating":
        rows = cur.execute(
            base + " ORDER BY rating DESC, avg_price DESC LIMIT ?",
            (limit,),
        ).fetchall()
    elif strategy == "mixed":
        # 先各 area top 1
        per_area = cur.execute("""
            WITH ranked AS (
                SELECT id, name, business_area, longitude, latitude, rating, category_lv1,
                       ROW_NUMBER() OVER (
                           PARTITION BY business_area ORDER BY rating DESC, avg_price DESC
                       ) AS rk
                FROM pois
                WHERE business_area IS NOT NULL AND business_area != ''
                    AND rating >= 4.3
                    AND longitude IS NOT NULL AND latitude IS NOT NULL
            )
            SELECT id, name, business_area, longitude, latitude, rating, category_lv1
            FROM ranked WHERE rk = 1
            ORDER BY rating DESC
        """).fetchall()
        seen_ids = {r["id"] for r in per_area}
        # 用 rating top 补足
        more = cur.execute(
            base + " AND id NOT IN (SELECT id FROM pois WHERE id IS NULL) "
            " ORDER BY rating DESC, avg_price DESC LIMIT ?",
            (limit * 2,),
        ).fetchall()
        rows = list(per_area)
        for r in more:
            if r["id"] not in seen_ids:
                rows.append(r)
                seen_ids.add(r["id"])
                if len(rows) >= limit:
                    break
    else:  # by_area
        rows = cur.execute("""
            WITH ranked AS (
                SELECT id, name, business_area, longitude, latitude, rating, category_lv1,
                       ROW_NUMBER() OVER (
                           PARTITION BY business_area ORDER BY rating DESC, avg_price DESC
                       ) AS rk
                FROM pois
                WHERE business_area IS NOT NULL AND business_area != ''
                    AND rating >= 4.3
                    AND longitude IS NOT NULL AND latitude IS NOT NULL
            )
            SELECT id, name, business_area, longitude, latitude, rating, category_lv1
            FROM ranked WHERE rk = 1
            ORDER BY rating DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def build_legs(pois: list[dict], k_nearest: int = 3,
               max_km: float = 8.0) -> list[tuple[dict, dict]]:
    """每个 POI 与 k 个最近邻配对。去重 (a, b) == (b, a)。"""
    legs = set()  # set of (id_a, id_b) 排序后的 tuple
    for i, a in enumerate(pois):
        # 计算到所有其他 POI 的距离
        dists = []
        for j, b in enumerate(pois):
            if i == j:
                continue
            d = haversine_km(a["longitude"], a["latitude"],
                             b["longitude"], b["latitude"])
            if d <= max_km:
                dists.append((d, b))
        dists.sort()
        for _, b in dists[:k_nearest]:
            key = tuple(sorted([a["id"], b["id"]]))
            legs.add(key)

    # 把 tuple 转回 (a_dict, b_dict)
    by_id = {p["id"]: p for p in pois}
    return [(by_id[a_id], by_id[b_id]) for a_id, b_id in legs]


def make_route_rows(legs: list[tuple[dict, dict]]) -> list[tuple]:
    """每个 leg × 4 mode → 4 行 routes 表数据。"""
    rows = []
    for a, b in legs:
        direct_km = haversine_km(a["longitude"], a["latitude"],
                                 b["longitude"], b["latitude"])
        road_km = direct_km * DETOUR
        for mode, speed in SPEED_KMH.items():
            duration_min = (road_km / speed) * 60
            if mode == "transit":
                duration_min += TRANSIT_WAIT_MIN
            duration_s = max(60, int(duration_min * 60))
            distance_m = int(road_km * 1000)
            cache_key = f"est:{a['id']}->{b['id']}:{mode}"
            scene_id = f"est:{a['business_area']}--{b['business_area']}"
            leg_id = f"{a['name']}--{b['name']}"
            summary_text = f"{mode} estimated {road_km*1000:.0f}m {duration_min:.0f}min"
            raw = {
                "mode": mode,
                "request": {
                    "params": {
                        "origin": f"{a['longitude']},{a['latitude']}",
                        "destination": f"{b['longitude']},{b['latitude']}",
                    }
                },
                "summary": {
                    "distance_m": distance_m,
                    "duration_s": duration_s,
                    "summary": summary_text,
                },
                "dataset_version": DATASET_VERSION,
                "method": "haversine_x_detour_with_mode_speed",
                "from_poi": a["name"],
                "to_poi": b["name"],
                "from_business_area": a["business_area"],
                "to_business_area": b["business_area"],
            }
            rows.append((
                cache_key, scene_id, leg_id, mode,
                distance_m, duration_s, summary_text,
                json.dumps(raw, ensure_ascii=False),
            ))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-limit", type=int, default=25)
    ap.add_argument("--k-nearest", type=int, default=3)
    ap.add_argument("--strategy", default="by_area",
                    choices=["by_area", "by_rating", "mixed"])
    ap.add_argument("--max-km", type=float, default=8.0,
                    help="leg 最长直线距离 km，超出不配对")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pois = fetch_seed_pois(args.seed_limit, args.strategy)
    print(f"=== 选种子 POI {len(pois)} 个 ===")
    for p in pois[:10]:
        print(f"  {p['business_area']:<10} {p['name'][:25]:<28} rating={p['rating']}")
    if len(pois) > 10:
        print(f"  ... 共 {len(pois)} 个")

    legs = build_legs(pois, args.k_nearest, args.max_km)
    print(f"\n=== 配对 leg {len(legs)} 条 ===")
    sample = legs[:5]
    for a, b in sample:
        d = haversine_km(a["longitude"], a["latitude"], b["longitude"], b["latitude"])
        print(f"  {a['name'][:18]:<20} → {b['name'][:18]:<20}  直线 {d:.2f}km")

    rows = make_route_rows(legs)
    print(f"\n=== 生成 routes {len(rows)} 行（{len(legs)} leg × 4 mode）===")

    if args.dry_run:
        print("\n[DRY RUN] 不入库")
        return

    conn = get_conn()
    cur = conn.cursor()
    before = cur.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
    cur.executemany(
        "INSERT OR REPLACE INTO routes VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    after = cur.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
    print(f"\n[DONE] routes 表 {before} → {after}（净增 {after - before}）")
    n_est = cur.execute("SELECT COUNT(*) FROM routes WHERE cache_key LIKE 'est:%'").fetchone()[0]
    print(f"  其中 estimated_v2: {n_est}")
    conn.close()


if __name__ == "__main__":
    main()
