"""朋友 4 人偏好调和（v2 改 8）。

输入：候选 POI 池 + 4 人各自偏好（GroupMember）
输出：RankedPOI[]，reasons 含"X/4 人偏好命中"

策略（简化版 pareto）：
- L1 硬过滤：任一人"绝对不要"（aversion）→ 排除该 POI
- L2 软排序：每人独立打分（0-1）；总分 = 4 人最低分 × 0.5 + 4 人平均分 × 0.5
  - 这样既避免"老好人方案"（最低分太低被惩罚），也奖励"普遍受欢迎"
- reasons 每条 POI 记 "(命中 X/4 人偏好)"
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mock_message import GroupMember  # noqa: E402
from tools.types import POI, RankedPOI, Reason, SearchConstraints  # noqa: E402


# ============================================================
# Aversion / Prefer → POI 信号匹配规则
# ============================================================

AVERSION_BLACKLIST = {
    "spicy": ["辣", "麻辣", "川菜", "重庆", "四川", "火锅", "烧烤", "酸辣"],
    "expensive": [],   # 走 avg_price 数值检查
    "heavy_oil": ["烤鸭", "炸", "烤肉", "韩式烤肉", "炖", "扒"],
    "loud": ["夜市", "酒吧", "ktv", "簋街"],
}

PREFER_BOOST = {
    "coffee": ["咖啡", "Cafe", "café"],
    "dessert": ["甜品", "蛋糕", "糕点", "马卡龙", "甜筒"],
    "photo": [],   # 走 typecode/category：景点 / 文创 / 网红
    "meat": ["烤鸭", "牛排", "烤肉", "羊蝎子", "肉饼"],
    "drink": ["酒", "鸡尾酒", "餐酒", "红酒", "饮品"],
    "indoor": ["室内", "博物馆", "书店"],
    "outdoor": ["公园", "胡同", "街区"],
    "yogurt": ["酸奶"],
    "fruit": ["水果", "鲜果"],
}


def score_poi_for_member(poi: POI, member: GroupMember,
                         budget_per_person: Optional[float] = None) -> tuple[float, list[str]]:
    """单人对 POI 的打分 ∈ [0, 1] + 命中关键词列表。"""
    blob = f"{poi.name or ''} {poi.category_lv2 or ''} {poi.category_lv3 or ''}".lower()
    matched_prefers: list[str] = []
    matched_aversions: list[str] = []

    for av in member.diet_aversion:
        if av == "expensive":
            if budget_per_person and poi.avg_price and poi.avg_price > budget_per_person:
                matched_aversions.append("expensive")
            continue
        keywords = AVERSION_BLACKLIST.get(av, [])
        if any(kw.lower() in blob for kw in keywords):
            matched_aversions.append(av)

    for pf in member.prefers:
        keywords = PREFER_BOOST.get(pf, [])
        if any(kw.lower() in blob for kw in keywords):
            matched_prefers.append(pf)

    # 评分：基础 = 0.5
    score = 0.5
    score += 0.15 * len(matched_prefers)
    score -= 0.30 * len(matched_aversions)  # aversion 惩罚强
    # rating bonus
    if poi.rating:
        score += (poi.rating - 4.0) * 0.1
    return max(0.0, min(score, 1.0)), matched_prefers


def _has_any_aversion_hit(poi: POI, member: GroupMember,
                           budget_per_person: Optional[float] = None) -> bool:
    blob = f"{poi.name or ''} {poi.category_lv2 or ''} {poi.category_lv3 or ''}".lower()
    for av in member.diet_aversion:
        if av == "expensive":
            if budget_per_person and poi.avg_price and poi.avg_price > budget_per_person:
                return True
            continue
        keywords = AVERSION_BLACKLIST.get(av, [])
        if any(kw.lower() in blob for kw in keywords):
            return True
    return False


# ============================================================
# 主接口
# ============================================================

@dataclass
class GroupRankedPOI:
    poi: POI
    score: float                # 总分（0-1）
    member_scores: dict          # {member_name: score}
    member_matches: dict         # {member_name: [matched_prefers]}
    hit_count: int               # X/N 人偏好被满足（matched_prefers 非空）
    reasons: list[Reason] = field(default_factory=list)


def group_rank(
    candidates: list[POI],
    members: list[GroupMember],
    constraints: Optional[SearchConstraints] = None,
    aggregate_by: str = "weighted",
    member_weights: Optional[dict[str, float]] = None,
) -> list[GroupRankedPOI]:
    """4 人调和排序。

    Args:
        aggregate_by:
            - "weighted" (默认): min × 0.5 + avg × 0.5，分值加权法
            - "kemeny":          先 Borda 粗排 → Kemeny 精排（[47][48] 改进点）
                                 优势：保留偏好结构，对 pair 偏好稳定，避开多数暴政
        member_weights: D5 群偏好收敛器输出的 {name: weight}。
                        默认全 1.0；implicit_leader=1.5，silent=0.7，vetoer=0.5。
                        仅作用于 weighted 聚合的 avg 项；min 项保留以防一票否决被忽略。

    Returns:
        ranked list, 按聚合方法对应的顺序
    """
    constraints = constraints or SearchConstraints()
    n_members = len(members)
    weights = member_weights or {m.name: 1.0 for m in members}
    out: list[GroupRankedPOI] = []
    for poi in candidates:
        # L1 硬过滤：任一人 aversion 命中 → 排除
        any_aversion = any(
            _has_any_aversion_hit(poi, m, constraints.budget_per_person)
            for m in members
        )
        if any_aversion:
            continue

        # L2 每人独立打分
        member_scores: dict[str, float] = {}
        member_matches: dict[str, list[str]] = {}
        for m in members:
            s, matched = score_poi_for_member(poi, m, constraints.budget_per_person)
            member_scores[m.name] = round(s, 3)
            member_matches[m.name] = matched
        # 总分 = min × 0.5 + weighted_avg × 0.5
        scores = list(member_scores.values())
        score_min = min(scores) if scores else 0
        # weighted average：每人 score × weight，再除以 weight 总和
        weight_sum = sum(weights.get(m.name, 1.0) for m in members) or 1.0
        score_weighted_avg = sum(
            member_scores[m.name] * weights.get(m.name, 1.0) for m in members
        ) / weight_sum
        total = score_min * 0.5 + score_weighted_avg * 0.5

        hit_count = sum(1 for matches in member_matches.values() if matches)
        # 构建 reasons
        # 用 weighted_avg 替代单纯 avg；evidence 注明权重分布
        weight_repr = ",".join(
            f"{m.name[1:]}:{weights.get(m.name, 1.0):.1f}" for m in members
        )
        reasons = [Reason(
            factor="group_min_score",
            contrib=round(score_min * 0.5, 3),
            evidence=f"{n_members} 人中最低分 {score_min:.2f}（避免老好人方案）",
        ), Reason(
            factor="group_avg_score",   # 保留旧 factor 名，向后兼容
            contrib=round(score_weighted_avg * 0.5, 3),
            evidence=(
                f"{n_members} 人加权平均 {score_weighted_avg:.2f}"
                f"（命中 {hit_count}/{n_members} 人偏好，权重 {weight_repr}）"
            ),
        )]
        # 命中明细
        details = "; ".join(
            f"{name[1:]}: {','.join(matches)}"
            for name, matches in member_matches.items() if matches
        )
        if details:
            reasons.append(Reason(
                factor="member_prefers_hit",
                contrib=0.0,
                evidence=f"命中明细：{details}",
            ))

        out.append(GroupRankedPOI(
            poi=poi, score=round(total, 3),
            member_scores=member_scores,
            member_matches=member_matches,
            hit_count=hit_count,
            reasons=reasons,
        ))

    if aggregate_by == "kemeny":
        return _reorder_by_kemeny(out, members)

    out.sort(key=lambda r: r.score, reverse=True)
    return out


def _reorder_by_kemeny(
    ranked: list[GroupRankedPOI],
    members: list[GroupMember],
) -> list[GroupRankedPOI]:
    """把 score-based 排序改写为 Kemeny 共识排序。

    步骤：
    1. 每个成员按其个人 member_score 倒序得到自己的 ranking
    2. 用 voting.group_consensus 求 Kemeny 最优共识
    3. 把 ranked 重新排成共识顺序，并在 reasons 里加一行 kemeny_rank
    """
    if not ranked or not members:
        return ranked
    from .voting import group_consensus  # 局部 import 避免循环

    poi_id_to_obj = {r.poi.id: r for r in ranked}

    # 每个成员对 ranked POI 的 ranking
    rankings: list[list[str]] = []
    for m in members:
        sorted_pois = sorted(
            ranked,
            key=lambda r: r.member_scores.get(m.name, 0),
            reverse=True,
        )
        rankings.append([r.poi.id for r in sorted_pois])

    consensus = group_consensus(rankings, coarse_top=min(7, len(ranked)))
    consensus_order: list[str] = consensus["kemeny_consensus"]
    disagreement = consensus["kemeny_disagreement"]

    out = []
    for kr_idx, pid in enumerate(consensus_order):
        if pid not in poi_id_to_obj:
            continue
        r = poi_id_to_obj[pid]
        r.reasons.append(Reason(
            factor="kemeny_consensus_rank",
            contrib=0.0,
            evidence=f"Kemeny 共识第 {kr_idx + 1} 位 / {len(consensus_order)}（总分歧 {disagreement} pair-swap）",
        ))
        out.append(r)

    # 没进 Kemeny 精排的（Borda 粗排截断掉的）按原 score 接尾
    seen_ids = {pid for pid in consensus_order if pid in poi_id_to_obj}
    tail = [r for r in ranked if r.poi.id not in seen_ids]
    tail.sort(key=lambda r: r.score, reverse=True)
    out.extend(tail)
    return out
