"""D5 群偏好收敛器 — broadcast 主路径接入。

把 group_dynamics（成员模式检测）+ group_harmony（带权排序）+
mock_message（broadcast 模拟）+ replanner 串成一个收敛循环。

之前 group_dynamics 是孤岛 — 写好但没人调。本模块提供两层接入：

1. 单轮：reroute_with_group_dynamics(plan, history, candidates) -> 用 weights 选替补
2. 多轮：run_convergence_loop(plan, members, max_rounds) -> 端到端 convergence

参考：docs/V2.4_ITERATION_PLAN.md D5 + Round 5（聚焦群体型用户）。
"""

from __future__ import annotations

import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.mock_message import (  # noqa: E402
    ContactResponse,
    GroupMember,
    simulate_group_responses,
)
from tools.types import POI, SearchConstraints  # noqa: E402

from .group_dynamics import (  # noqa: E402
    MemberProfile,
    measure_convergence,
    profile_group,
)
from .group_harmony import group_rank  # noqa: E402
from .replanner import _kind_to_category, _prefs_to_constraints  # noqa: E402
from .tracing import trace_span  # noqa: E402
from .types import Plan, Step, UserPreferences  # noqa: E402


# ============================================================
# 单轮：用 D5 weights 选替补
# ============================================================

@dataclass
class RerouteResult:
    new_plan: Plan
    profiles: dict[str, MemberProfile]
    member_weights: dict[str, float]
    failed_step_idx: int
    old_poi_name: str
    new_poi_name: Optional[str]
    rejection_reasons: list[str]


def _identify_failed_step(
    plan: Plan,
    rejection_reasons: list[str],
) -> int:
    """从 rejection_reasons 推 plan 中最相关的 step。

    简化策略：
    - "spicy" → 找含"辣/麻辣/火锅/川菜"的 step
    - "expensive" → 找 booking 中价格最高 / rationale 含"贵"的
    - "loud" → 含"夜市/酒吧/簋街"
    - 其他 → 默认第一个 meal step
    """
    SPICY_KEYWORDS = ["辣", "麻辣", "火锅", "川菜", "重庆"]
    LOUD_KEYWORDS = ["夜市", "酒吧", "ktv", "簋街"]

    for reason in rejection_reasons:
        if reason == "spicy":
            for i, s in enumerate(plan.steps):
                blob = (s.poi_name or "") + (s.rationale or "")
                if any(kw in blob for kw in SPICY_KEYWORDS):
                    return i
        elif reason == "loud":
            for i, s in enumerate(plan.steps):
                blob = (s.poi_name or "") + (s.rationale or "")
                if any(kw in blob.lower() for kw in [k.lower() for k in LOUD_KEYWORDS]):
                    return i
        elif reason == "expensive":
            # 取有 booking 且 avg_price 最大的
            best = -1
            best_price = 0.0
            for i, s in enumerate(plan.steps):
                if s.booking and isinstance(s.booking.get("avg_price"), (int, float)):
                    p = float(s.booking["avg_price"])
                    if p > best_price:
                        best_price = p
                        best = i
            if best >= 0:
                return best

    # 默认：第 1 个 meal step
    for i, s in enumerate(plan.steps):
        if s.kind == "meal":
            return i
    return 0


def reroute_with_group_dynamics(
    plan: Plan,
    members: list[GroupMember],
    history_by_member: dict[str, list[ContactResponse]],
    *,
    candidates: Optional[list[POI]] = None,
    prefs: Optional[UserPreferences] = None,
    first_responder: Optional[str] = None,
    rejection_reasons: Optional[list[str]] = None,
) -> RerouteResult:
    """单轮 reroute：用 D5 weights 选替补 POI。

    Args:
        plan: 当前 plan
        members: 全部成员
        history_by_member: 累计 broadcast 响应（D5 检测模式的输入）
        candidates: 候选池（None 时按 plan.area_anchor + failed_step.kind 自动拉）
        prefs: 偏好（影响 constraints）
        first_responder: 首响应者（leader 信号）
        rejection_reasons: 当前轮的拒绝原因列表（用来定位 failed step）

    Returns:
        RerouteResult，含新 plan + profile/weights debug 信息
    """
    with trace_span("group_convergence.reroute", attrs={
        "plan_id": plan.plan_id, "n_members": len(members),
    }):
        prefs = prefs or UserPreferences(persona=plan.persona)
        rejection_reasons = rejection_reasons or []

        # 1) profile + weights
        profiles = profile_group(members, history_by_member, first_responder=first_responder)
        weights = {name: p.weight for name, p in profiles.items()}

        # 2) 找 failed step
        failed_idx = _identify_failed_step(plan, rejection_reasons)
        failed = plan.steps[failed_idx]

        # 3) 候选池（自动拉 or 复用）
        if candidates is None:
            cat = _kind_to_category(failed.kind)
            constraints = _prefs_to_constraints(prefs)
            candidates = search_pois(
                area_anchor=plan.area_anchor,
                category=cat,
                constraints=constraints,
                limit=30,
            )
        else:
            constraints = _prefs_to_constraints(prefs)

        # 排除当前 plan 已用 POI
        used_names = {s.poi_name for s in plan.steps if s.poi_name}
        candidates = [c for c in candidates if c.name not in used_names]

        # 4) 带 weights 的 group_rank
        ranked = group_rank(candidates, members, constraints,
                            member_weights=weights)

        # 5) 替换 failed step
        new_plan = deepcopy(plan)
        if ranked:
            new_poi = ranked[0].poi
            new_step = new_plan.steps[failed_idx]
            new_step.poi_name = new_poi.name
            new_step.poi_id = new_poi.id
            new_step.is_rerouted = True
            new_step.reroute_reason = "user_dissent"
            new_step.rationale = (
                f"D5 群收敛 reroute：原 {failed.poi_name} 被 {','.join(rejection_reasons) or '群体'} 否决，"
                f"按成员权重 {weights} 选 {new_poi.name}"
            )
            new_poi_name = new_poi.name
        else:
            new_poi_name = None

        return RerouteResult(
            new_plan=new_plan,
            profiles=profiles,
            member_weights=weights,
            failed_step_idx=failed_idx,
            old_poi_name=failed.poi_name,
            new_poi_name=new_poi_name,
            rejection_reasons=rejection_reasons,
        )


# ============================================================
# 多轮：端到端 convergence loop
# ============================================================

@dataclass
class RoundReport:
    round_index: int
    n_confirmed: int
    n_rejected: int
    n_no_reply: int
    n_waiting: int
    rejection_reasons: list[str]
    member_weights_after: dict[str, float]
    plan_top_step_after: Optional[str]   # 简短描述


@dataclass
class ConvergenceReport:
    final_plan: Plan
    converged: bool
    rounds_used: int
    reason: str   # consensus / max_rounds / veto_loop
    round_reports: list[RoundReport] = field(default_factory=list)
    history_by_member: dict[str, list[ContactResponse]] = field(default_factory=dict)


def run_convergence_loop(
    plan: Plan,
    members: list[GroupMember],
    *,
    prefs: Optional[UserPreferences] = None,
    max_rounds: int = 4,
    rng_seed: Optional[int] = 42,
) -> ConvergenceReport:
    """端到端：N 轮 broadcast + reroute 直到收敛。

    每轮：
      1. simulate_group_responses(plan)
      2. accumulate history
      3. 算 confirmed_rate；≥0.8 → 收敛 break
      4. 否则用 D5 weights 算 reroute → 替换被否决的 step
      5. 进入下一轮
    """
    with trace_span("group_convergence.loop", attrs={
        "plan_id": plan.plan_id, "max_rounds": max_rounds,
    }):
        prefs = prefs or UserPreferences(persona=plan.persona)
        history: dict[str, list[ContactResponse]] = {m.name: [] for m in members}
        round_reports: list[RoundReport] = []
        current_plan = deepcopy(plan)
        rounds_summary = []
        first_responder: Optional[str] = None

        for r_idx in range(1, max_rounds + 1):
            # 1) broadcast 模拟
            seed = (rng_seed + r_idx) if rng_seed is not None else None
            responses = simulate_group_responses(
                current_plan, members,
                force_one_dissent=(r_idx == 1),  # 只第 1 轮强制 dissent
                seed=seed,
            )

            # 累计 history
            for resp in responses:
                history.setdefault(resp.contact, []).append(resp)

            # 第 1 轮记录 leader 信号（首响应）
            if r_idx == 1 and responses:
                # 按 reply_at_ms 找最早响应者
                non_zero = [r for r in responses if r.reply_at_ms > 0]
                if non_zero:
                    first_responder = min(non_zero, key=lambda r: r.reply_at_ms).contact

            # 2) 统计
            n_confirmed = sum(1 for r in responses if r.status == "confirmed")
            n_rejected = sum(1 for r in responses if r.status == "rejected")
            n_no_reply = sum(1 for r in responses if r.status == "no_reply")
            n_waiting = sum(1 for r in responses if r.status == "waiting")
            rejection_reasons = [r.rejection_reason for r in responses if r.status == "rejected"]

            rounds_summary.append({
                "n_confirmed": n_confirmed,
                "n_rejected": n_rejected,
                "n_no_reply": n_no_reply,
            })

            # 3) profile + weights（用累计 history）
            profiles = profile_group(members, history, first_responder=first_responder)
            weights = {n: p.weight for n, p in profiles.items()}

            top_step_repr = (
                f"{current_plan.steps[0].kind}:{current_plan.steps[0].poi_name}"
                if current_plan.steps else "(empty)"
            )
            round_reports.append(RoundReport(
                round_index=r_idx,
                n_confirmed=n_confirmed,
                n_rejected=n_rejected,
                n_no_reply=n_no_reply,
                n_waiting=n_waiting,
                rejection_reasons=rejection_reasons,
                member_weights_after=weights,
                plan_top_step_after=top_step_repr,
            ))

            # 4) 判断收敛
            cr = measure_convergence(rounds_summary, max_rounds=max_rounds)
            if cr.converged:
                return ConvergenceReport(
                    final_plan=current_plan,
                    converged=True,
                    rounds_used=r_idx,
                    reason="consensus",
                    round_reports=round_reports,
                    history_by_member=history,
                )

            # 5) reroute：用 D5 weights 选替补
            if n_rejected > 0:
                rr = reroute_with_group_dynamics(
                    current_plan, members, history,
                    prefs=prefs,
                    first_responder=first_responder,
                    rejection_reasons=rejection_reasons,
                )
                current_plan = rr.new_plan

        # 跑完 max_rounds 仍未收敛
        cr_final = measure_convergence(rounds_summary, max_rounds=max_rounds)
        return ConvergenceReport(
            final_plan=current_plan,
            converged=False,
            rounds_used=max_rounds,
            reason=cr_final.reason,
            round_reports=round_reports,
            history_by_member=history,
        )


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    from agents.planner import plan
    from tools.mock_message import DEMO_FRIEND_GROUP

    prefs = UserPreferences(persona="friends", target_start="14:00",
                             duration_hours=4.0, raw_input="4 人下午溜达")
    p = plan(
        user_input="4 个朋友周六下午雍和宫附近吃饭遛弯",
        persona="friends",
        prefs=prefs,
    )
    print(f"\n初始 plan {p.plan_id}: {len(p.steps)} 步")
    for s in p.steps[:3]:
        print(f"  [{s.step_index}] {s.kind:10} {s.poi_name}")

    report = run_convergence_loop(p, DEMO_FRIEND_GROUP, prefs=prefs, max_rounds=3, rng_seed=42)

    print(f"\n收敛报告：")
    print(f"  converged={report.converged}  rounds={report.rounds_used}  reason={report.reason}")
    print(f"\n每轮：")
    for rr in report.round_reports:
        print(f"  R{rr.round_index}: confirmed={rr.n_confirmed} rejected={rr.n_rejected} "
              f"no_reply={rr.n_no_reply} reasons={rr.rejection_reasons}")
        print(f"      weights={rr.member_weights_after}")

    # final plan
    print(f"\n最终 plan ({report.final_plan.plan_id}):")
    for s in report.final_plan.steps[:5]:
        marker = " (rerouted)" if s.is_rerouted else ""
        print(f"  [{s.step_index}] {s.kind:10} {s.poi_name}{marker}")
