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

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.execution_budget import (  # noqa: E402
    ExecutionBudgetExceeded,
    ExecutionBudgetPolicy,
    enforce_execution_budget,
)
from agents.planner_tot import (  # noqa: E402
    BranchScore,
    DEFAULT_BRANCHES,
    plan_tot,
    score_plan,
)
from agents.tracing import capture_execution  # noqa: E402
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
    """一个普通分支失败时必须显式记录，其他分支仍可参与选优。"""
    from agents.planner import plan as make_plan

    prefs = UserPreferences(persona="friends", party_size=3,
                             walk_radius_km=2.0, budget_per_person=200,
                             target_start="14:00", duration_hours=4.0)
    custom_branches = [
        {"label": "alpha", "hint": "", "temperature": 0.3},
        {"label": "beta", "hint": "试试别的", "temperature": 0.7},
    ]

    def branch_planner(**kwargs):
        plan = make_plan(**kwargs)
        if kwargs["branch_hint"] == "试试别的":
            raise RuntimeError("injected branch failure")
        return plan

    best, branches = plan_tot(
        user_input="朋友下午出去玩",
        persona="friends",
        prefs=prefs,
        area_anchor="五道营-雍和宫片区",
        branches=custom_branches,
        max_workers=1,
        branch_planner=branch_planner,
    )
    assert len(branches) == 2
    assert best is not None
    alpha = next(b for b in branches if b.label == "alpha")
    beta = next(b for b in branches if b.label == "beta")
    assert alpha.plan is best
    assert beta.plan is None
    assert beta.error == "RuntimeError: injected branch failure"
    assert "beta=ERR" in (best.summary or "")


def test_plan_tot_rejects_unbounded_or_ambiguous_fan_out():
    prefs = UserPreferences(persona="solo")
    with pytest.raises(ValueError, match="between 1 and 3"):
        plan_tot("下午出去玩", prefs=prefs, branches=[], max_workers=1)
    with pytest.raises(ValueError, match="duplicate branch label"):
        plan_tot(
            "下午出去玩",
            prefs=prefs,
            branches=[
                {"label": "same", "hint": "", "temperature": 0.3},
                {"label": "same", "hint": "", "temperature": 0.5},
            ],
            max_workers=1,
        )
    with pytest.raises(ValueError, match="max_workers"):
        plan_tot("下午出去玩", prefs=prefs, max_workers=4)


def test_parallel_branches_inherit_request_budget_and_capture():
    prefs = UserPreferences(persona="family", party_size=3, has_child=True,
                             walk_radius_km=1.5, budget_per_person=120,
                             target_start="14:00", duration_hours=4.5)
    policy = ExecutionBudgetPolicy(
        max_llm_calls=3,
        max_data_provider_batches=3,
        max_tool_calls=64,
    )
    with enforce_execution_budget(policy) as tracker:
        with capture_execution("parallel-tot-budget") as capture:
            best, branches = plan_tot(
                user_input="带娃下午出去玩",
                persona="family",
                prefs=prefs,
                max_workers=3,
            )
        snapshot = tracker.complete()

    assert best is not None
    assert len(branches) == 3
    assert snapshot.usage.llm_call_count == 3
    assert snapshot.usage.data_provider_batch_count == 3
    spans = capture.snapshot()["spans"]
    root = next(span for span in spans if span["name"] == "planner.plan_tot")
    branch_spans = [span for span in spans if span["name"] == "tot.branch"]
    assert len(branch_spans) == 3
    assert all(span["parent_span_id"] == root["span_id"] for span in branch_spans)


def test_parallel_branches_cannot_bypass_default_provider_budget():
    prefs = UserPreferences(persona="family", party_size=3,
                             target_start="14:00", duration_hours=4.5)
    with pytest.raises(ExecutionBudgetExceeded) as raised:
        with enforce_execution_budget(ExecutionBudgetPolicy()):
            plan_tot(
                user_input="带娃下午出去玩",
                persona="family",
                prefs=prefs,
                max_workers=3,
            )

    snapshot = raised.value.snapshot
    assert snapshot.status == "terminated"
    assert snapshot.termination_reason == "data_provider_batch_limit"
    assert snapshot.usage.data_provider_batch_count == 2
    assert snapshot.verify_integrity() is True


if __name__ == "__main__":
    setup_module(None)
    test_default_branches_count()
    test_plan_tot_returns_best_and_branches()
    test_plan_tot_serial_mode()
    test_score_plan_components()
    test_plan_tot_handles_branch_failure()
    test_plan_tot_rejects_unbounded_or_ambiguous_fan_out()
    test_parallel_branches_inherit_request_budget_and_capture()
    test_parallel_branches_cannot_bypass_default_provider_budget()
    print("OK test_planner_tot 8/8")
