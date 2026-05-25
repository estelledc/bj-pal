"""UGC 软信号融合 ranking。

L1 硬过滤 + L2 加权软分 + 每条 RankedPOI 附 reasons[] 含 evidence。

公式（和 plan.md Part I 轮 6 对齐）：
    score = 0.35 * amap_rating_norm
          + 0.30 * ugc_soft_score
          + 0.15 * budget_fit
          + 0.10 * distance_penalty (反向)
          + 0.10 * crowd_penalty    (反向)

reasons 字段直接引用 manual_ugc_seed.jsonl 的 evidence_summary，
评委问"为什么选这家"时一键展开就能答。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .types import POI, RankedPOI, Reason, SearchConstraints, haversine_km  # noqa: E402
from .ugc_signals import fetch_aspects, fetch_risk_signals  # noqa: E402


# ============================================================
# 主接口
# ============================================================

def fuse_and_rank(
    candidates: list[POI],
    constraints: SearchConstraints,
    center: Optional[tuple[float, float]] = None,
    weights: Optional[dict[str, float]] = None,
    heritage_query: bool = False,
    seasonal_aware: bool = True,
    facility_aware: bool = True,
    weather: Optional["WeatherContext"] = None,
    target_dt: Optional["datetime"] = None,
    crowd_aware: bool = True,
    graph_anchor: Optional[str] = None,
    audience_preference: Optional[str] = None,
    driving: bool = False,
) -> list[RankedPOI]:
    """对候选 POI 做 L1 硬过滤 + L2 加权排序。

    Args:
        candidates: search_pois 返回的 POI 列表
        constraints: 用户约束
        center: 当前停留点（lng, lat），用于距离惩罚；None 时不计距离
        weights: 自定义权重；默认见 DEFAULT_WEIGHTS
        heritage_query: True 时对老字号品牌的非总店分店降权（[08] 改进）
    """
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    ranked: list[RankedPOI] = []
    for poi in candidates:
        # L1 硬过滤
        if not _passes_hard_filter(poi, constraints):
            continue
        # L2 软分
        scored = _score_with_reasons(poi, constraints, center, weights)
        # [08] 老字号 query 模式：非总店分店降权 + 加 reason
        if heritage_query:
            _apply_heritage_brand_adjustment(scored)
        # [05] 季节限定：当前月份是 POI 网红期 → boost；劣势期 → demote
        if seasonal_aware:
            _apply_seasonal_adjustment(scored)
        # [01] 设施约束：has_child / wheelchair / driving 时，按 facility flag 加减分
        if facility_aware:
            _apply_facility_adjustment(scored, constraints)
        # [16] 天气降级：恶劣天气下户外大幅降权
        if weather is not None:
            _apply_weather_adjustment(scored, weather)
        # [42] 节假日人流：节假日 + 热门景点 -0.30
        if crowd_aware:
            _apply_crowd_adjustment(scored, target_dt)
        # [22] POI 图邻居：候选与 graph_anchor 的图距离 → boost
        if graph_anchor:
            _apply_graph_neighbor_boost(scored, graph_anchor)
        # [20] 受众分层：local / tourist 视角偏好
        if audience_preference:
            _apply_audience_adjustment(scored, audience_preference)
        # [03] 开车场景：停车难度 → score 调整
        if driving:
            _apply_parking_adjustment(scored, target_dt)
        ranked.append(scored)
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked


def _apply_weather_adjustment(ranked: RankedPOI, weather) -> None:
    """[16] 根据天气状态调整 score。"""
    from .weather_shelter import get_weather_adjust
    delta, why = get_weather_adjust(ranked.poi, weather)
    if delta == 0.0:
        return
    ranked.score = round(ranked.score + delta, 4)
    ranked.reasons.append(Reason(
        factor="weather_shelter_match" if delta > 0 else "weather_outdoor_penalty",
        contrib=delta,
        evidence=why,
    ))


def _apply_graph_neighbor_boost(ranked: RankedPOI, anchor: str) -> None:
    """[22] 候选若是 anchor 的图邻居 → 加分 + reason。

    edge weight 越大（共现 + 同片区 + 邻近）boost 越多。
    """
    from .poi_graph import find_neighbors, _resolve, _GRAPH
    if _GRAPH is None:
        from .poi_graph import build_graph
        build_graph()
    anchor_id = _resolve(anchor)
    if anchor_id is None or anchor_id not in _GRAPH:
        return
    cand_id = ranked.poi.id
    if cand_id not in _GRAPH or not _GRAPH.has_edge(anchor_id, cand_id):
        return
    edge = _GRAPH[anchor_id][cand_id]
    # 边权 → boost：weight 1 → +0.02, weight 5 → +0.10
    boost = min(0.10, edge["weight"] * 0.02)
    ranked.score = round(ranked.score + boost, 4)
    types = ", ".join(edge["types"])
    ranked.reasons.append(Reason(
        factor="graph_neighbor_of_anchor",
        contrib=boost,
        evidence=f"🔗 与 {anchor} 是图邻居（{types}, w={edge['weight']:.1f}）",
    ))


def _apply_parking_adjustment(ranked: RankedPOI, target_dt) -> None:
    """[03] 开车场景：停车难度 → score 调整。"""
    from .parking import get_parking_score_adjust
    delta, why = get_parking_score_adjust(ranked.poi, target_dt)
    if delta == 0.0:
        return
    ranked.score = round(ranked.score + delta, 4)
    ranked.reasons.append(Reason(
        factor="parking_friendly" if delta > 0 else "parking_hard",
        contrib=delta,
        evidence=why,
    ))


def _apply_audience_adjustment(ranked: RankedPOI, preference: str) -> None:
    """[20] 用户视角偏好（local/tourist/mixed）调整。"""
    from .audience_segment import get_audience_score_adjust
    delta, why = get_audience_score_adjust(ranked.poi.name, preference)  # type: ignore
    if delta == 0.0:
        return
    ranked.score = round(ranked.score + delta, 4)
    ranked.reasons.append(Reason(
        factor=f"audience_{preference}_match" if delta > 0 else f"audience_{preference}_mismatch",
        contrib=delta,
        evidence=why,
    ))


def _apply_crowd_adjustment(ranked: RankedPOI, target_dt) -> None:
    """[42] 节假日 / 周末高峰人流调整。"""
    from .crowd_forecast import get_crowd_score_adjust
    delta, why = get_crowd_score_adjust(ranked.poi.name, target_dt)
    if delta == 0.0:
        return
    ranked.score = round(ranked.score + delta, 4)
    ranked.reasons.append(Reason(
        factor="holiday_crowd_penalty",
        contrib=delta,
        evidence=why,
    ))


def _apply_facility_adjustment(ranked: RankedPOI, constraints: SearchConstraints) -> None:
    """[01] 根据用户约束 + POI facility flag 加减分。"""
    from .facilities import get_facility_score_adjust
    has_child = bool(constraints.has_child) and (
        constraints.child_age is None or constraints.child_age <= 8
    )
    wheelchair = bool(getattr(constraints, "wheelchair", False))
    driving = False  # 当前 SearchConstraints 没 driving 字段，先 False
    delta, reasons = get_facility_score_adjust(
        ranked.poi.name,
        has_child=has_child,
        wheelchair=wheelchair,
        driving=driving,
    )
    if delta == 0.0 and not reasons:
        return
    ranked.score = round(ranked.score + delta, 4)
    for evd in reasons:
        ranked.reasons.append(Reason(
            factor="facility_match" if delta > 0 else "facility_blocker",
            contrib=delta if reasons.index(evd) == 0 else 0.0,
            evidence=evd,
        ))


def _apply_seasonal_adjustment(ranked: RankedPOI) -> None:
    """[05] 当前月份与 POI 季节峰值匹配 → 加分 / 减分 + reason。"""
    from .seasonal import get_season_match

    match = get_season_match(ranked.poi.name)
    adjust = match["score_adjust"]
    if adjust == 0.0:
        return
    ranked.score = round(ranked.score + adjust, 4)
    ranked.reasons.append(Reason(
        factor="seasonal_peak" if adjust > 0 else "seasonal_avoid",
        contrib=adjust,
        evidence=match["reason"][:120],
    ))


def _apply_heritage_brand_adjustment(ranked: RankedPOI) -> None:
    """[08] 老字号 query 时调整分数。

    - 是总店 → score ×1.15 + reason ("总店认证")
    - 是分店但评分达标 → 不变 + reason ("分店但评分达标")
    - 不是老字号 → score ×0.85（推老字号场景下不老字号被降权）
    """
    from .heritage_brand import identify_brand

    info = identify_brand(ranked.poi.name)
    if info is None:
        # 不是老字号，在 heritage_query 场景下降权
        ranked.score = round(ranked.score * 0.85, 4)
        ranked.reasons.append(Reason(
            factor="heritage_query_non_brand",
            contrib=-0.05,
            evidence="（非老字号，在'要老字号'场景下降权）",
        ))
        return

    if info.is_flagship:
        ranked.score = round(ranked.score * 1.15, 4)
        ranked.reasons.append(Reason(
            factor="heritage_brand_flagship",
            contrib=0.10,
            evidence=f"⭐ {info.brand}总店认证（{info.founded_year}年创立 · {info.category}）",
        ))
    else:
        cap = info.branch_min_acceptable_rating
        rating = ranked.poi.rating or 0
        if rating >= cap:
            ranked.reasons.append(Reason(
                factor="heritage_brand_branch_acceptable",
                contrib=0.0,
                evidence=f"{info.brand}分店（评分 {rating} ≥ 品牌底线 {cap}，可接受）",
            ))
        else:
            ranked.score = round(ranked.score * 0.7, 4)
            ranked.reasons.append(Reason(
                factor="heritage_brand_branch_subpar",
                contrib=-0.10,
                evidence=f"⚠️ {info.brand}分店但评分 {rating} < 品牌底线 {cap}（建议优先认总店）",
            ))


# ============================================================
# L1 硬过滤
# ============================================================

def _passes_hard_filter(poi: POI, c: SearchConstraints) -> bool:
    # 评分门槛
    if c.min_rating > 0 and (poi.rating is None or poi.rating < c.min_rating):
        return False
    # 预算超标 1.2 倍直接砍（餐饮 only）
    if poi.category_lv1 == "餐饮服务" and c.budget_per_person and poi.avg_price:
        if poi.avg_price > c.budget_per_person * 1.2:
            return False
    # 5 岁娃不能进酒吧 / 夜店
    if c.has_child and c.child_age and c.child_age <= 6:
        if _looks_adult_only(poi):
            return False
    return True


def _looks_adult_only(poi: POI) -> bool:
    blacklist = ["酒吧", "夜店", "清吧", "ktv", "电竞", "lounge"]
    blob = f"{poi.name or ''} {poi.category_lv2 or ''} {poi.category_lv3 or ''}".lower()
    return any(kw in blob for kw in blacklist)


# ============================================================
# L2 软评分
# ============================================================

DEFAULT_WEIGHTS: dict[str, float] = {
    "rating": 0.35,
    "ugc_soft": 0.30,
    "budget": 0.15,
    "distance": 0.10,
    "crowd": 0.10,
}


def _score_with_reasons(
    poi: POI,
    c: SearchConstraints,
    center: Optional[tuple[float, float]],
    w: dict[str, float],
) -> RankedPOI:
    reasons: list[Reason] = []
    risk_tags: list[str] = []

    # ---- 1. 高德 rating ----
    rating_norm = (poi.rating or 0) / 5.0
    rating_contrib = rating_norm * w["rating"]
    reasons.append(Reason(
        factor="amap_rating",
        contrib=round(rating_contrib, 3),
        evidence=f"高德评分 {poi.rating or '无'}/5.0",
    ))

    # ---- 2. UGC 软分 ----
    ugc_score, ugc_evidence = _ugc_signal(poi)
    ugc_contrib = ugc_score * w["ugc_soft"]
    if ugc_evidence:
        reasons.append(Reason(
            factor="ugc_soft_score",
            contrib=round(ugc_contrib, 3),
            evidence=ugc_evidence,
        ))

    # ---- 3. 预算契合度 ----
    budget_score = _budget_fit(poi, c)
    budget_contrib = budget_score * w["budget"]
    if c.budget_per_person and poi.avg_price:
        if poi.avg_price <= c.budget_per_person * 0.8:
            ev = f"人均 ¥{poi.avg_price:.0f} 远低于预算 ¥{c.budget_per_person:.0f}"
        elif poi.avg_price <= c.budget_per_person:
            ev = f"人均 ¥{poi.avg_price:.0f} 在预算 ¥{c.budget_per_person:.0f} 内"
        else:
            ev = f"人均 ¥{poi.avg_price:.0f} 略超预算 ¥{c.budget_per_person:.0f}"
        reasons.append(Reason(
            factor="budget_fit",
            contrib=round(budget_contrib, 3),
            evidence=ev,
        ))

    # ---- 4. 距离惩罚（步行可达） ----
    distance_contrib = 0.0
    if center and poi.longitude and poi.latitude:
        dist_km = haversine_km(center[0], center[1], poi.longitude, poi.latitude)
        # 1.5km 内满分，往外线性衰减；超 c.walk_radius_km 视为 0
        if dist_km <= c.walk_radius_km:
            d_norm = max(0.0, 1.0 - dist_km / max(c.walk_radius_km, 0.5))
        else:
            d_norm = 0.0
        distance_contrib = d_norm * w["distance"]
        reasons.append(Reason(
            factor="distance",
            contrib=round(distance_contrib, 3),
            evidence=f"距上一站直线 {dist_km:.2f} km，步行 {int(dist_km * 12)} min",
        ))

    # ---- 5. 拥堵惩罚 ----
    crowd_pen, crowd_evidence, crowd_tags = _crowd_penalty(poi)
    crowd_contrib = -crowd_pen * w["crowd"]  # 反向
    if crowd_tags:
        risk_tags.extend(crowd_tags)
    if crowd_evidence:
        reasons.append(Reason(
            factor="crowd_penalty",
            contrib=round(crowd_contrib, 3),
            evidence=crowd_evidence,
        ))

    total = (
        rating_contrib
        + ugc_contrib
        + budget_contrib
        + distance_contrib
        + crowd_contrib
    )
    return RankedPOI(poi=poi, score=round(total, 3), reasons=reasons, risk_tags=risk_tags)


# ============================================================
# helpers
# ============================================================

def _ugc_signal(poi: POI, weekend_afternoon_focus: bool = True) -> tuple[float, str]:
    """该 POI 的 UGC 综合软分（[-1, 1]）+ 一句话证据。

    Args:
        weekend_afternoon_focus: Task 1.2 加 — True 时按 intensity 加权，
            周六下午强相关 aspect 影响 ranking 更多；False 等同 v1 公式。
    """
    aspects = fetch_aspects(poi_name=poi.name)
    if not aspects:
        return 0.0, ""
    total = 0.0
    weight_sum = 0.0
    pos_evi: list[tuple[float, str]] = []  # (intensity, evidence) 用于按 intensity 排
    neg_evi: list[tuple[float, str]] = []
    for a in aspects:
        sign = {"positive": 1.0, "negative": -1.0, "mixed": 0.0}.get(a.sentiment, 0.0)
        weight = a.confidence
        if weekend_afternoon_focus:
            # intensity 0.5 = 中性（不加不减），1.0 强相关、0.1 弱相关
            weight *= a.weekend_afternoon_intensity * 2  # 让 0.5 维持原权重
        total += sign * weight
        weight_sum += weight
        if sign > 0 and a.evidence_summary:
            pos_evi.append((a.weekend_afternoon_intensity, f"[{a.aspect_type}] {a.evidence_summary[:50]}"))
        elif sign < 0 and a.evidence_summary:
            neg_evi.append((a.weekend_afternoon_intensity, f"[{a.aspect_type}] {a.evidence_summary[:50]}"))
    avg = total / max(weight_sum, 0.01)
    avg = max(-1.0, min(1.0, avg))
    # 选 intensity 最高的一条作为 evidence
    pos_evi.sort(reverse=True)
    neg_evi.sort(reverse=True)
    pick = pos_evi[0][1] if avg > 0 and pos_evi else (neg_evi[0][1] if neg_evi else (pos_evi[0][1] if pos_evi else ""))
    return avg, pick


def _budget_fit(poi: POI, c: SearchConstraints) -> float:
    """人均价 vs 预算契合度，[0, 1]。

    - 在预算 70%-100%：1.0（甜区）
    - 30%-70%：0.8（便宜但还行）
    - <30%：0.5（太便宜，可能太简陋）
    - 100%-120%：0.5（略超）
    - 没价格数据：0.7
    """
    if not c.budget_per_person or not poi.avg_price:
        return 0.7
    ratio = poi.avg_price / c.budget_per_person
    if 0.7 <= ratio <= 1.0:
        return 1.0
    if 0.3 <= ratio < 0.7:
        return 0.8
    if ratio < 0.3:
        return 0.5
    if ratio <= 1.2:
        return 0.5
    return 0.0  # 实际上 hard filter 已过滤


def _crowd_penalty(poi: POI) -> tuple[float, str, list[str]]:
    """拥堵 / 排队 / 预订风险惩罚 ∈ [0, 1]，0=无问题，1=高风险。"""
    risk_aspects = fetch_risk_signals(poi_name=poi.name)
    if not risk_aspects:
        return 0.0, "", []
    pen = 0.0
    tags: list[str] = []
    evidence_parts: list[str] = []
    for a in risk_aspects:
        if a.sentiment == "negative":
            pen += a.confidence * 0.5
        elif a.sentiment == "mixed":
            pen += a.confidence * 0.2
        tags.extend(a.risk_tags())
        if a.evidence_summary:
            evidence_parts.append(f"[{a.aspect_type}] {a.evidence_summary[:50]}")
    pen = min(pen, 1.0)
    evidence = " | ".join(evidence_parts[:2])
    return pen, evidence, list(set(tags))
