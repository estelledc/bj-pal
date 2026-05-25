"""v2.8 D7 验收：路线可惜度（opportunity cost）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.opportunity_cost import (  # noqa: E402
    OpportunityReport,
    StepOpportunity,
    _compute_regret,
    compute_plan_opportunity,
    compute_step_opportunity,
)
from agents.planner import plan  # noqa: E402
from agents.types import Plan, Step, UserPreferences  # noqa: E402
from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.types import SearchConstraints  # noqa: E402


def t1_regret_formula_alt_higher():
    """alt 比 chosen 高 → regret 高。"""
    r1 = _compute_regret(score_gap=-0.3, factor_diff_count=2)
    assert r1 > 0.5, f"expected high regret, got {r1}"
    print(f"\n[1] regret(gap=-0.3) = {r1}")


def t2_regret_formula_chosen_better():
    """chosen 比 alt 高 → regret 低（仅由 factor_diff 贡献）。"""
    r = _compute_regret(score_gap=0.3, factor_diff_count=1)
    assert r < 0.2
    print(f"[2] regret(gap=+0.3, diff=1) = {r}")


def t3_regret_capped_at_one():
    r = _compute_regret(score_gap=-1.0, factor_diff_count=10)
    assert r <= 1.0
    print(f"[3] regret upper bound: {r} ≤ 1.0")


def t4_step_opportunity_basic():
    """单步：chosen vs 候选池中的 alternative。"""
    c = SearchConstraints(persona="friends", min_rating=4.0,
                          walk_radius_km=2.0, budget_per_person=300)
    candidates = search_pois(area_anchor="五道营-雍和宫片区",
                             category="food", constraints=c, limit=15)
    assert len(candidates) >= 2

    # 构造一个 step：选择列表中第 3 名
    chosen_poi = candidates[2]
    step = Step(step_index=2, kind="meal",
                poi_id=chosen_poi.id, poi_name=chosen_poi.name)
    center = resolve_area_center("五道营-雍和宫片区")

    op = compute_step_opportunity(step, candidates, c, center=center)
    assert op is not None
    print(f"[4] step opportunity: chosen={op.chosen_name} alt={op.alternative_name} "
          f"gap={op.score_gap:+.3f} regret={op.regret_score}")


def t5_step_opportunity_no_candidates():
    """候选不足时返回 None。"""
    c = SearchConstraints(persona="friends")
    step = Step(step_index=1, kind="meal", poi_id="x", poi_name="X")
    op = compute_step_opportunity(step, [], c)
    assert op is None
    print(f"[5] 空候选 → None")


def t6_plan_e2e():
    """完整 plan 跑 opportunity_cost。"""
    prefs = UserPreferences(persona="friends", target_start="14:00",
                             duration_hours=4.0, raw_input="4 人雍和宫")
    p = plan(user_input="4 人周六下午雍和宫吃饭", persona="friends", prefs=prefs)
    report = compute_plan_opportunity(p, prefs=prefs)
    assert report.plan_id == p.plan_id
    assert len(report.steps) >= 2
    assert all(s.regret_score >= 0 for s in report.steps)
    assert report.total_regret >= 0
    print(f"\n[6] plan e2e: {len(report.steps)} 步，total_regret={report.total_regret} "
          f"high_regret={report.high_regret_steps}")


def t7_high_regret_steps_flagged():
    """regret > 0.5 的 step 应进 high_regret_steps。"""
    prefs = UserPreferences(persona="friends", raw_input="4 人雍和宫")
    p = plan(user_input="4 人周六下午雍和宫吃饭", persona="friends", prefs=prefs)
    report = compute_plan_opportunity(p, prefs=prefs)
    for s in report.steps:
        if s.regret_score > 0.5:
            assert s.step_index in report.high_regret_steps
    for idx in report.high_regret_steps:
        s = next(x for x in report.steps if x.step_index == idx)
        assert s.regret_score > 0.5
    print(f"[7] high_regret_steps 索引一致")


def t8_rationale_text_quality():
    """rationale 文案应有"chosen ... 备选 ..."格式。"""
    prefs = UserPreferences(persona="friends", raw_input="4 人雍和宫")
    p = plan(user_input="4 人吃饭", persona="friends", prefs=prefs)
    report = compute_plan_opportunity(p, prefs=prefs)
    assert len(report.steps) > 0
    for s in report.steps:
        assert s.chosen_name in s.rationale or "chosen" in s.rationale.lower()
        assert s.alternative_name in s.rationale or "备选" in s.rationale or "alt" in s.rationale.lower()
    print(f"[8] rationale 文案格式 OK")


def t9_to_dict_serializable():
    """OpportunityReport.to_dict 应可 JSON 化（UI / API 用）。"""
    import json
    prefs = UserPreferences(persona="friends", raw_input="4 人雍和宫")
    p = plan(user_input="4 人吃饭", persona="friends", prefs=prefs)
    report = compute_plan_opportunity(p, prefs=prefs)
    s = json.dumps(report.to_dict(), ensure_ascii=False)
    assert "plan_id" in s
    assert "regret_score" in s
    print(f"[9] to_dict JSON OK ({len(s)} chars)")


if __name__ == "__main__":
    t1_regret_formula_alt_higher()
    t2_regret_formula_chosen_better()
    t3_regret_capped_at_one()
    t4_step_opportunity_basic()
    t5_step_opportunity_no_candidates()
    t6_plan_e2e()
    t7_high_regret_steps_flagged()
    t8_rationale_text_quality()
    t9_to_dict_serializable()
    print("\n所有 v2.8 D7 opportunity_cost 验收通过！")
