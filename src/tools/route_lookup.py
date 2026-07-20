"""缓存优先、确定性估算兜底的路线时间查询。

策略：
- 优先：从 routes 表里按 origin/destination 经纬度模糊匹配找缓存的 4 模式 leg
- fallback：直线距离 × 城市绕行系数 1.3，再用 4 模式速度估计：
    walking 5 km/h / bicycling 15 km/h / driving 25 km/h（市区慢）/ transit 18 km/h + 等车 5min
- 输出：4 模式各自的 distance_m / duration_min / source（cached/estimated）

接口：
    lookup_routes(origin, destination) -> dict[mode, RouteLeg]
    pick_best_mode(legs, ...) -> str   # agent 推荐
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import get_conn  # noqa: E402

from .types import haversine_km  # noqa: E402

Mode = Literal["walking", "bicycling", "driving", "transit"]


@dataclass
class RouteLeg:
    mode: Mode
    distance_m: int
    duration_min: int
    source: Literal["cached", "estimated"]
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "distance_m": self.distance_m,
            "duration_min": self.duration_min,
            "source": self.source,
            "summary": self.summary,
        }


# 城市内绕行系数（直线 → 实际道路距离）
DETOUR = 1.3
SPEED_KMH = {"walking": 5.0, "bicycling": 15.0, "driving": 25.0, "transit": 18.0}
TRANSIT_WAIT_MIN = 5


def lookup_routes(
    origin_lng: float, origin_lat: float,
    dest_lng: float, dest_lat: float,
    match_radius_m: float = 200.0,
) -> dict[str, RouteLeg]:
    """查 4 种交通方式的路线时间。

    Args:
        origin_lng, origin_lat: 起点经纬度
        dest_lng, dest_lat: 终点经纬度
        match_radius_m: cached leg 匹配半径（origin 和 dest 都需在此半径内）
    """
    cached = _find_cached_leg(origin_lng, origin_lat, dest_lng, dest_lat, match_radius_m)
    if cached:
        return cached

    # fallback 估算
    direct_km = haversine_km(origin_lng, origin_lat, dest_lng, dest_lat)
    road_km = direct_km * DETOUR
    legs = {}
    for mode, speed in SPEED_KMH.items():
        duration_min = (road_km / speed) * 60
        if mode == "transit":
            duration_min += TRANSIT_WAIT_MIN
        legs[mode] = RouteLeg(
            mode=mode,
            distance_m=int(road_km * 1000),
            duration_min=max(1, int(round(duration_min))),
            source="estimated",
            summary=f"{mode} 估算 {road_km*1000:.0f}m {duration_min:.0f}min",
        )
    return legs


def pick_best_mode(
    legs: dict[str, RouteLeg],
    has_child: bool = False,
    walk_radius_km: float = 1.5,
    avoid_modes: Optional[list[str]] = None,
) -> tuple[str, str]:
    """给 4 模式 leg 字典，agent 推荐最优 + 写一句理由。

    规则：
    - 距离 < 1km：步行（带娃也能走）
    - 1-1.5km 不带娃：步行；带娃：骑行
    - 1.5-3km：骑行
    - 3-8km：驾车 / 公交（看哪个快）
    - 超 walk_radius_km × 2：直接公交 / 驾车
    """
    avoid = set(avoid_modes or [])
    walk = legs.get("walking")
    if not walk:
        return "transit", "缺步行数据，默认公交"
    walk_km = walk.distance_m / 1000

    if walk_km <= 1.0 and "walking" not in avoid:
        return "walking", f"步行 {walk.duration_min}min，距离短，走着舒服"
    if walk_km <= 1.5 and not has_child and "walking" not in avoid:
        return "walking", f"步行 {walk.duration_min}min，纯成人队伍走得动"
    if walk_km <= 1.5 and has_child:
        bike = legs.get("bicycling")
        if bike and "bicycling" not in avoid:
            return "bicycling", f"带娃距离 {walk_km:.1f}km，骑行 {bike.duration_min}min 比走着省力"
        return "walking", f"步行 {walk.duration_min}min，带娃慢走"
    # 1.5-3km：骑行优先（共享单车遍地）
    if walk_km <= 3.0:
        bike = legs.get("bicycling")
        if bike and "bicycling" not in avoid:
            return "bicycling", f"骑行 {bike.duration_min}min，{walk_km:.1f}km 共享单车正合适"
    # 较远（>3km）
    drive = legs.get("driving")
    transit = legs.get("transit")
    if drive and transit:
        if drive.duration_min <= transit.duration_min - 5 and "driving" not in avoid:
            return "driving", f"驾车 {drive.duration_min}min，比公交快 {transit.duration_min - drive.duration_min}min"
        return "transit", f"公交 {transit.duration_min}min，省停车"
    if drive and "driving" not in avoid:
        return "driving", f"驾车 {drive.duration_min}min"
    if transit:
        return "transit", f"公交 {transit.duration_min}min"
    return "walking", f"无更快选项，步行 {walk.duration_min}min"


# ============================================================
# helpers
# ============================================================

def _find_cached_leg(
    o_lng: float, o_lat: float, d_lng: float, d_lat: float,
    radius_m: float,
) -> Optional[dict[str, RouteLeg]]:
    """从 routes 表里找匹配的缓存 leg（origin 和 dest 都在 radius 内）。"""
    conn = get_conn()
    rows = conn.execute("SELECT raw_json FROM routes").fetchall()
    conn.close()

    radius_km = radius_m / 1000
    matches: dict[str, RouteLeg] = {}
    for row in rows:
        try:
            raw = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        # raw_json 里 origin/destination 经纬度
        # walking: response.route.origin
        # bicycling/driving/transit 结构略不同
        leg_origin, leg_dest = _extract_endpoints(raw)
        if leg_origin is None or leg_dest is None:
            continue
        d1 = haversine_km(o_lng, o_lat, leg_origin[0], leg_origin[1])
        d2 = haversine_km(d_lng, d_lat, leg_dest[0], leg_dest[1])
        if d1 <= radius_km and d2 <= radius_km:
            mode = raw.get("mode")
            summary = raw.get("summary") or {}
            matches[mode] = RouteLeg(
                mode=mode,
                distance_m=int(summary.get("distance_m") or 0),
                duration_min=max(1, int((summary.get("duration_s") or 0) / 60)),
                source="cached",
                summary=summary.get("summary") or "",
            )
    if len(matches) >= 2:  # 至少 2 个模式命中才用缓存，否则 fallback 估算更稳
        return matches if len(matches) == 4 else None
    return None


def _extract_endpoints(raw: dict) -> tuple[Optional[tuple[float, float]], Optional[tuple[float, float]]]:
    """从 amap raw response 里提取 origin/destination 经纬度。"""
    request_params = (raw.get("request") or {}).get("params") or {}
    origin_str = request_params.get("origin") or ""
    dest_str = request_params.get("destination") or ""
    return _parse_coord(origin_str), _parse_coord(dest_str)


def _parse_coord(s: str) -> Optional[tuple[float, float]]:
    if not s or "," not in s:
        return None
    try:
        lng, lat = s.split(",")
        return float(lng), float(lat)
    except (ValueError, TypeError):
        return None


def format_modes_compact(legs: dict[str, RouteLeg]) -> str:
    """给 demo 用的紧凑显示："🚶11min · 🚴3min · 🚗8min · 🚌15min"。"""
    icons = {"walking": "🚶", "bicycling": "🚴", "driving": "🚗", "transit": "🚌"}
    parts = []
    for mode in ["walking", "bicycling", "driving", "transit"]:
        if mode in legs:
            parts.append(f"{icons[mode]}{legs[mode].duration_min}min")
    return " · ".join(parts)
