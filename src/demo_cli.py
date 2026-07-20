"""W1 D7 / W2 D1 e2e CLI demo。

跑法：
    python3 src/demo_cli.py
    python3 src/demo_cli.py --persona friends --area "王府井-东单片区"
    BJ_PAL_LLM=longcat python3 src/demo_cli.py     # 真 LongCat
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.types import UserPreferences  # noqa: E402
from application import (  # noqa: E402
    PREFERENCE_PROVIDED_FIELDS,
    PlanRequest,
    PlanningClarificationRequired,
    PlanningService,
)
from clarifications import ClarificationContinuationService  # noqa: E402
from operations import (  # noqa: E402
    SideEffectOperationRepository,
    SideEffectOperationService,
    approve_sandbox_booking,
    build_sandbox_booking_draft,
    execute_next_sandbox_booking,
    request_sandbox_booking,
)
from tools.mock_message import render_im_card  # noqa: E402
from tools.tool_call_log import clear_session, fetch_calls, set_session  # noqa: E402


SCENARIOS = {
    "family": {
        "user_input": "今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。",
        "prefs": UserPreferences(
            persona="family",
            party_size=3,
            has_child=True,
            child_age=5,
            diet_flags=["light_diet"],
            walk_radius_km=1.5,
            budget_per_person=120,
            target_start="14:00",
            duration_hours=4.0,
        ),
        "audience": "spouse",
        "contact": "老婆",
    },
    "friends": {
        "user_input": "跟 4 个朋友周六下午出去玩，2 男 2 女，别太赶，能聊天。",
        "prefs": UserPreferences(
            persona="friends",
            party_size=4,
            walk_radius_km=2.0,
            budget_per_person=250,
            target_start="14:30",
            duration_hours=5.0,
        ),
        "audience": "friend",
        "contact": "@小张",
    },
}

PLANNING_SERVICE = PlanningService()


def banner(text: str, char: str = "="):
    print(char * 70)
    print(f"  {text}")
    print(char * 70)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", choices=["family", "friends"], default="family")
    parser.add_argument("--area", default="五道营-雍和宫片区")
    parser.add_argument(
        "--book",
        action="store_true",
        help="创建报价绑定的沙箱预订请求；不会触发真实下单",
    )
    parser.add_argument(
        "--approve-sandbox-booking",
        action="store_true",
        help="由独立演示审批人确认精确报价，再让沙箱 worker 执行",
    )
    parser.add_argument(
        "--clarification-demo",
        action="store_true",
        help="制造文本 2 人/结构化 4 人冲突，展示可续跑澄清",
    )
    parser.add_argument(
        "--clarification-choice",
        choices=["text", "structured"],
        help="澄清演示采用文本值或结构化值；省略时只打印 continuation",
    )
    args = parser.parse_args()
    if args.approve_sandbox_booking and not args.book:
        parser.error("--approve-sandbox-booking requires --book")

    session_id = f"demo-{uuid.uuid4().hex[:8]}"
    set_session(session_id)
    clear_session(session_id)
    print(f"[session] {session_id}\n")

    sc = SCENARIOS[args.persona]

    # ============================================================
    # Stage 1: Plan
    # ============================================================
    banner("Stage 1 · Planner 生成方案 v1")
    if args.clarification_demo:
        user_input = "下午三点，两个人在三里屯玩三小时"
        preferences = UserPreferences(
            persona=args.persona,
            party_size=4,
            target_start="15:00",
            duration_hours=3,
            raw_input=user_input,
        )
        area_anchor = "三里屯片区"
        provided_fields = frozenset(
            {
                "user_input",
                "preferences.party_size",
                "preferences.target_start",
                "preferences.duration_hours",
            }
        )
    else:
        user_input = sc["user_input"]
        preferences = sc["prefs"]
        area_anchor = args.area
        provided_fields = frozenset(
            {"user_input", "persona", "preferences", "area_anchor"}
        ) | PREFERENCE_PROVIDED_FIELDS
    print(f"用户输入：{user_input}\n")
    request = PlanRequest(
        user_input=user_input,
        persona=args.persona,
        preferences=preferences,
        area_anchor=area_anchor,
        provided_fields=provided_fields,
    )
    result = _execute_with_clarification(
        request,
        choice=args.clarification_choice,
    )
    if result is None:
        return 2
    p1 = result.initial_plan
    p2 = result.final_plan
    events = list(result.reroute_events)
    print(
        f"[data] {result.data_profile.name} · "
        f"{result.data_profile.classification}"
    )
    print(
        f"[requirements] {result.requirements.status} · "
        f"area={result.requirements.resolved_area_anchor}"
    )
    for assumption in result.requirements.assumptions:
        print(f"[assumption] {assumption.field}={assumption.value} · {assumption.reason}")
    applied = [
        entry
        for entry in result.constraints.entries
        if entry.outcome in {"applied", "matched", "merged", "resolved"}
    ]
    print(
        f"[constraints] {result.constraints.version} · "
        f"recognized={len(applied)} · conflicts={len(result.constraints.conflicts)}"
    )
    for entry in applied:
        print(
            f"[constraint] {entry.field}={entry.value} · "
            f"source={entry.source} · evidence={entry.evidence}"
        )
    observation = result.execution
    print(
        f"[execution] {observation.execution_id} · "
        f"spans={observation.operation_counts['span_count']} · "
        f"llm_calls={observation.operation_counts['llm_call_count']} · "
        f"tokens={observation.token_usage.completeness} · "
        f"sha256={observation.artifact_sha256[:12]}…"
    )
    _print_plan(p1, label="v1 方案")

    # ============================================================
    # Stage 2: Probe & Reroute
    # ============================================================
    banner("Stage 2 · 主动余位探针 + Reroute", "-")
    if not events:
        print("✅ 无需 reroute，方案 v1 全程通畅")
    else:
        for ev in events:
            print(f"\n⚠️ 检测到风险：")
            print(f"   step #{ev.failed_step_idx}: {ev.failed_poi_name}")
            print(f"   原因：{ev.reason}")
            print(f"   evidence：")
            for e in ev.evidence[:2]:
                print(f"     • {e}")
            print(f"   → 已切换到：{ev.replacement_poi_name}")
        _print_plan(p2, label="v2 方案（reroute 后）")

    # ============================================================
    # Stage 3: approval-gated sandbox booking
    # ============================================================
    banner("Stage 3 · 审批式沙箱预订", "-")
    if args.book:
        meal = next(
            (step for step in p2.steps if step.kind == "meal" and step.poi_id),
            None,
        )
        if meal is None:
            print("方案中没有可申请预订的餐饮步骤。")
        else:
            amount_minor = (
                result.request.preferences.budget_per_person
                * result.request.preferences.party_size
                * 100
            )
            with tempfile.TemporaryDirectory(prefix="bj-pal-demo-operation-") as tmp:
                operation_service = SideEffectOperationService(
                    repository=SideEffectOperationRepository(
                        Path(tmp) / "operations.db"
                    )
                )
                draft = build_sandbox_booking_draft(
                    session_id=session_id,
                    poi_id=meal.poi_id,
                    poi_name=meal.poi_name,
                    target_time=meal.start_time,
                    party_size=result.request.preferences.party_size,
                    amount_minor=amount_minor,
                )
                requested = request_sandbox_booking(operation_service, draft)
                print(
                    f"[request] operation={requested.operation_id} "
                    f"status={requested.status}"
                )
                print(
                    f"[quote] CNY {requested.quote.amount_minor / 100:.2f} · "
                    f"valid_until={requested.quote.valid_until} · sandbox=true"
                )
                print(f"[approval] sha256={requested.approval_sha256}")
                if args.approve_sandbox_booking:
                    approved = approve_sandbox_booking(
                        operation_service,
                        requested,
                    )
                    executed = execute_next_sandbox_booking(operation_service)
                    if executed is None or executed.operation_id != requested.operation_id:
                        raise RuntimeError("sandbox worker did not execute the approved request")
                    print(
                        f"[approval] actor={approved.approved_by} "
                        f"status={approved.status}"
                    )
                    print(
                        f"[worker] status={executed.status} "
                        f"receipt_sha256={executed.receipt_sha256}"
                    )
                    print("[safety] sandbox=true；未扣款、未联系商家、未发送消息。")
                else:
                    print(
                        "[approval] 尚未批准；追加 --approve-sandbox-booking "
                        "可演练独立审批与 worker 回执。"
                    )
    else:
        print("（跳过沙箱预订请求；加 --book 创建待审批操作）")

    # ============================================================
    # Stage 4: 话术化 IM 卡片
    # ============================================================
    banner("Stage 4 · 话术化 IM 卡片", "-")
    card = render_im_card(p2, audience=sc["audience"])
    print(f"\n[卡片标题] {card.title}")
    print(f"[发送对象] {sc['contact']}")
    print("[卡片正文]")
    print("-" * 50)
    print(card.body)
    print("-" * 50)
    print(f"[操作按钮] {[a['label'] for a in card.actions]}\n")

    print("[safety] 这里只渲染消息预览，不执行发送。")

    # ============================================================
    # Stage 5: Tool Call Log
    # ============================================================
    banner("Stage 5 · Tool Call Log（评委 Q&A 用）", "-")
    calls = fetch_calls(session_id=session_id, limit=50)
    print(f"本 session {len(calls)} 次工具调用：\n")
    for c in reversed(calls[:20]):
        params_brief = (c["params_json"] or "")[:60]
        print(f"   {c['timestamp'][11:19]} [{c['tool_name']:30}] "
              f"{c['latency_ms']:.0f}ms  status={c['status']}  {params_brief}")

    banner(f"Demo 完成 · session={session_id}")
    return 0


def _execute_with_clarification(
    request: PlanRequest,
    *,
    choice: str | None,
    planning_service: PlanningService | None = None,
    continuation_service: ClarificationContinuationService | None = None,
):
    """Execute once, or persist and resume one deterministic clarification."""
    planning_service = planning_service or PLANNING_SERVICE
    try:
        return planning_service.execute(request)
    except PlanningClarificationRequired as exc:
        continuation_service = (
            continuation_service or ClarificationContinuationService()
        )
        session = continuation_service.issue(
            request=request,
            error=exc,
            delivery="sync",
        )
        question = exc.decision.questions[0]
        print(f"[clarification] {question.prompt}")
        print(
            f"[continuation] id={session.continuation_id} "
            f"decision_sha256={session.decision_sha256}"
        )
        for option in session.options:
            print(f"  - {option.option_id}: {option.label}")
        if choice is None:
            print(
                "[continuation] 未执行；追加 --clarification-choice text|structured "
                "可从同一原始请求继续。"
            )
            return None

        option_id = {
            "text": "use_text_value",
            "structured": "use_structured_value",
        }[choice]
        if option_id not in {item.option_id for item in session.options}:
            raise ValueError(
                "the selected CLI clarification choice is not valid for this decision"
            )
        _, resolved_request = continuation_service.resolve_request(
            continuation_id=session.continuation_id,
            delivery="sync",
            option_id=option_id,
        )
        owner = f"cli-{uuid.uuid4().hex}"
        continuation_service.claim_execution(
            continuation_id=session.continuation_id,
            owner=owner,
        )
        try:
            result = planning_service.execute(resolved_request)
            continuation_service.complete(
                continuation_id=session.continuation_id,
                owner=owner,
                result_payload=result.to_dict(),
            )
        except Exception:
            continuation_service.release_execution(
                continuation_id=session.continuation_id,
                owner=owner,
            )
            raise
        print(
            f"[continuation] resumed={option_id} "
            f"effective_party_size={result.request.preferences.party_size}"
        )
        return result


def _print_plan(p, label="方案"):
    print(f"\n📋 {label}（{p.persona}, {p.area_anchor}）：")
    for s in p.steps:
        marker = "🔄" if s.is_rerouted else " "
        route_detail = ""
        if s.travel_options:
            selected = s.travel_options.get(s.mode_to_here) or {}
            source = selected.get("source", "unknown")
            route_detail = (
                f", 入站 {s.travel_time_min}min/{s.travel_distance_m}m {source}"
            )
        print(f"   {marker} {s.step_index}. [{s.kind:9}] {s.start_time} {s.poi_name:25} "
              f"({s.duration_min}min, {s.mode_to_here}{route_detail})")
        if s.rationale:
            r = s.rationale[:100]
            print(f"        💭 {r}")
    if p.summary:
        print(f"   📝 总结：{p.summary}")
    if p.schedule_context:
        schedule = p.schedule_context
        print(
            "   ⏱️ 时间轴："
            f"{schedule.get('status', 'unknown')} · "
            f"{schedule.get('target_start', '?')}–{schedule.get('planned_end', '?')} · "
            f"路程 {schedule.get('travel_minutes', 0)}min · "
            f"超时 {schedule.get('overrun_minutes', 0)}min"
        )


if __name__ == "__main__":
    raise SystemExit(main())
