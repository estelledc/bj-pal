"""UGC aspects 检索 + 聚合 tool。

供：
- ranking 层（rank_fuse）：拉某 POI 所有 aspects，算软分 + 提取 evidence
- availability_probe：拉某片区/POI 的 risk_tags（queue/crowd/booking_risk）
- Planner：拉某片区的 scenario_fit / scene_tags 做"这片区适合什么场景"summary
- P0.1 red flags：extract_red_flags 给 UI 直接拉一条最关键的吐槽
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import query_ugc  # noqa: E402

from .types import Aspect  # noqa: E402


# ============================================================
# P0.1 信号 2/6：UGC 时效衰减 + red flags
# ============================================================

# 不同 dataset_version 的"基准时间锚点"
_DATASET_AGE_ANCHOR = {
    "manual_ugc_seed_v1": "2026-05-03",          # 截图实际拍摄日
    "synthetic_from_public_summaries_v2": "2026-04-15",
    "synthetic_from_scenario_theme_v2": "2026-04-15",
    "derived_from_amap_attributes_v2": "2026-05-01",
}
_DEFAULT_AGE_ANCHOR = "2026-04-01"

# freshness_decay：每类目的衰减半衰期
_DECAY_HALF_LIFE_DAYS = {
    "food": 30,           # 餐饮 30 天衰减 50%
    "scenic": 90,         # 景点 90 天衰减 30%
    "culture": 180,       # 文化场所 180 天衰减 20%
    "shopping": 60,
    "default": 60,
}


def _compute_age_days(dataset_version: str) -> int:
    """根据 dataset_version 推导 evidence_age_days。"""
    anchor = _DATASET_AGE_ANCHOR.get(dataset_version, _DEFAULT_AGE_ANCHOR)
    try:
        anchor_dt = datetime.strptime(anchor, "%Y-%m-%d")
        return max(0, (datetime.now() - anchor_dt).days)
    except ValueError:
        return 30


def freshness_decay(age_days: int, category: str = "default") -> float:
    """时效衰减系数 ∈ [0, 1]。

    采用半衰期模型：decay = 0.5 ** (age / half_life)
    - food age_days=30 → 0.5
    - food age_days=90 → 0.125
    - scenic age_days=90 → 0.5
    - culture age_days=180 → 0.5
    """
    half_life = _DECAY_HALF_LIFE_DAYS.get(category, _DECAY_HALF_LIFE_DAYS["default"])
    if half_life <= 0:
        return 1.0
    return round(0.5 ** (age_days / half_life), 3)


def _aspect_category(aspect_type: str) -> str:
    """aspect_type → freshness_decay 类目。"""
    mapping = {
        "food": "food",
        "queue": "food",
        "booking_risk": "food",
        "budget": "food",
        "comfort": "scenic",
        "crowd": "scenic",
        "environment": "scenic",
        "transport": "scenic",
        "scenario_fit": "culture",
    }
    return mapping.get(aspect_type, "default")


# ============================================================
# 主接口
# ============================================================

def fetch_aspects(
    area_anchor: Optional[str] = None,
    poi_name: Optional[str] = None,
    aspect_types: Optional[list[str]] = None,
    min_confidence: float = 0.0,
    min_weekend_afternoon_intensity: float = 0.0,
) -> list[Aspect]:
    """按片区 / POI / aspect 类型筛 UGC 切片。

    Args:
        min_weekend_afternoon_intensity: Task 1.2 加 — 仅返回 intensity ≥ 阈值的；
            0.0 = 全返（默认），0.7 = 只要"周六下午强相关"的。
    """
    rows = query_ugc(
        area_anchor=area_anchor,
        poi_name=poi_name,
        aspect_types=aspect_types,
        min_confidence=min_confidence,
    )
    aspects = [_row_to_aspect(r) for r in rows]
    if min_weekend_afternoon_intensity > 0:
        aspects = [a for a in aspects
                   if a.weekend_afternoon_intensity >= min_weekend_afternoon_intensity]
    return aspects


def fetch_risk_signals(
    area_anchor: Optional[str] = None,
    poi_name: Optional[str] = None,
) -> list[Aspect]:
    """拉拥堵 / 排队 / 预订风险信号——availability_probe 的输入。"""
    return fetch_aspects(
        area_anchor=area_anchor,
        poi_name=poi_name,
        aspect_types=["queue", "crowd", "booking_risk", "transport"],
        min_confidence=0.6,
    )


def fetch_scenario_fit(area_anchor: str) -> dict[str, float]:
    """聚合该片区所有 scenario_fit aspect，返回 {scene: avg_score}。

    示例：{'citywalk': 0.6, 'first_visit': 0.7, 'classic_beijing': 0.8, ...}
    """
    aspects = fetch_aspects(
        area_anchor=area_anchor,
        aspect_types=["scenario_fit"],
        min_confidence=0.5,
    )
    score_by_scene: dict[str, list[float]] = defaultdict(list)
    for a in aspects:
        # normalized_value 可能含 fit_scores 或 scene_tags
        fit_scores = a.normalized_value.get("fit_scores") or {}
        for scene, score in fit_scores.items():
            try:
                score_by_scene[scene].append(float(score))
            except (TypeError, ValueError):
                continue
        for tag in (a.normalized_value.get("scene_tags") or []):
            score_by_scene[tag].append(a.confidence)
    return {scene: sum(scores) / len(scores) for scene, scores in score_by_scene.items() if scores}


def soft_score_for_poi(poi_name: str) -> tuple[float, list[Aspect]]:
    """为某 POI 算 UGC 综合软分（[-1, 1]），并返回支撑 aspects。

    简单加权：
        positive = +1 * confidence
        mixed    = 0
        negative = -1 * confidence
    后续 ranking 层用这个分 + amap_rating 做融合。
    """
    aspects = fetch_aspects(poi_name=poi_name)
    if not aspects:
        return 0.0, []
    total = 0.0
    for a in aspects:
        sign = {"positive": 1.0, "negative": -1.0, "mixed": 0.0}.get(a.sentiment, 0.0)
        total += sign * a.confidence
    return total / max(len(aspects), 1), aspects


def summarize_area(area_anchor: str) -> dict:
    """片区 summary——Planner 用来理解"这片区是什么样"。

    输出：{
        'aspect_counts': {'food': 7, 'environment': 6, ...},
        'risk_tags_top': [('parking_hard', 1), ('holiday_crowd', 1), ...],
        'scene_tags_top': [...],
        'scenario_fit': {...},
        'mentioned_pois': ['雍和宫', '五道营胡同', '北新桥', ...]
    }
    """
    aspects = fetch_aspects(area_anchor=area_anchor)
    if not aspects:
        return {"aspect_counts": {}, "risk_tags_top": [], "scene_tags_top": [],
                "scenario_fit": {}, "mentioned_pois": []}

    risk_counter: Counter[str] = Counter()
    scene_counter: Counter[str] = Counter()
    poi_set: set[str] = set()
    for a in aspects:
        risk_counter.update(a.risk_tags())
        scene_counter.update(a.scene_tags())
        if a.poi_name:
            poi_set.add(a.poi_name)

    return {
        "aspect_counts": dict(Counter(a.aspect_type for a in aspects).most_common()),
        "risk_tags_top": risk_counter.most_common(8),
        "scene_tags_top": scene_counter.most_common(8),
        "scenario_fit": fetch_scenario_fit(area_anchor),
        "mentioned_pois": sorted(poi_set),
    }


# ============================================================
# Helpers
# ============================================================

def _row_to_aspect(row: dict) -> Aspect:
    nv_raw = row.get("normalized_value_json") or "{}"
    try:
        nv = json.loads(nv_raw)
    except json.JSONDecodeError:
        nv = {}
    intensity_raw = row.get("weekend_afternoon_intensity")
    intensity = float(intensity_raw) if intensity_raw is not None else 0.5

    # P0.1 时效字段：从 raw_json 取 dataset_version，再推导 age_days
    raw_json_str = row.get("raw_json") or "{}"
    try:
        raw_meta = json.loads(raw_json_str)
    except json.JSONDecodeError:
        raw_meta = {}
    dataset_version = raw_meta.get("dataset_version") or ""
    age_days = _compute_age_days(dataset_version)
    source_files = raw_meta.get("source_files") or []
    source_count = max(1, len(source_files))

    aspect_type = row.get("aspect_type") or ""
    confidence = float(row.get("confidence") or 0.0)
    decayed = round(confidence * freshness_decay(age_days, _aspect_category(aspect_type)), 3)

    return Aspect(
        record_id=row["record_id"],
        area_anchor=row.get("area_anchor") or "",
        poi_name=row.get("poi_name") or "",
        aspect_type=aspect_type,
        sentiment=row.get("sentiment") or "",
        confidence=confidence,
        time_bucket=row.get("time_bucket"),
        needs_review=bool(row.get("needs_review")),
        evidence_summary=row.get("evidence_summary") or "",
        normalized_value=nv,
        weekend_afternoon_intensity=intensity,
        dataset_version=dataset_version,
        evidence_age_days=age_days,
        evidence_source_count=source_count,
        decayed_confidence=decayed,
    )


# ============================================================
# P0.1 red flags（信号 2：必须把吐槽点出来）
# ============================================================

def extract_red_flags(
    poi_name: Optional[str] = None,
    area_anchor: Optional[str] = None,
    top_k: int = 1,
) -> list[dict]:
    """提取一家 POI 的"最关键吐槽"——每张卡片必显示 1 条，即使整体推荐。

    设计：
    - 仅从 sentiment=negative 的 aspects 里挑
    - 排序：confidence × freshness_decay 降序
    - 同 POI 同 aspect_type 取最新（age_days 最小）一条
    - 返回带 raw_text + age_days + source_count + conflict_count

    Returns:
        [
            {
                "aspect_type": "queue",
                "evidence_summary": "周末 14-18 点排队 60 分钟",
                "confidence": 0.86,
                "decayed_confidence": 0.43,
                "age_days": 20,
                "source_count": 3,
                "conflicting_signals": 1,   # 同时有 1 条相反正向评价
                "should_dim": False,         # confidence < 0.5 或 age > 30 → UI 标灰
            }
        ]
    """
    aspects = fetch_aspects(area_anchor=area_anchor, poi_name=poi_name)
    if not aspects:
        return []
    # 同 POI 同 aspect_type 的反向票数（用来标 conflicting_signals）
    pos_count: dict[tuple[str, str], int] = defaultdict(int)
    for a in aspects:
        if a.sentiment == "positive":
            pos_count[(a.poi_name, a.aspect_type)] += 1

    negs = [a for a in aspects if a.sentiment == "negative"]
    if not negs:
        return []

    negs.sort(key=lambda a: (a.decayed_confidence, -a.evidence_age_days), reverse=True)

    # 同 (poi, aspect_type) 只保留排第一的
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for a in negs:
        key = (a.poi_name, a.aspect_type)
        if key in seen:
            continue
        seen.add(key)
        should_dim = a.confidence < 0.5 or a.evidence_age_days > 30
        out.append({
            "aspect_type": a.aspect_type,
            "evidence_summary": a.evidence_summary,
            "confidence": a.confidence,
            "decayed_confidence": a.decayed_confidence,
            "age_days": a.evidence_age_days,
            "source_count": a.evidence_source_count,
            "conflicting_signals": pos_count.get(key, 0),
            "should_dim": should_dim,
            "dataset_version": a.dataset_version,
        })
        if len(out) >= top_k:
            break
    return out
