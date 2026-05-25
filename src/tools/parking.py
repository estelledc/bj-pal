"""停车实时车位 mock 预测（[03] 改进点）。

启发式两步：
1. 估总车位（capacity）— 看 POI 类目 + 关键词
   - 大商场（万达 / SKP / 大悦城）= 3000-5000
   - 中型商场 = 800-1500
   - 公园 = 100-500
   - 古建（故宫 / 颐和园）= 200-300（很紧张）
   - 老胡同 = 0（基本停不了）
   - 餐厅独立 = 20-50（共享商场则继承）

2. 估占用率（occupancy）— 看时段 + 周几 + 节假日
   - 节假日 ×1.5 + 周末 ×1.3 + 平日 ×1.0
   - 14-16 点高峰 + 0.15
   - 占用 ≥ 1.0 → 排队
   - 排队时长 = (occupancy - 0.85) × 60 分钟

输出：
- estimate_parking(poi, dt) → ParkingEstimate
- driving 用户场景下 wait > 30 触发 reroute；wait > 15 警告

与 [01] facility.parking 互补：
- [01]：该 POI 是否结构性有停车场（UGC 评价 +/-1）
- [03]：此时此刻有多挤（时段动态）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

ParkingDifficulty = Literal["easy", "moderate", "tight", "extreme", "no_parking"]


@dataclass
class ParkingEstimate:
    poi_name: str
    capacity: int
    occupancy: float        # [0, 1.5+]，> 1 表示满 + 排队
    available: int          # 估剩余车位（int(capacity * (1 - min(1, occ))))
    wait_min: int           # 等位时长（分钟）
    difficulty: ParkingDifficulty
    fee_per_hour: float     # 元
    explanation: str


# ============================================================
# 容量估算
# ============================================================

# (关键词列表 → 默认 capacity)
_CAPACITY_RULES: list[tuple[list[str], int]] = [
    # 巨型商场综合体
    (["SKP", "合生汇", "国贸商城", "万达广场", "大悦城", "万象城", "蓝色港湾"], 3500),
    (["太古里", "中粮祥云", "颐堤港", "侨福芳草地"], 2500),
    # 中型商场
    (["商城", "购物中心", "奥莱"], 1500),
    # 大型公园
    (["奥林匹克", "颐和园", "圆明园", "玉渊潭"], 800),
    # 中型公园 / 景区
    (["公园", "森林"], 300),
    # 古建紧张
    (["故宫", "天安门", "国博", "雍和宫"], 200),
    # 胡同 / 街区
    (["胡同", "大栅栏", "南锣", "什刹海", "鼓楼东"], 0),
    # 餐饮独立小店
    (["餐厅", "饭馆", "饭庄", "咖啡", "餐"], 30),
]

# 类目兜底
_LV2_DEFAULT: dict[str, int] = {
    "商场": 1500,
    "中餐厅": 30,
    "外国餐厅": 30,
    "咖啡厅": 20,
    "公园广场": 200,
    "风景名胜": 100,
    "风景名胜相关": 100,
}


def estimate_capacity(poi) -> int:
    name = getattr(poi, "name", None) or ""
    cat_lv2 = getattr(poi, "category_lv2", None) or ""
    for kws, cap in _CAPACITY_RULES:
        if any(kw in name for kw in kws):
            return cap
    if cat_lv2 in _LV2_DEFAULT:
        return _LV2_DEFAULT[cat_lv2]
    return 50  # 默认


# ============================================================
# 时段占用率
# ============================================================

def estimate_occupancy(
    capacity: int,
    poi_name: str,
    dt: Optional[datetime] = None,
) -> tuple[float, list[str]]:
    """[0, 1.5+] 占用率 + 解释列表。"""
    dt = dt or datetime.now()
    hour = dt.hour
    weekday = dt.weekday()

    base = 0.40  # 平日基线
    notes = []

    # 节假日：tier 来自 crowd_forecast
    try:
        from .crowd_forecast import get_holiday, is_famous_outdoor_poi
        hol = get_holiday(dt.date())
        if hol:
            if hol.tier == "tier_1_extreme":
                base += 0.50
                notes.append(f"{hol.name}（黄金周级别）")
            elif hol.tier == "tier_2_high":
                base += 0.30
                notes.append(f"{hol.name}（小长假）")
        elif weekday >= 5:
            base += 0.30
            notes.append("周末")
        # 知名景点周末 / 节假日额外加
        if (hol or weekday >= 5) and is_famous_outdoor_poi(poi_name):
            base += 0.15
            notes.append("热门 POI")
    except Exception:
        if weekday >= 5:
            base += 0.30
            notes.append("周末")

    # 时段
    if 11 <= hour <= 13:
        base += 0.10
        notes.append("午高峰")
    elif 14 <= hour <= 16:
        base += 0.15
        notes.append("下午高峰")
    elif 17 <= hour <= 19:
        base += 0.15
        notes.append("傍晚高峰")
    elif hour <= 9 or hour >= 21:
        base -= 0.10
        notes.append("非高峰")

    # capacity 越小占用率波动越大（小停车场易满）
    if capacity < 100:
        base += 0.15
        notes.append("小型停车场")

    return round(min(1.5, max(0.0, base)), 2), notes


# ============================================================
# 综合估算
# ============================================================

def _difficulty(occupancy: float, capacity: int) -> ParkingDifficulty:
    if capacity == 0:
        return "no_parking"
    if occupancy >= 1.0:
        return "extreme"
    if occupancy >= 0.85:
        return "tight"
    if occupancy >= 0.6:
        return "moderate"
    return "easy"


def _wait_min(occupancy: float, capacity: int) -> int:
    if capacity == 0:
        return 999  # 没停车场用 999 表示"找不到位"
    if occupancy < 0.85:
        return 0
    if occupancy < 1.0:
        return int((occupancy - 0.85) * 60)  # 0.85→0, 1.0→9 min
    # 满了：每 0.1 超出再加 15 min
    return 10 + int((occupancy - 1.0) * 150)


def _fee_per_hour(poi_name: str, capacity: int) -> float:
    if capacity == 0:
        return 0.0
    name = poi_name or ""
    # 商业中心 / 二三环内 ¥10-15
    if any(kw in name for kw in ["国贸", "三里屯", "王府井", "西单", "朝阳大悦城", "SKP", "合生汇"]):
        return 15.0
    if any(kw in name for kw in ["商场", "购物中心", "万达", "奥莱"]):
        return 10.0
    if any(kw in name for kw in ["公园", "森林", "颐和园", "圆明园"]):
        return 5.0
    if any(kw in name for kw in ["故宫", "天安门"]):
        return 8.0
    return 8.0


def estimate_parking(poi, dt: Optional[datetime] = None) -> ParkingEstimate:
    name = getattr(poi, "name", None) or "POI"
    capacity = estimate_capacity(poi)

    if capacity == 0:
        return ParkingEstimate(
            poi_name=name, capacity=0, occupancy=0.0, available=0,
            wait_min=999, difficulty="no_parking", fee_per_hour=0.0,
            explanation=f"⚠️ {name} 无独立停车场（胡同 / 老街区），建议公共交通",
        )

    occ, notes = estimate_occupancy(capacity, name, dt)
    available = max(0, int(capacity * (1 - min(1.0, occ))))
    wait = _wait_min(occ, capacity)
    diff = _difficulty(occ, capacity)
    fee = _fee_per_hour(name, capacity)

    if diff == "extreme":
        msg = f"🚗 {name} 停车场满（{int(occ*100)}%），等位约 {wait}min，建议改公共交通"
    elif diff == "tight":
        msg = f"🚗 {name} 紧张（{int(occ*100)}%，剩 {available} 位），可能小排队"
    elif diff == "moderate":
        msg = f"🅿️ {name} 中等（{int(occ*100)}%，剩 {available} 位）"
    else:
        msg = f"🅿️ {name} 充足（{int(occ*100)}%，剩 {available} 位）"
    if notes:
        msg += f"  · {', '.join(notes)}"

    return ParkingEstimate(
        poi_name=name, capacity=capacity, occupancy=occ, available=available,
        wait_min=wait, difficulty=diff, fee_per_hour=fee, explanation=msg,
    )


# ============================================================
# rank_fuse adjust
# ============================================================

def get_parking_score_adjust(
    poi,
    dt: Optional[datetime] = None,
) -> tuple[float, str]:
    """driving 模式下，停车难度 → score 调整。

    映射：
    - no_parking：-0.20（开车去要打 ¥30+ 周边停）
    - extreme：-0.20（满 + 排队 30min+）
    - tight：-0.10
    - moderate：0
    - easy：+0.05
    """
    est = estimate_parking(poi, dt)
    if est.difficulty == "no_parking":
        return -0.20, est.explanation
    if est.difficulty == "extreme":
        return -0.20, est.explanation
    if est.difficulty == "tight":
        return -0.10, est.explanation
    if est.difficulty == "easy":
        return 0.05, est.explanation
    return 0.0, ""


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    class P:
        def __init__(self, name, lv2=""):
            self.name = name
            self.category_lv2 = lv2

    samples = [
        P("国贸商城", "商场"),
        P("故宫博物院", "风景名胜"),
        P("玉渊潭公园", "公园广场"),
        P("南锣鼓巷", "风景名胜"),
        P("某中关村咖啡馆", "咖啡厅"),
        P("万达广场(石景山)", "商场"),
        P("奥林匹克森林公园", "公园广场"),
    ]

    print("=== 平日 周四 14:00 ===")
    weekday = datetime(2026, 5, 21, 14, 0)
    for p in samples:
        e = estimate_parking(p, weekday)
        print(f"  {p.name:25s} cap={e.capacity:>4} occ={e.occupancy:.2f} "
              f"剩={e.available:>4} wait={e.wait_min:>3}min  {e.difficulty}")

    print("\n=== 周六 14:00 ===")
    weekend = datetime(2026, 5, 23, 14, 0)
    for p in samples:
        e = estimate_parking(p, weekend)
        print(f"  {p.name:25s} cap={e.capacity:>4} occ={e.occupancy:.2f} "
              f"wait={e.wait_min:>3}min  {e.difficulty}")
        delta, why = get_parking_score_adjust(p, weekend)
        if delta != 0:
            print(f"      delta={delta:+.2f}  {why[:80]}")

    print("\n=== 国庆 14:00（极端拥堵）===")
    holiday = datetime(2026, 10, 4, 14, 0)
    for p in samples[:4]:
        e = estimate_parking(p, holiday)
        print(f"  {p.name:25s} occ={e.occupancy:.2f} wait={e.wait_min}min  {e.explanation[:80]}")
