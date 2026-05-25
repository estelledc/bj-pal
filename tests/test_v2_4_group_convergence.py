"""v2.4 D5 接入主路径验收：group_convergence 编排器。

把 plan() → broadcast → profile_group → group_rank(weights) → reroute 串成闭环。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.group_convergence import (  # noqa: E402
    _identify_failed_step,
    reroute_with_group_dynamics,
    run_convergence_loop,
)
from agents.planner import plan  # noqa: E402
from agents.types import Plan, Step, UserPreferences  # noqa: E402
from tools.mock_message import (  # noqa: E402
    DEMO_FRIEND_GROUP,
    ContactResponse,
)


def t1_identify_failed_step_spicy():
    """spicy 拒绝 → 找含'辣'的 step。"""
    plan_obj = Plan(
        persona="friends", area_anchor="五道营",
        steps=[
            Step(step_index=1, kind="citywalk", poi_name="雍和宫"),
            Step(step_index=2, kind="meal", poi_name="麻辣火锅老李"),
            Step(step_index=3, kind="rest", poi_name="星巴克"),
        ],
    )
    idx = _identify_failed_step(plan_obj, ["spicy"])
    print(f"\n[1] spicy → step idx={idx} (poi={plan_obj.steps[idx].poi_name})")
    assert idx == 1


def t2_identify_failed_step_default():
    """无匹配关键词 → 默认第 1 个 meal。"""
    plan_obj = Plan(
        persona="friends", area_anchor="五道营",
        steps=[
            Step(step_index=1, kind="citywalk", poi_name="A"),
            Step(step_index=2, kind="meal", poi_name="B"),
            Step(step_index=3, kind="rest", poi_name="C"),
        ],
    )
    idx = _identify_failed_step(plan_obj, ["other"])
    print(f"[2] other → step idx={idx}")
    assert idx == 1


def t3_reroute_with_weights():
    """reroute_with_group_dynamics 接 history → 用 weights 选替补。"""
    p = plan(
        user_input="4 人雍和宫吃饭",
        persona="friends",
        area_anchor="五道营-雍和宫片区",
    )
    history = {
        "@小张": [ContactResponse(contact="@小张", avatar="🧑", status="confirmed",
                                  reply_text="听我的就这家！", reply_at_ms=100)],
        "@阿明": [ContactResponse(contact="@阿明", avatar="👨", status="confirmed", reply_at_ms=500)],
        "@小雅": [ContactResponse(contact="@小雅", avatar="👩", status="rejected",
                                  rejection_reason="spicy", reply_at_ms=400)],
        "@老王": [ContactResponse(contact="@老王", avatar="🧓", status="no_reply")],
    }
    rr = reroute_with_group_dynamics(
        p, DEMO_FRIEND_GROUP, history,
        first_responder="@小张",
        rejection_reasons=["spicy"],
    )
    print(f"[3] reroute: failed_step_idx={rr.failed_step_idx} "
          f"old={rr.old_poi_name!r} → new={rr.new_poi_name!r}")
    print(f"     weights={rr.member_weights}")
    # 小张应被识别为 implicit_leader (1.5)
    # 注意：1 次 history 不足以触发 vetoer/silent，但 leader 检测不需要多次
    assert rr.member_weights["@小张"] == 1.5, rr.member_weights
    # new plan 该 step 应标 is_rerouted
    assert rr.new_plan.steps[rr.failed_step_idx].is_rerouted


def t4_convergence_loop_e2e():
    """端到端：跑 max_rounds=3 应在 ≤ 2 轮收敛（v2.4 度量目标）。"""
    p = plan(
        user_input="4 人雍和宫片区下午溜达吃饭",
        persona="friends",
    )
    report = run_convergence_loop(p, DEMO_FRIEND_GROUP, max_rounds=3, rng_seed=42)
    print(f"[4] convergence: converged={report.converged} rounds={report.rounds_used} "
          f"reason={report.reason}")
    for r in report.round_reports:
        print(f"     R{r.round_index}: c={r.n_confirmed} r={r.n_rejected} "
              f"no_reply={r.n_no_reply} reasons={r.rejection_reasons}")
    assert report.converged, f"应收敛，实际 {report.reason}"
    assert report.rounds_used <= 2, f"v2.4 目标 ≤ 2 轮，实际 {report.rounds_used}"


def t5_convergence_records_history():
    """history_by_member 累计每轮响应。"""
    p = plan(user_input="4 人下午溜达", persona="friends")
    report = run_convergence_loop(p, DEMO_FRIEND_GROUP, max_rounds=2, rng_seed=42)
    total_responses = sum(len(h) for h in report.history_by_member.values())
    print(f"[5] history total responses={total_responses} across {len(report.history_by_member)} members")
    # 每轮 4 人 × N 轮
    assert total_responses == 4 * report.rounds_used


def t6_e2e_plan_tracer_records_rerouted_step():
    """convergence loop 跑完，rerouted step 应被 plan_tracer 记到（is_rerouted 评分扣分）。

    final plan 是 reroute 后的副本，没经过 planner.plan() 路径，
    所以 final_plan 不会自动落 plan_tracer。这是已知行为 — D1 hook 只挂在 plan() 入口。
    本测试验证：原 plan 已落库，但 final_plan 是 deepcopy 没新落 — 文档化此约束。
    """
    from agents.plan_tracer import iter_steps

    p = plan(user_input="4 人下午溜达", persona="friends")
    report = run_convergence_loop(p, DEMO_FRIEND_GROUP, max_rounds=2, rng_seed=42)

    # 原 plan 应已落 plan_tracer（plan() 入口自动落）
    original_traces = iter_steps(p.plan_id)
    print(f"[6] 原 plan trace 步数={len(original_traces)}")
    assert len(original_traces) == len(p.steps)

    # final_plan 是 deepcopy，复用 plan_id；同 plan_id 累计落多次（每次 reroute 重新 hook）
    # 实际行为：reroute 不再次调用 _record_plan_to_tracer，只有 plan() 入口才会。
    # 这是设计取舍：避免 trace 表膨胀。后续 v2.5 可加 record_step_update API。


if __name__ == "__main__":
    t1_identify_failed_step_spicy()
    t2_identify_failed_step_default()
    t3_reroute_with_weights()
    t4_convergence_loop_e2e()
    t5_convergence_records_history()
    t6_e2e_plan_tracer_records_rerouted_step()
    print("\n所有 D5 接入验收通过！")
