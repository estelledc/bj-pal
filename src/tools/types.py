"""BJ-Pal 共用数据模型。

设计：用 dataclass（标准库，零依赖），不上 pydantic v2——降低 3.9 兼容
风险，等到 Planner agent 真要 LLM 输出校验时再引入 pydantic v2。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional


# ============================================================
# 基础枚举（用 Literal 避免引入 enum 带来的 JSON 序列化坑）
# ============================================================

POICategory = Literal["scenic", "food", "landmark", "museum", "sports", "shopping", "all"]
TransportMode = Literal["walking", "bicycling", "driving", "transit"]
Persona = Literal["family", "friends", "solo", "with_parents"]
TimeBucket = Literal["weekend_afternoon", "weekday_afternoon", "evening", "holiday", "general"]


# 高德 category_lv1 → 我们的 POICategory 映射
AMAP_CAT_MAP = {
    "scenic": ["风景名胜"],
    "food": ["餐饮服务"],
    "landmark": ["风景名胜"],  # amap 不区分，用 typecode 二次过滤
    "museum": ["科教文化服务"],
    "sports": ["体育休闲服务"],
    "shopping": ["购物服务"],
}

# UGC area_anchor 全枚举（来自 manual_ugc_seed.jsonl 实测分布）
KNOWN_AREA_ANCHORS = [
    "五道营-雍和宫片区",      # UGC 11 条，主 demo 片区
    "奥林匹克公园片区",        # UGC 8 条
    "王府井-东单片区",        # UGC 6 条
    "什刹海-鼓楼片区",        # UGC 4 条
    "天安门-故宫片区",        # UGC 3 条
    "景山-什刹海片区",        # UGC 2 条
    "东四-本地餐饮片区",       # UGC 2 条
]


# ============================================================
# Domain models
# ============================================================

@dataclass
class POI:
    """高德 POI，对应 SQLite pois 表一行 + 派生字段。"""
    id: str
    name: str
    category_lv1: Optional[str]
    category_lv2: Optional[str]
    category_lv3: Optional[str]
    typecode: Optional[str]
    district: Optional[str]
    business_area: Optional[str]
    address: Optional[str]
    longitude: Optional[float]
    latitude: Optional[float]
    rating: Optional[float]
    avg_price: Optional[float]
    open_time: Optional[str]
    phone: Optional[str]
    photos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Aspect:
    """UGC 切片单条记录。来自 manual_ugc_seed.jsonl。"""
    record_id: str
    area_anchor: str
    poi_name: str
    aspect_type: str   # environment/comfort/food/budget/crowd/transport/queue/scenario_fit/booking_risk
    sentiment: str     # positive/negative/mixed
    confidence: float
    time_bucket: Optional[str]
    needs_review: bool
    evidence_summary: str
    normalized_value: dict  # 已解析回 dict（risk_tags / scene_tags / fit_scores ...）
    weekend_afternoon_intensity: float = 0.5  # Task 1.2：周六下午相关度 [0, 1]
    # P0.1 红旗面板（信号 2/6）
    dataset_version: str = ""           # manual_ugc_seed_v1 / synthetic_v2 / amap_inferred_v2
    evidence_age_days: int = 0          # UGC 距今多少天（基于 dataset_version 推导）
    evidence_source_count: int = 1      # 多少条独立来源支撑
    decayed_confidence: float = 0.0     # confidence × freshness_decay(category)

    def risk_tags(self) -> list[str]:
        return list(self.normalized_value.get("risk_tags") or [])

    def scene_tags(self) -> list[str]:
        return list(self.normalized_value.get("scene_tags") or [])


@dataclass
class SearchConstraints:
    """检索约束。Planner 从用户偏好转换。"""
    persona: Persona = "family"
    party_size: int = 3
    has_child: bool = False
    child_age: Optional[int] = None
    diet_flags: list[str] = field(default_factory=list)  # ["light_diet", "vegetarian", "halal", "no_spicy"]
    walk_radius_km: float = 1.5
    budget_per_person: Optional[float] = None  # 单人预算上限，元
    open_at: Optional[str] = None              # ISO 时间字符串，"2026-05-18T14:00"
    min_rating: float = 4.0


@dataclass
class Reason:
    """Ranking 解释——评委问"为什么选这家"时展开的依据。"""
    factor: str        # "amap_rating" / "ugc_softscore" / "budget_fit" / "distance" / "crowd_penalty"
    contrib: float     # 该因子贡献的 [-1, 1] 归一分
    evidence: str      # 人话解释 + UGC 引用片段


@dataclass
class RankedPOI:
    poi: POI
    score: float
    reasons: list[Reason] = field(default_factory=list)
    risk_tags: list[str] = field(default_factory=list)


# ============================================================
# 距离计算（避免引入 numpy / scipy）
# ============================================================

def haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """两点经纬度直线距离，公里。"""
    from math import radians, sin, cos, asin, sqrt
    lng1, lat1, lng2, lat2 = map(radians, [lng1, lat1, lng2, lat2])
    dlng = lng2 - lng1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))
