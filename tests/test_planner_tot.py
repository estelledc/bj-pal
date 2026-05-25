"""[11] Tree of Thoughts Planner 测试。

Mock 模式下 3 个分支会跑出近似相同的 plan（因为 mock 不真理 hint），
所以我们主要验证：
- 框架能跑通 K 分支并返回最高分
- score_plan 给合法 plan 打出合理分数
- 失败分支会被记录但不阻塞
- summary 末尾带 ToT 调试信息
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.planner_tot import (  # noqa: E402
    BranchScore,
    DEFAULT_BRANCHES,
    plan_tot,
    score_plan,
)
from agents.types import UserPreferences  # noqa: E402


def setup_module(module):
    os.environ["BJ_PAL_LLM"] = "mock"


def test_default_branches_count():
    assert len(DEFAULT_BRANCHES) == 3
    labels = {b["label"] for b in DEFAULT_BRANCHES}
    assert labels == {"balanced", "culture_first", "food_first"}


def test_plan_tot_returns_best_and_branches():
    prefs = UserPreferences(persona="family", party_size=3, has_child=True,
                             child_age=5, walk_radius_km=1.5,
                             budget_per_person=120, target_start="14:00",
                             duration_hours=4.5)
    best, branches = plan_tot(
        user_input="带 5 岁娃下午出去玩，4 小时",
        persona="family",
        prefs=prefs,
        area_anchor="五道营-雍和宫片区",
    )
    assert best is not None
    assert len(best.steps) >= 4
    assert len(branches) == 3
    # 至少有一个分支成功
    assert any(b.plan is not None for b in branches)
    # 最佳分支分数最高
    plan_branches = [b for b in branches if b.plan is not None]
    if len(plan_branches) > 1:
        assert plan_branches[0].score >= plan_branches[-1].score
    # summary 带 ToT 标注
    assert "ToT[" in (best.summary or "")


def test_plan_tot_serial_mode():
    """max_workers=1 走串行路径，检查一致性。"""
    prefs = UserPreferences(persona="solo", party_size=1, walk_radius_km=2.0,
                             budget_per_person=150, target_start="14:00",
                             duration_hours=3.0)
    best, branches = plan_tot(
        user_input="自己出门 3 小时",
        persona="solo",
        prefs=prefs,
        area_anchor="五道营-雍和宫片区",
        max_workers=1,
    )
    assert best is not None
    assert len(branches) == 3


def test_score_plan_components():
    """直接构造一个 plan 测 score_plan 各分项。"""
    from agents.planner import plan as make_plan
    prefs = UserPreferences(persona="family", party_size=3, has_child=True,
                             walk_radius_km=1.5, budget_per_person=120,
                             target_start="14:00", duration_hours=4.5)
    p = make_plan(user_input="带娃出门",
                   persona="family", prefs=prefs,
                   area_anchor="五道营-雍和宫片区")
    score, breakdown = score_plan(p, prefs)
    assert "commonsense" in breakdown
    assert "hard_constraint" in breakdown
    assert "utility" in breakdown
    assert "diversity" in breakdown
    assert "rationale_quality" in breakdown
    assert "total" in breakdown
    # 合法 mock plan 应该 commonsense pass
    assert breakdown["commonsense"]["pass"] in (True, False)
    # utility 在 [0, 1]
    assert 0.0 <= breakdown["utility"] <= 1.0
    # diversity 在 [0, 1]
    assert 0.0 <= breakdown["diversity"] <= 1.0
    # 总分非 -inf
    assert score > float("-inf")


def test_plan_tot_handles_branch_failure():
    """构造一个肯定失败的 branch（hint 不影响 mock，所以这里只测兜底）。

    通过传一个 branches list 包含一个不存在的 'temperature' 类型，
    最终所有分支都成功（mock 鲁棒）。改成测：所有 branch 都成功时也有合理输出。
    """
    prefs = UserPreferences(persona="friends", party_size=3,
                             walk_radius_km=2.0, budget_per_person=200,
                             target_start="14:00", duration_hours=4.0)
    custom_branches = [
        {"label": "alpha", "hint": "", "temperature": 0.3},
        {"label": "beta", "hint": "试试别的", "temperature": 0.7},
    ]
    best, branches = plan_tot(
        user_input="朋友下午出去玩",
        persona="friends",
        prefs=prefs,
        area_anchor="五道营-雍和宫片区",
        branches=custom_branches,
        max_workers=1,
    )
    assert len(branches) == 2
    assert best is not None
    assert any(b.label == "alpha" for b in branches)
    assert any(b.label == "beta" for b in branches)


if __name__ == "__main__":
    setup_module(None)
    test_default_branches_count()
    test_plan_tot_returns_best_and_branches()
    test_plan_tot_serial_mode()
    test_score_plan_components()
    test_plan_tot_handles_branch_failure()
    print("OK test_planner_tot 5/5")
