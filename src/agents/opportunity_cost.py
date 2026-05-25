"""v2.8 D7 可视化升级 — 路线可惜度（opportunity cost）。

回答："如果选 B 而不是 A，损失什么？"

输入：当前 plan + 同片区同类型候选池
输出：OpportunityReport，每步含：
  - chosen: 当前 plan 选择
  - alternative_top: 同类下一名候选
  - score_gap: 分差
  - reason_diff: 哪些维度 chosen 强 / 弱
  - regret_score: 综合可惜度（越高越值得考虑切换）

设计思路（gstack 第一性）：
- 用户付钱不是为了"AI 给个方案"，而是为了"AI 替我扛了选错的责任"
- 但 AI 不能假装没有备选——必须告诉用户"我考虑过这些，理由是 X"
- 这是信任契约的可视化层
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.rank_fuse import fuse_and_rank  # noqa: E402
from tools.types import POI, RankedPOI, Reason, SearchConstraints  # noqa: E402

from .replanner import _kind_to_category, _prefs_to_constraints  # noqa: E402
from .types import Plan, Step, UserPreferences  # noqa: E402


# ============================================================
# 数据结构
# ============================================================

@dataclass
class StepOpportunity:
    step_index: int
    step_kind: str
    chosen_name: str
    chosen_poi_id: Optional[str]
    chosen_score: float
    alternative_name: str
    alternative_poi_id: Optional[str]
    alternative_score: float
    score_gap: float                  # chosen - alternative；负值意味着 alt 其实更高
    chosen_only_factors: list[str]    # chosen 独有的高分 factor
    alt_only_factors: list[str]       # alternative 独有的高分 factor
    regret_score: float                # 0-1，可惜度（alt 更高 / 差距大 → regret 高）
    rationale: str                     # 一句话解释

    def to_dict(self) -> dict:
        return {
            "step_index": self.step_index,
            "step_kind": self.step_kind,
            "chosen": {"name": self.chosen_name, "poi_id": self.chosen_poi_id,
                       "score": self.chosen_score},
            "alternative": {"name": self.alternative_name,
                            "poi_id": self.alternative_poi_id,
                            "score": self.alternative_score},
            "score_gap": self.score_gap,
            "regret_score": self.regret_score,
            "chosen_only_factors": self.chosen_only_factors,
            "alt_only_factors": self.alt_only_factors,
            "rationale": self.rationale,
        }


@dataclass
class OpportunityReport:
    plan_id: str
    steps: list[StepOpportunity] = field(default_factory=list)
    total_regret: float = 0.0          # sum of step regret，体现整 plan 的"可能更好"程度
    high_regret_steps: list[int] = field(default_factory=list)  # regret > 0.5 的 step_index

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "total_regret": self.total_regret,
            "high_regret_steps": self.high_regret_steps,
            "steps": [s.to_dict() for s in self.steps],
        }


# ============================================================
# 计算
# ============================================================

def _factor_set(reasons: list[Reason], threshold: float = 0.0) -> set[str]:
    """从 reasons 中提取 contrib > threshold 的 factor 名集合。"""
    return {r.factor for r in reasons if r.contrib > threshold}


def _compute_regret(score_gap: float, factor_diff_count: int) -> float:
    """可惜度公式：

    - score_gap > 0（chosen > alt）：regret 低，但仍非零（alt 有独有优势）
    - score_gap < 0（alt 比 chosen 高）：regret 高
    - factor_diff_count 越多 → 维度差异越大 → regret 上调

    返回 ∈ [0, 1]
    """
    # 基础：score_gap 反向（alt 高 → regret 高）
    base = max(0.0, -score_gap) * 2.0   # gap=-0.5 → base 1.0
    base = min(1.0, base)
    # 维度差异 boost
    diff_boost = min(0.3, factor_diff_count * 0.05)
    return round(min(1.0, base + diff_boost), 3)


def compute_step_opportunity(
    step: Step,
    candidates: list[POI],
    constraints: SearchConstraints,
    *,
    center: Optional[tuple[float, float]] = None,
    time_context: Optional[str] = None,
    n_alternatives: int = 3,
) -> Optional[StepOpportunity]:
    """单步可惜度计算。

    candidates: 同片区同类型候选池（会自动跑 fuse_and_rank 排序）
    返回 None 表示无法计算（候选不足或 chosen 不在候选）
    """
    if not candidates or not step.poi_id:
        return None

    ranked = fuse_and_rank(candidates, constraints,
                           center=center, time_context=time_context)
    if len(ranked) < 2:
        return None

    chosen_rp = next((r for r in ranked if r.poi.id == step.poi_id), None)
    # 如果 chosen 不在 ranked 里（例如被 hard filter 排除），用 score=0 占位
    if chosen_rp is None:
        chosen_rp = RankedPOI(
            poi=POI(id=step.poi_id, name=step.poi_name,
                    category_lv1="", category_lv2=None, category_lv3=None,
                    typecode=None, district=None, business_area=None,
                    address=None, longitude=None, latitude=None,
                    rating=None, avg_price=None, open_time=None,
                    phone=None, photos=[]),
            score=0.0,
            reasons=[],
        )

    # 取 alternative：跳过 chosen 之外排名最高的（最有竞争力的备选）
    alts = [r for r in ranked if r.poi.id != step.poi_id][:n_alternatives]
    if not alts:
        return None
    alt_rp = alts[0]

    chosen_factors = _factor_set(chosen_rp.reasons, threshold=0.0)
    alt_factors = _factor_set(alt_rp.reasons, threshold=0.0)
    chosen_only = sorted(chosen_factors - alt_factors)
    alt_only = sorted(alt_factors - chosen_factors)

    score_gap = round(chosen_rp.score - alt_rp.score, 4)
    diff_count = len(chosen_only) + len(alt_only)
    regret = _compute_regret(score_gap, diff_count)

    if score_gap >= 0.05:
        rationale = (
            f"选了 {chosen_rp.poi.name}（{chosen_rp.score:.2f}），"
            f"备选 {alt_rp.poi.name}（{alt_rp.score:.2f}）差距 {score_gap:+.2f}，"
            f"chosen 独有：{','.join(chosen_only[:2]) or '无明显优势'}"
        )
    elif score_gap >= -0.05:
        rationale = (
            f"{chosen_rp.poi.name} vs {alt_rp.poi.name} 几乎打平 "
            f"({score_gap:+.2f})，差异维度：{','.join(alt_only[:2]) or '/'}"
        )
    else:
        rationale = (
            f"⚠️ 备选 {alt_rp.poi.name} ({alt_rp.score:.2f}) 实际比 "
            f"{chosen_rp.poi.name} ({chosen_rp.score:.2f}) 高 {abs(score_gap):.2f}；"
            f"alt 独有：{','.join(alt_only[:2])}"
        )

    return StepOpportunity(
        step_index=step.step_index,
        step_kind=step.kind,
        chosen_name=step.poi_name,
        chosen_poi_id=step.poi_id,
        chosen_score=round(chosen_rp.score, 4),
        alternative_name=alt_rp.poi.name,
        alternative_poi_id=alt_rp.poi.id,
        alternative_score=round(alt_rp.score, 4),
        score_gap=score_gap,
        chosen_only_factors=chosen_only,
        alt_only_factors=alt_only,
        regret_score=regret,
        rationale=rationale,
    )


def compute_plan_opportunity(
    plan: Plan,
    *,
    prefs: Optional[UserPreferences] = None,
    time_context: Optional[str] = None,
) -> OpportunityReport:
    """对整 plan 跑 opportunity cost。

    每个有 poi_id 的步骤：拉同片区同类型候选 → 计算 alternative 对比。
    """
    prefs = prefs or UserPreferences(persona=plan.persona)
    constraints = _prefs_to_constraints(prefs)
    center = resolve_area_center(plan.area_anchor)

    report = OpportunityReport(plan_id=plan.plan_id)

    for step in plan.steps:
        if step.kind == "depart" or not step.poi_id:
            continue
        cat = _kind_to_category(step.kind)
        cands = search_pois(
            area_anchor=plan.area_anchor,
            category=cat,
            constraints=constraints,
            limit=15,
        )
        if not cands:
            continue

        op = compute_step_opportunity(
            step, cands, constraints,
            center=center, time_context=time_context,
        )
        if op is not None:
            report.steps.append(op)

    report.total_regret = round(sum(s.regret_score for s in report.steps), 3)
    report.high_regret_steps = [s.step_index for s in report.steps if s.regret_score > 0.5]
    return report


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    from agents.planner import plan
    from agents.types import UserPreferences

    prefs = UserPreferences(persona="friends", target_start="14:00",
                             duration_hours=4.0, raw_input="4 人雍和宫吃饭")
    p = plan(user_input="4 人周六下午雍和宫吃饭", persona="friends", prefs=prefs)
    print(f"\n初始 plan {p.plan_id}: {len(p.steps)} 步")

    report = compute_plan_opportunity(p, prefs=prefs)
    print(f"\nopportunity report:")
    print(f"  total_regret = {report.total_regret}")
    print(f"  high_regret_steps = {report.high_regret_steps}")
    for op in report.steps:
        print(f"\n  Step {op.step_index} [{op.step_kind}] regret={op.regret_score}")
        print(f"    chosen: {op.chosen_name} ({op.chosen_score})")
        print(f"    alt:    {op.alternative_name} ({op.alternative_score})  gap={op.score_gap:+.3f}")
        print(f"    {op.rationale}")
