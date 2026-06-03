"""Plan-and-Execute Planner。

输入：用户一句话（"今天下午带老婆和 5 岁娃出去玩，别离家太远"）+ 偏好
输出：5-7 步结构化 Plan（dataclass + JSON-serializable）

设计：
- 不让 LLM 自由生成 POI——POI 候选池通过 amap_search 给定，LLM 只做"挑哪个 + 编排顺序 + 写理由"
- LLM 必须输出结构化 JSON（schema 在 prompt 里 + 解析后用 Plan.from_dict 校验）
- 离线 mock client 也能跑同样接口，单测不依赖网络
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.rank_fuse import fuse_and_rank  # noqa: E402
from tools.route_lookup import format_modes_compact, lookup_routes, pick_best_mode  # noqa: E402
from tools.types import SearchConstraints  # noqa: E402
from tools.ugc_signals import extract_red_flags, summarize_area  # noqa: E402

from .llm_client import LLMClient, get_llm_client  # noqa: E402
from .plan_tracer import record_step as _tracer_record_step  # noqa: E402
from .tracing import trace_span  # noqa: E402
from .types import Plan, Step, UserPreferences  # noqa: E402


# ============================================================
# Prompt
# ============================================================

PLANNER_SYSTEM = """你是 BJ-Pal 的 Plan-and-Execute Planner。

任务：把用户的一句自然语言目标，转成 5-7 步可执行的下午活动方案。

硬约束：
1. 你**只能从候选 POI 池里选**，不得编造未列出的 POI
2. 输出必须是合法 JSON，schema 见用户消息里的 <schema>
3. 每步必须有 rationale（30-80 字），讲清"为什么这个 POI"
4. 时间衔接：start_time + duration_min ≤ 下一步 start_time
5. 总时长不超过用户指定 duration_hours
6. 5 岁娃 / 减脂 / 不吃辣等约束必须体现在 step 选择和 rationale 里
7. **同一 POI 不可在 plan 中出现两次**（每个 poi_id 唯一，避免回头路）
8. 每步必须有 poi_name 字段，与候选池里的名字一致；depart 收尾步可填"返程"

输出 JSON schema：
```
{
  "persona": "family|friends|solo|with_parents",
  "area_anchor": "...",
  "steps": [
    {
      "step_index": 1,
      "kind": "citywalk|meal|culture|rest|shopping|snack|depart",
      "poi_id": "<候选池中的 id>",
      "poi_name": "<候选池中的 name>",
      "start_time": "HH:MM",
      "duration_min": 60,
      "mode_to_here": "walking|bicycling|driving|transit",
      "rationale": "..."
    }
  ],
  "fallback_strategies": {
    "queue_overflow": "...",
    "weather_bad": "...",
    "child_tired": "..."
  },
  "summary": "一句话方案总结"
}
```

非流式调用时，只输出上方完整 Plan JSON，不要 markdown 代码块。
如果后续提示要求 JSONL 事件流协议，以 JSONL 协议为准，不要再输出单个普通 JSON。
"""

PLANNER_EVENT_STREAM_PROTOCOL = """

当调用方开启流式输出时，改用 JSONL 事件流协议：
1. 每一行都是一个独立 JSON object，不要输出 markdown。
2. 第一行必须立即输出 status，不要等完整方案想好后才开始输出：
   {"event":"status","text":"正在读取用户约束和候选地点"}
3. 在生成 final_plan 前，再输出 2-5 行 status 事件，让用户看到你正在做什么：
   {"event":"status","text":"正在筛选适合孩子的动物相关地点"}
   {"event":"status","text":"正在排除高排队风险餐厅"}
4. 最后一行必须是 final_plan 事件，data 字段就是上方 schema 的完整 Plan JSON：
   {"event":"final_plan","data":{...完整 Plan...}}
5. 不要在 final_plan 之后再输出任何文字。
"""

PLANNER_PREFLIGHT_SYSTEM = """你是 BJ-Pal 的 Planner Preflight。

任务：在完整规划开始前，先用 JSONL 事件流输出 2-3 行 status，让用户立刻看到模型会怎么处理。

要求：
1. 第一行必须立即输出 status。
2. 每行都是一个独立 JSON object。
3. 只允许输出 {"event":"status","text":"..."}。
4. 不要输出 final_plan，不要 markdown。
5. status 要具体，比如正在筛选亲子友好地点、正在避开高排队餐厅、正在平衡步行距离和预算。
"""


# ============================================================
# 主接口
# ============================================================

def plan(
    user_input: str,
    persona: str = "family",
    prefs: Optional[UserPreferences] = None,
    area_anchor: str = "五道营-雍和宫片区",
    client: Optional[LLMClient] = None,
    branch_hint: str = "",
    temperature: float = 0.3,
    user_id: Optional[str] = None,
    on_token: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    on_stream_event: Optional[Callable[[str], None]] = None,
) -> Plan:
    """生成方案。

    Args:
        user_input: 用户一句话
        persona: family/friends/solo/with_parents
        prefs: 已解析的偏好；若 None 则用默认（家庭画像）
        area_anchor: 主活动片区（默认五道营-雍和宫，UGC 数据最厚）
        client: 注入 LLM client；默认 get_llm_client()
        branch_hint: ToT 分支提示词（append 到 user message extra_hint）
        temperature: 采样温度；ToT 不同分支用不同温度增加多样性
        user_id: 跨 session 用户标识。提供时只读取并注入已有记忆；
                 新记忆必须由 UI 的手动记忆入口显式写入。
                 None 时跳过（保持 stateless 行为）
    """
    prefs = prefs or UserPreferences(persona=persona, raw_input=user_input)
    client = client or get_llm_client()

    # 注入 user_memory；不在规划过程中自动写记忆，避免 rerun/reroute 误沉淀。
    augmented_input = user_input
    if user_id:
        from .user_memory import merge_into_prompt
        augmented_input = merge_into_prompt(user_input, user_id)

    with trace_span("planner.plan", attrs={
        "persona": persona, "area_anchor": area_anchor,
        "branch_hint": (branch_hint or "")[:40], "temperature": temperature,
        "client": client.name, "user_id": user_id or "",
    }):
        return _plan_inner(augmented_input, persona, prefs, area_anchor,
                            client, branch_hint, temperature, on_token, on_progress, on_stream_event)


def _plan_inner(user_input, persona, prefs, area_anchor,
                client, branch_hint, temperature, on_token=None, on_progress=None,
                on_stream_event=None):
    _emit_progress(
        on_progress,
        f"查询人数和约束：{prefs.party_size} 人，预算 {prefs.budget_per_person or '不限'}，"
        f"出发 {prefs.target_start}，游玩 {prefs.duration_hours:g} 小时",
    )
    # 1) 拉片区 summary（场景标签 / 风险 / 已提及 POI）
    _emit_progress(on_progress, f"查询片区画像：{area_anchor}")
    with trace_span("planner.summarize_area", attrs={"area": area_anchor}):
        area_ctx = summarize_area(area_anchor)

    # 1.5) v2.6 D4：识别时段画像
    from tools.time_bucket import detect_time_bucket, score_poi_for_bucket
    _emit_progress(on_progress, "识别时间场景：周末/雨天/夜间/餐时等")
    time_detection = detect_time_bucket(user_input)

    # 2) 拉候选 POI 池
    _emit_progress(on_progress, "查询POI候选：餐饮、景点、地标、博物馆、购物")
    with trace_span("planner.search_candidates",
                    attrs={"time_bucket": time_detection.bucket}):
        constraints = _prefs_to_constraints(prefs)
        food = search_pois(area_anchor=area_anchor, category="food", constraints=constraints, limit=12)
        scenic = search_pois(area_anchor=area_anchor, category="scenic", constraints=constraints, limit=8)
        landmark = search_pois(area_anchor=area_anchor, category="landmark", constraints=constraints, limit=6)
        museum = search_pois(area_anchor=area_anchor, category="museum", constraints=constraints, limit=4)
        shopping = search_pois(area_anchor=area_anchor, category="shopping", constraints=constraints, limit=4)

        # v2.6 D4：如果命中时段画像 → 按 time_bucket 对每池重排
        if time_detection.bucket != "none":
            def _rerank(pool: list) -> list:
                scored = [(p, score_poi_for_bucket(p, time_detection.bucket)[0]) for p in pool]
                scored.sort(key=lambda t: t[1], reverse=True)
                return [t[0] for t in scored]
            food = _rerank(food)
            scenic = _rerank(scenic)
            landmark = _rerank(landmark)
            museum = _rerank(museum)
            shopping = _rerank(shopping)

    # 3) 拼用户消息（含 <context> JSON 块和 <schema> 提示）
    _emit_progress(on_progress, "整理候选上下文：合并片区信号和用户约束")
    user_msg = _build_user_message(
        user_input=user_input,
        prefs=prefs,
        area_anchor=area_anchor,
        area_ctx=area_ctx,
        candidates={
            "food": [_poi_brief(p) for p in food],
            "scenic": [_poi_brief(p) for p in scenic],
            "landmark": [_poi_brief(p) for p in landmark],
            "museum": [_poi_brief(p) for p in museum],
            "shopping": [_poi_brief(p) for p in shopping],
        },
        branch_hint=branch_hint,
    )

    # 4) 调 LLM
    if on_token is not None:
        _run_preflight_status(
            client=client,
            user_input=user_input,
            prefs=prefs,
            area_anchor=area_anchor,
            on_token=on_token,
            on_progress=on_progress,
            on_stream_event=on_stream_event,
        )

    _emit_progress(on_progress, f"调用LLM生成结构化方案：{client.name}")
    system_prompt = PLANNER_SYSTEM + (PLANNER_EVENT_STREAM_PROTOCOL if on_token else "")
    resp = client.complete(
        system=system_prompt,
        user=user_msg,
        json_schema={"plan": "Plan"},  # 标记，提示 client 尝试解析 JSON
        temperature=temperature,
        on_token=on_token,
        on_stream_event=on_stream_event,
    )

    # 5) 解析
    _emit_progress(on_progress, "解析模型输出：校验 steps、时间和 POI 字段")
    plan_dict = resp.parsed if resp.parsed and "steps" in resp.parsed else parse_plan_response_text(resp.text)
    if not plan_dict or "steps" not in plan_dict:
        raise RuntimeError(
            f"Planner LLM 返回不可解析。前 300 字：\n{resp.text[:300]}"
        )
    plan = Plan.from_dict(plan_dict)

    # 6) post-process：去重 + 重排序号（[15] 改进点）
    _dedup_and_renumber(plan)

    # 7) 用真实路由数据填 travel_time（v2 改 1）+ 4 模式对比（v2 改 6B）
    _emit_progress(on_progress, "查询路线时间：步行/骑行/驾车/公交")
    _fill_real_travel_times(plan, prefs)

    # 8) v2.4 D1：每步落 plan_tracer（失败不抛）
    _emit_progress(on_progress, "写入计划追踪：用于诊断和校准")
    _record_plan_to_tracer(plan)
    return plan


def _emit_progress(callback, message: str) -> None:
    if callback is not None:
        callback(message)


def _run_preflight_status(
    *,
    client,
    user_input: str,
    prefs: UserPreferences,
    area_anchor: str,
    on_token,
    on_progress,
    on_stream_event,
) -> None:
    """Small streaming call that surfaces model-visible planning intent before full JSON."""
    _emit_progress(on_progress, f"启动模型预分析：{client.name}")
    preflight_user = (
        f"用户需求：{user_input}\n"
        f"片区：{area_anchor}\n"
        f"人数：{prefs.party_size}\n"
        f"孩子：{'有' if prefs.has_child else '无'}"
        f"{f'，{prefs.child_age} 岁' if prefs.child_age else ''}\n"
        f"预算：{prefs.budget_per_person or '不限'}\n"
        f"时间：{prefs.target_start} 出发，约 {prefs.duration_hours:g} 小时\n"
        f"忌口/偏好：{', '.join(prefs.diet_flags) if prefs.diet_flags else '无'}\n"
        "请立即输出 2-3 行 status JSONL。"
    )
    client.complete(
        system=PLANNER_PREFLIGHT_SYSTEM,
        user=preflight_user,
        json_schema=None,
        temperature=0.1,
        on_token=on_token,
        on_stream_event=on_stream_event,
    )


# ============================================================
# [31] OPTW 全局最优 Planner（不调 LLM）
# ============================================================

def plan_optw(
    user_input: str,
    persona: str = "family",
    prefs: Optional[UserPreferences] = None,
    area_anchor: str = "五道营-雍和宫片区",
    min_visits: int = 4,
    max_visits: int = 7,
    candidate_limit: int = 30,
    time_limit_s: float = 5.0,
) -> Plan:
    """OPTW + OR-Tools 求全局最优行程（[31] 改进点）。

    与 plan() 不同：
    - 完全不调 LLM；把候选 POI 喂给 CP-SAT 求 Σ utility 最大的访问序列
    - 满足时窗约束 + 总时长上限
    - 不出 rationale；UI 上可后接一次 LLM "narrative" 调用（[72]）

    适用场景：
    - 离线 demo（无 LongCat 时也能跑）
    - 评估对照（OPTW vs LLM 谁更优）
    - 时间敏感场景（比 LLM 快 10×）
    """
    from .optw_solver import build_travel_matrix, from_ranked_pois, solve_optw
    from tools.amap_search import resolve_area_center
    from tools.rank_fuse import fuse_and_rank

    prefs = prefs or UserPreferences(persona=persona, raw_input=user_input)
    with trace_span("planner.plan_optw", attrs={
        "area_anchor": area_anchor, "min_visits": min_visits,
        "max_visits": max_visits, "candidate_limit": candidate_limit,
    }):
        return _plan_optw_inner(user_input, persona, prefs, area_anchor,
                                 min_visits, max_visits, candidate_limit, time_limit_s,
                                 build_travel_matrix, from_ranked_pois, solve_optw,
                                 resolve_area_center, fuse_and_rank)


def _plan_optw_inner(user_input, persona, prefs, area_anchor,
                     min_visits, max_visits, candidate_limit, time_limit_s,
                     build_travel_matrix, from_ranked_pois, solve_optw,
                     resolve_area_center, fuse_and_rank):

    # 拉候选 POI 池（合并所有类目）
    constraints = _prefs_to_constraints(prefs)
    pool = []
    for cat in ("food", "scenic", "landmark", "museum", "shopping"):
        pool.extend(search_pois(area_anchor=area_anchor, category=cat,
                                constraints=constraints, limit=candidate_limit // 5))
    if not pool:
        raise RuntimeError(f"片区 {area_anchor} 候选 POI 为空")

    center = resolve_area_center(area_anchor)
    if not center:
        raise RuntimeError(f"area_anchor {area_anchor} 无法解析中心点")

    # rank_fuse 给每个 POI 算 utility 分数（[08] 老字号 query 自动开启）
    from tools.heritage_brand import is_heritage_brand_query
    heritage = is_heritage_brand_query(user_input)
    ranked = fuse_and_rank(pool, constraints, center=center,
                            heritage_query=heritage)[:candidate_limit]

    # 转 OPTW 输入
    optw_pois, start_min, end_min = from_ranked_pois(
        ranked,
        target_start=prefs.target_start,
        duration_hours=prefs.duration_hours,
    )
    travel_matrix = build_travel_matrix(optw_pois, start=center)

    # 求解
    result = solve_optw(
        pois=optw_pois,
        start_min=start_min, end_min=end_min,
        travel_matrix=travel_matrix,
        min_visits=min_visits, max_visits=max_visits,
        time_limit_s=time_limit_s,
    )

    if result.solver_status not in ("OPTIMAL", "FEASIBLE"):
        raise RuntimeError(
            f"OPTW solver 未找到可行解：status={result.solver_status}，"
            f"建议放宽 min_visits 或 duration_hours"
        )

    # 包装成 Plan
    poi_by_id = {p.id: p for p in pool}
    optw_poi_by_id = {p.id: p for p in optw_pois}
    steps = []
    for idx, (pid, arr_min) in enumerate(zip(result.sequence, result.arrival_times), start=1):
        poi = poi_by_id.get(pid)
        if not poi:
            continue
        op = optw_poi_by_id[pid]
        steps.append(_make_optw_step(idx, poi, arr_min, op.visit_min))

    # 收尾 depart 步
    depart_min = start_min + result.total_minutes_used
    steps.append(_make_depart_step(len(steps) + 1, depart_min))

    plan = Plan(
        persona=prefs.persona,
        area_anchor=area_anchor,
        steps=steps,
        fallback_strategies={
            "queue_overflow": "若餐厅排队 >30min，切换到本片区同类备选",
            "weather_bad": "户外景点改为室内博物馆类",
        },
        summary=(
            f"OPTW 全局最优 {len(steps)-1} 步路线，"
            f"总效用 {result.total_utility}，"
            f"耗时 {result.total_minutes_used} 分钟"
            f"（求解 {result.solve_time_s}s, status={result.solver_status}）"
        ),
    )
    _fill_real_travel_times(plan, prefs)
    # v2.4 D1：每步落 plan_tracer
    _record_plan_to_tracer(plan)
    return plan


def _make_optw_step(idx: int, poi, arrival_min: int, visit_min: int):
    """从 OPTW 输出造一个 Step。"""
    from .types import Step
    return Step(
        step_index=idx,
        kind="meal" if "餐" in (poi.category_lv2 or "") else "citywalk",
        poi_id=poi.id,
        poi_name=poi.name,
        start_time=f"{arrival_min // 60:02d}:{arrival_min % 60:02d}",
        duration_min=visit_min,
        mode_to_here="walking",
        rationale=f"OPTW 全局最优入选；utility={getattr(poi, 'rating', 0)}",
    )


def _make_depart_step(idx: int, depart_min: int):
    from .types import Step
    return Step(
        step_index=idx,
        kind="depart",
        poi_id=None,
        poi_name="返程",
        start_time=f"{depart_min // 60:02d}:{depart_min % 60:02d}",
        duration_min=0,
        mode_to_here="transit",
        rationale="OPTW solver 给定的总时长上限内返程",
    )


# ============================================================
# v2.4 D1：每个 plan return 前落 plan_tracer
# ============================================================

def _estimate_step_confidence(step: Step) -> float:
    """启发式估算单步置信度 ∈ [0.5, 0.95]。

    依据：
    - base 0.7
    - 有 booking 已落库 +0.1（mock_book 校验过餐位/座位）
    - rationale 含强词（"最高/精选/口碑"）+0.05
    - risk_tags 每多 1 个 -0.05
    - is_rerouted -0.05（refined plan 仍带不确定性）
    - depart 收尾步固定 0.9（确定性高）

    真实场景下这个函数应被 LLM probability / OPTW utility 替换，
    现在用规则版给 ECE 评测一个 baseline。
    """
    if step.kind == "depart":
        return 0.9
    base = 0.7
    if step.booking:
        base += 0.10
    rat = step.rationale or ""
    if any(kw in rat for kw in ("最高", "精选", "口碑", "高度推荐", "最优")):
        base += 0.05
    base -= 0.05 * len(step.risk_tags or [])
    if step.is_rerouted:
        base -= 0.05
    return max(0.5, min(0.95, round(base, 3)))


def _record_plan_to_tracer(plan: Plan) -> None:
    """把 Plan 的每一步落 plan_tracer。

    失败不抛错（trace 不能让业务挂）。
    """
    try:
        for step in plan.steps:
            decision = f"[{step.kind}] {step.poi_name or '(no_poi)'} @ {step.start_time}"
            confidence = _estimate_step_confidence(step)
            evidence = {
                "rationale": (step.rationale or "")[:200],
                "rating": getattr(step, "rating", None),
                "risk_tags": list(step.risk_tags or []),
                "duration_min": step.duration_min,
                "is_rerouted": bool(step.is_rerouted),
                "has_booking": bool(step.booking),
            }
            fallback = None
            if plan.fallback_strategies:
                # plan 级 fallback 一并存到每步，UI 能解释"如果这步出问题怎么办"
                fallback = dict(plan.fallback_strategies)
            _tracer_record_step(
                plan.plan_id,
                step.step_index,
                decision=decision,
                confidence=confidence,
                step_kind=step.kind,
                poi_id=step.poi_id,
                evidence=evidence,
                fallback_action=fallback,
            )
    except Exception:
        # 故意吞掉：trace 不能让 plan 主路径挂
        pass


def _dedup_and_renumber(plan: Plan) -> None:
    """同 poi_id 在 plan 中出现 2+ 次时保留首次出现，删后续重复；重排 step_index 连续。

    保留没有 poi_id 的 step（如 depart 收尾步）。
    LLM 偶尔把同一咖啡馆写在第 2 步又第 4 步，post-process 直接删第 4 步。
    """
    seen: set[str] = set()
    kept = []
    for s in plan.steps:
        pid = s.poi_id
        if pid and pid in seen:
            continue  # 删除重复
        if pid:
            seen.add(pid)
        kept.append(s)
    # 重排序号
    for i, s in enumerate(kept, start=1):
        s.step_index = i
    plan.steps = kept


def _fill_real_travel_times(plan: Plan, prefs: UserPreferences) -> None:
    """对每对相邻 step（含 poi_id），查 amap routes 拿真实 4 模式时间。

    回填到 step.travel_time_min / travel_distance_m / travel_options / mode_to_here。
    """
    # 拉所有 step 的经纬度
    all_ids = [s.poi_id for s in plan.steps if s.poi_id]
    if len(all_ids) < 2:
        return
    coords = _resolve_coords(all_ids)
    has_child = prefs.has_child

    prev_step_idx: Optional[int] = None
    prev_coords: Optional[tuple[float, float]] = None
    for i, step in enumerate(plan.steps):
        if not step.poi_id or step.poi_id not in coords:
            continue
        cur_coords = coords[step.poi_id]
        if prev_coords is not None:
            legs = lookup_routes(*prev_coords, *cur_coords)
            best_mode, _reason = pick_best_mode(legs, has_child=has_child,
                                                 walk_radius_km=prefs.walk_radius_km)
            step.mode_to_here = best_mode  # type: ignore
            chosen = legs.get(best_mode)
            if chosen:
                step.travel_time_min = chosen.duration_min
                step.travel_distance_m = chosen.distance_m
            step.travel_options = {m: leg.to_dict() for m, leg in legs.items()}
        prev_coords = cur_coords
        prev_step_idx = i


def _resolve_coords(poi_ids: list[str]) -> dict[str, tuple[float, float]]:
    if not poi_ids:
        return {}
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from loader import get_conn
    conn = get_conn()
    placeholders = ",".join(["?"] * len(poi_ids))
    rows = conn.execute(
        f"SELECT id, longitude, latitude FROM pois WHERE id IN ({placeholders})",
        poi_ids,
    ).fetchall()
    conn.close()
    return {
        r["id"]: (r["longitude"], r["latitude"])
        for r in rows if r["longitude"] is not None
    }


# ============================================================
# helpers
# ============================================================

def _prefs_to_constraints(p: UserPreferences) -> SearchConstraints:
    return SearchConstraints(
        persona=p.persona,
        party_size=p.party_size,
        has_child=p.has_child,
        child_age=p.child_age,
        diet_flags=list(p.diet_flags),
        walk_radius_km=p.walk_radius_km,
        budget_per_person=p.budget_per_person,
        open_at=f"2026-05-18T{p.target_start}",
        min_rating=4.0,
    )


def _poi_brief(p) -> dict:
    """精简 POI 给 LLM 用，省 token。"""
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category_lv2 or p.category_lv1,
        "rating": p.rating,
        "avg_price": p.avg_price,
        "address": p.address,
        "open_time": p.open_time,
    }


def _build_user_message(*, user_input, prefs, area_anchor, area_ctx, candidates,
                        branch_hint: str = "") -> str:
    from tools.heritage_brand import is_heritage_brand_query

    ctx = {
        "user_input": user_input,
        "persona": prefs.persona,
        "party_size": prefs.party_size,
        "has_child": prefs.has_child,
        "child_age": prefs.child_age,
        "diet_flags": prefs.diet_flags,
        "budget_per_person": prefs.budget_per_person,
        "target_start": prefs.target_start,
        "duration_hours": prefs.duration_hours,
        "area_anchor": area_anchor,
        "area_summary": {
            "scenario_fit": area_ctx.get("scenario_fit", {}),
            "risk_tags_top": area_ctx.get("risk_tags_top", []),
            "scene_tags_top": area_ctx.get("scene_tags_top", []),
            "mentioned_pois": area_ctx.get("mentioned_pois", []),
        },
        "candidates": candidates,
        "heritage_intent": is_heritage_brand_query(user_input),  # [08]
    }
    extra_hint = ""
    if ctx["heritage_intent"]:
        extra_hint = ("\n\n<note>用户表达了想要体验北京老字号的意图。"
                      "选餐饮 step 时优先认含'总店/老店/前门/大栅栏'等关键词的本店分店；"
                      "对全聚德/东来顺/便宜坊/稻香村/护国寺小吃/聚宝源等品牌，"
                      "明显的非总店分店应让位给已知本店。</note>")
    if branch_hint:
        extra_hint += f"\n\n<branch>{branch_hint}</branch>"
    return f"""<context>{json.dumps(ctx, ensure_ascii=False)}</context>

<schema>见 system prompt 里的 JSON schema</schema>{extra_hint}

请按 schema 输出方案。"""


# ============================================================
# P0.2 筛选模式（信号 5：6 人生日饭只用 BJ-Pal 筛餐厅，不交给完全规划）
# ============================================================

def screen_candidates(
    user_input: str,
    persona: str = "family",
    prefs: Optional[UserPreferences] = None,
    area_anchor: str = "五道营-雍和宫片区",
    category: str = "food",
    top_k: int = 8,
) -> dict:
    """筛选模式：返回 ranked 候选 + 各家适合不适合的细节。**不出 plan**。

    适用：6 人生日饭 / 老人首次见 / 家宴等重要场合——AI 只筛餐厅，最终决策用户拍板。

    Returns:
        {
            "mode": "screening",
            "user_input": "...",
            "area_anchor": "...",
            "candidates": [
                {
                    "poi_id": "...",
                    "poi_name": "...",
                    "rating": 4.7,
                    "avg_price": 220,
                    "score": 0.625,
                    "fit_reasons": ["人均 ¥220 在预算内", "评分 4.7 高于片区均值"],
                    "concerns": ["⚠ 周末晚餐排队 60min（UGC 3 条）"],
                    "red_flags": [...],
                }
            ],
            "decision_hint": "AI 只筛了候选，最终选哪家由您决定 ✋"
        }
    """
    prefs = prefs or UserPreferences(persona=persona, raw_input=user_input)
    constraints = _prefs_to_constraints(prefs)
    pois = search_pois(area_anchor=area_anchor, category=category,
                        constraints=constraints, limit=30)
    if not pois:
        return {"mode": "screening", "candidates": [],
                "decision_hint": "片区内无符合预算 + 评分约束的候选"}

    center = resolve_area_center(area_anchor)
    ranked = fuse_and_rank(pois, constraints, center=center)[:top_k]

    out_candidates = []
    for r in ranked:
        flags = extract_red_flags(poi_name=r.poi.name, top_k=1)
        # fit_reasons：从 reasons 里挑 contrib > 0 的
        fit = [f"{rs.factor}: {rs.evidence[:60]}"
               for rs in r.reasons if rs.contrib > 0][:3]
        # concerns：从 reasons 里挑 contrib < 0 的 + red_flags
        concerns = [f"{rs.factor}: {rs.evidence[:60]}"
                    for rs in r.reasons if rs.contrib < 0][:2]
        for f in flags:
            concerns.append(
                f"⚠ [{f['aspect_type']}] {f['evidence_summary'][:60]} "
                f"({f['age_days']}天前)"
            )
        out_candidates.append({
            "poi_id": r.poi.id,
            "poi_name": r.poi.name,
            "rating": r.poi.rating,
            "avg_price": r.poi.avg_price,
            "category": r.poi.category_lv2,
            "address": r.poi.address,
            "score": r.score,
            "fit_reasons": fit,
            "concerns": concerns,
            "red_flags": flags,
        })

    return {
        "mode": "screening",
        "user_input": user_input,
        "area_anchor": area_anchor,
        "category": category,
        "candidates": out_candidates,
        "decision_hint": "AI 只筛了候选，最终选哪家由您决定 ✋",
    }


def parse_plan_response_text(text: str) -> Optional[dict]:
    """Parse either plain Plan JSON or JSONL status/final_plan event stream."""
    raw = str(text or "").strip()
    if not raw:
        return None

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(event, dict)
            and event.get("event") == "final_plan"
        ):
            payload = event.get("data") if isinstance(event.get("data"), dict) else event.get("plan")
            if isinstance(payload, dict):
                return payload

    parsed = _safe_parse_json(raw)
    if not isinstance(parsed, dict):
        return None
    if "steps" in parsed:
        return parsed
    if parsed.get("event") == "final_plan":
        payload = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed.get("plan")
        if isinstance(payload, dict):
            return payload
    return parsed


def _safe_parse_json(text: str) -> Optional[dict]:
    """LLM JSON 输出鲁棒解析（含截断恢复）。"""
    from .llm_robust import repair_json
    return repair_json(text)
