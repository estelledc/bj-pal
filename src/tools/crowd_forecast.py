"""节假日人流预测（[42] 改进点 — 日历 + 启发式版）。

不训 LightGBM 也能做：
- 日历：data/holiday_calendar_2026.json 含 7 个法定节假日 + tier 分级
- POI 维度：知名户外景点列表（故宫/南锣/什刹海等）— 节假日人流极端
- 时段维度：早上 < 中午 < 下午峰值
- 周几维度：周末 ×1.5，工作日 ×1.0

输出：crowd_multiplier(poi, dt) ∈ [0.5, 3.0]
- 1.0 = 平日基线
- ≥ 2.0 = 极端拥挤（节假日 + 热门景点 + 高峰时段）
- < 1.0 = 平时清淡（工作日早上）

集成 rank_fuse：multiplier ≥ 2 时给户外景点 -0.20。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "holiday_calendar_2026.json"

_CALENDAR_CACHE: Optional[dict] = None


def _load() -> dict:
    global _CALENDAR_CACHE
    if _CALENDAR_CACHE is not None:
        return _CALENDAR_CACHE
    with DATA_PATH.open(encoding="utf-8") as f:
        _CALENDAR_CACHE = json.load(f)
    return _CALENDAR_CACHE


# ============================================================
# 节假日检测
# ============================================================

@dataclass
class HolidayHit:
    name: str
    tier: str          # tier_1_extreme / tier_2_high / tier_3_normal
    is_holiday: bool


def get_holiday(d: date) -> Optional[HolidayHit]:
    """该日期是否在节假日范围内。"""
    cal = _load()
    for h in cal["holidays"]:
        start = datetime.strptime(h["start"], "%Y-%m-%d").date()
        end = datetime.strptime(h["end"], "%Y-%m-%d").date()
        if start <= d <= end:
            return HolidayHit(name=h["name"], tier=h["tier"], is_holiday=True)
    return None


def is_weekend(d: date) -> bool:
    return d.weekday() in (5, 6)


# ============================================================
# 拥挤倍率
# ============================================================

_TIER_MULTIPLIER = {
    "tier_1_extreme": 2.5,
    "tier_2_high": 1.7,
    "tier_3_normal": 1.0,
}

_FAMOUS_POI_BOOST = 1.4   # 知名 POI 在节假日 / 周末叠加
_HOUR_PROFILE = {
    # hour: multiplier
    8: 0.6, 9: 0.7, 10: 0.9, 11: 1.1,
    12: 1.2, 13: 1.3, 14: 1.5, 15: 1.6,
    16: 1.5, 17: 1.4, 18: 1.3, 19: 1.2,
    20: 1.0,
}


def is_famous_outdoor_poi(poi_name: str) -> bool:
    """该 POI 是否在已知"节假日人流极端" 列表里。"""
    if not poi_name:
        return False
    cal = _load()
    famous = cal.get("famous_outdoor_pois_extreme_crowd_on_holiday", [])
    return any(f in poi_name for f in famous)


def crowd_multiplier(
    poi_name: str,
    dt: Optional[datetime] = None,
) -> tuple[float, str]:
    """计算拥挤倍率 + 一句话解释。

    1.0 = 平日基线；> 2.0 = 极端拥挤
    """
    dt = dt or datetime.now()
    d = dt.date()
    hour = dt.hour

    hol = get_holiday(d)
    weekend = is_weekend(d)
    weekday_factor = 1.5 if weekend else 1.0
    hour_factor = _HOUR_PROFILE.get(hour, 1.0)
    is_famous = is_famous_outdoor_poi(poi_name)
    # famous_boost 只在节假日 / 周末叠加（平日故宫也是相对清淡的）
    famous_factor = _FAMOUS_POI_BOOST if (is_famous and (hol or weekend)) else 1.0

    if hol:
        base = _TIER_MULTIPLIER.get(hol.tier, 1.0)
    else:
        base = weekday_factor

    multiplier = round(base * hour_factor * famous_factor, 2)

    if hol and famous_factor > 1.0:
        why = f"⚠️ {hol.name}假期 + {poi_name} 历史人流极端（×{multiplier}）"
    elif hol:
        why = f"{hol.name}假期，整体人流偏多（×{multiplier}）"
    elif weekday_factor > 1.0 and famous_factor > 1.0:
        why = f"周末 + 热门景点（×{multiplier}）"
    elif famous_factor > 1.0:
        why = f"热门景点常规峰值（×{multiplier}）"
    elif weekday_factor > 1.0:
        why = f"周末高峰（×{multiplier}）"
    elif multiplier > 1.0:
        why = f"高峰时段拥挤（×{multiplier}）"
    else:
        why = ""

    return multiplier, why


# ============================================================
# 加分 / 减分
# ============================================================

def get_crowd_score_adjust(
    poi_name: str,
    dt: Optional[datetime] = None,
) -> tuple[float, str]:
    """multiplier → score 调整（用于 rank_fuse）。

    映射：
    - multiplier ≤ 1.1：0
    - 1.1 < m ≤ 1.5：-0.05
    - 1.5 < m ≤ 2.0：-0.15
    - m > 2.0：-0.30
    """
    m, why = crowd_multiplier(poi_name, dt)
    if m <= 1.1:
        return 0.0, ""
    if m <= 1.5:
        return -0.05, why
    if m <= 2.0:
        return -0.15, why
    return -0.30, why


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    print("=== holidays loaded ===")
    cal = _load()
    for h in cal["holidays"]:
        print(f"  {h['name']:6s}  {h['start']} → {h['end']}  ({h['tier']})")

    cases = [
        # (POI, datetime, 期望)
        ("故宫博物院", datetime(2026, 5, 21, 14, 0), "周四下午平日"),
        ("故宫博物院", datetime(2026, 5, 23, 14, 0), "周六下午"),
        ("故宫博物院", datetime(2026, 10, 4, 14, 0), "国庆节假日午高峰 — 极端"),
        ("某无名小店", datetime(2026, 10, 4, 14, 0), "国庆节假日 - 非热门，普通"),
        ("玉渊潭公园", datetime(2026, 4, 5, 11, 0), "清明节 + 樱花季"),
        ("国贸商城", datetime(2026, 5, 23, 14, 0), "周六商场"),
    ]
    print("\n=== 拥挤倍率 ===")
    for poi, dt, label in cases:
        m, why = crowd_multiplier(poi, dt)
        adj, _ = get_crowd_score_adjust(poi, dt)
        print(f"  [{label}]")
        print(f"    {poi:20s} {dt:%Y-%m-%d %H:%M}  multiplier=×{m}  adjust={adj:+.2f}")
        if why:
            print(f"    {why}")
