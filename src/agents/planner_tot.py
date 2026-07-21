"""[11] Experimental multi-branch planner — 多分支生成 + 自评分 + 选优。

参考 arxiv:2305.10601 Tree of Thoughts (Yao et al. 2023)。

与 plan() / plan_optw() 的关系：
- plan(): 单次 LLM 调用，便宜（1× LongCat 配额）
- plan_tot(): K 次 LLM 调用 + 自评分（用多样性换正确率，K× 配额）
- plan_optw(): 0 次 LLM 调用，OR-Tools 求全局最优（无 rationale）

设计：
1. 生成 K 个分支，每个分支用不同 (branch_hint, temperature) 组合：
   - balanced: 默认 hint, T=0.3
   - culture-first: "优先选 1 个 culture/landmark 作为 step 1", T=0.5
   - food-first: "优先选 meal 作为 step 1 或 step 2", T=0.5
2. 每个分支独立调 plan()；任一失败不阻塞其他分支
3. 自评分：commonsense + hard constraint + utility + diversity 加权
4. 返回最高分 plan，把 branches 元信息写到 summary 末尾

工程边界：
- 这是同一个 planner 的多提示词分支，不是多个自治 Agent；不在生产主链中启用
- ThreadPoolExecutor 并发时显式复制请求 ContextVar，预算、trace 和 capture 不能旁路
- 失败分支不抛出，记 score=-inf 让它自然落选
- 执行预算异常必须向上抛出，不能伪装成普通分支降级
- Plan 是 dataclass + 普通 dict，pickle 安全
"""
from __future__ import annotations

import contextvars
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .llm_client import LLMClient, get_llm_client  # noqa: E402
from .execution_budget import ExecutionBudgetExceeded  # noqa: E402
from .planner import plan as make_plan  # noqa: E402
from .tracing import trace_span  # noqa: E402
from .types import Plan, UserPreferences  # noqa: E402


# ============================================================
# 默认分支配置
# ============================================================

DEFAULT_BRANCHES: list[dict] = [
    {
        "label": "balanced",
        "hint": "",
        "temperature": 0.3,
    },
    {
        "label": "culture_first",
        "hint": "把 culture / landmark / museum 类放在 step 1 或 step 2，再吃饭、再 citywalk；"
                "首选评分 ≥4.5 的文化点位，给整段下午一个'文化打底'的节奏。",
        "temperature": 0.55,
    },
    {
        "label": "food_first",
        "hint": "把 meal 放在 step 1 或 step 2（先吃后逛），饭后安排 culture / citywalk；"
                "对家庭画像优先儿童友好的小馆子，对朋友画像优先有特色的网红店。",
        "temperature": 0.55,
    },
]

MAX_BRANCHES = 3
BranchPlanner = Callable[..., Plan]


# ============================================================
# 评分
# ============================================================

@dataclass
class BranchScore:
    label: str
    plan: Optional[Plan] = None
    score: float = float("-inf")
    breakdown: dict = field(default_factory=dict)
    error: Optional[str] = None


def score_plan(plan: Plan, prefs: UserPreferences) -> tuple[float, dict]:
    """对一个 plan 自评分。

    分数维度（加权和）：
    - commonsense (0/1, weight=3.0): 步数 + POI 白名单 + 时间单调 + mode 合法 + 不重复
    - hard_constraint (0/1, weight=2.0): 预算 + 步行半径 + 总时长 + diet
    - utility (0-1, weight=2.0): 各 step POI rating 均值 / 5
    - diversity (0-1, weight=1.0): 不同 kind 的 step 数 / max_kinds(=4)
    - rationale_quality (0-1, weight=0.5): 平均 rationale 长度（30-80 内为满分）
    """
    breakdown = {}
    # commonsense
    cs_pass, cs_reason = _check_commonsense(plan)
    breakdown["commonsense"] = {"pass": cs_pass, "reason": cs_reason}

    # hard constraint
    hd_pass, hd_reason = _check_hard_constraint(plan, prefs)
    breakdown["hard_constraint"] = {"pass": hd_pass, "reason": hd_reason}

    # utility（POI rating 均值）
    util = _compute_utility(plan)
    breakdown["utility"] = round(util, 3)

    # diversity
    div = _compute_diversity(plan)
    breakdown["diversity"] = round(div, 3)

    # rationale 质量
    rq = _compute_rationale_quality(plan)
    breakdown["rationale_quality"] = round(rq, 3)

    score = (
        3.0 * (1.0 if cs_pass else 0.0)
        + 2.0 * (1.0 if hd_pass else 0.0)
        + 2.0 * util
        + 1.0 * div
        + 0.5 * rq
    )
    breakdown["total"] = round(score, 3)
    return score, breakdown


def _check_commonsense(plan: Plan) -> tuple[bool, str]:
    """复用 evals/eval_plans.py 同语义的轻量内联实现，避免循环依赖。"""
    steps = [_step_to_dict(s) for s in plan.steps]
    n = len(steps)
    if not (4 <= n <= 8):
        return False, f"步数 {n} 不在 [4,8]"
    # POI 白名单
    try:
        from loader import get_conn
        conn = get_conn()
        wl = {r["id"] for r in conn.execute("SELECT id FROM pois").fetchall()}
        conn.close()
    except Exception:
        wl = None
    if wl is not None:
        for s in steps:
            pid = s.get("poi_id")
            if pid is None:
                if s.get("kind") != "depart":
                    return False, f"#{s.get('step_index')} 无 poi_id 且非 depart"
                continue
            if pid not in wl:
                return False, f"#{s.get('step_index')} 非白名单 POI"
    # 时间单调
    last_end = None
    for s in steps:
        t = _hh(s.get("start_time"))
        d = s.get("duration_min") or 0
        if t is None:
            return False, f"#{s.get('step_index')} 无 start_time"
        if last_end is not None and t < last_end:
            return False, f"#{s.get('step_index')} 时间倒流"
        last_end = t + d
    # mode 合法
    valid = {"walking", "bicycling", "driving", "transit"}
    for s in steps:
        m = s.get("mode_to_here")
        if m and m not in valid:
            return False, f"#{s.get('step_index')} 非法 mode {m}"
    # 不重复 POI
    seen: dict[str, int] = {}
    for s in steps:
        pid = s.get("poi_id")
        if pid:
            seen[pid] = seen.get(pid, 0) + 1
    dups = [pid for pid, n in seen.items() if n > 1]
    if dups:
        return False, f"POI 重复 {dups}"
    return True, ""


def _check_hard_constraint(plan: Plan, prefs: UserPreferences) -> tuple[bool, str]:
    cap_budget = prefs.budget_per_person
    cap_walk = prefs.walk_radius_km
    cap_dur = prefs.duration_hours
    # POI 详情查询
    try:
        from loader import get_conn
        conn = get_conn()
        ids = [s.poi_id for s in plan.steps if s.poi_id]
        if ids:
            placeholders = ",".join(["?"] * len(ids))
            rows = conn.execute(
                f"SELECT id, avg_price, longitude, latitude FROM pois WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            poi_lookup = {r["id"]: dict(r) for r in rows}
        else:
            poi_lookup = {}
        conn.close()
    except Exception:
        poi_lookup = {}
    # 预算
    if cap_budget:
        for s in plan.steps:
            if s.kind not in ("meal", "snack", "rest"):
                continue
            poi = poi_lookup.get(s.poi_id)
            if not poi:
                continue
            price = poi.get("avg_price")
            if price and price > cap_budget * 1.2:
                return False, f"#{s.step_index} ¥{price}>{cap_budget}×1.2"
    # 总时长
    if plan.steps:
        first = _hh(plan.steps[0].start_time)
        last = _hh(plan.steps[-1].start_time)
        last_dur = plan.steps[-1].duration_min or 0
        if first is not None and last is not None:
            total = (last + last_dur) - first
            cap_min = int(cap_dur * 60) + 30
            if total > cap_min:
                return False, f"总时长 {total}min > {cap_min}min"
    # 步行半径（仅检查 walking 模式）
    if cap_walk:
        prev = None
        for s in plan.steps:
            poi = poi_lookup.get(s.poi_id)
            cur = (poi.get("longitude"), poi.get("latitude")) if poi else None
            if prev and cur and s.mode_to_here == "walking":
                d = _haversine_km(prev[0], prev[1], cur[0], cur[1])
                if d > cap_walk * 1.5:
                    return False, f"#{s.step_index} {d:.2f}km > {cap_walk}×1.5"
            if cur and cur[0]:
                prev = cur
    return True, ""


def _compute_utility(plan: Plan) -> float:
    """POI rating 均值 / 5（depart 步跳过）。"""
    try:
        from loader import get_conn
        conn = get_conn()
        ids = [s.poi_id for s in plan.steps if s.poi_id]
        if not ids:
            return 0.0
        placeholders = ",".join(["?"] * len(ids))
        rows = conn.execute(
            f"SELECT id, rating FROM pois WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        conn.close()
        ratings = [r["rating"] for r in rows if r["rating"] is not None]
        if not ratings:
            return 0.0
        return sum(ratings) / len(ratings) / 5.0
    except Exception:
        return 0.0


def _compute_diversity(plan: Plan) -> float:
    """count(unique kind, 不含 depart) / 4。"""
    kinds = {s.kind for s in plan.steps if s.kind != "depart"}
    return min(1.0, len(kinds) / 4.0)


def _compute_rationale_quality(plan: Plan) -> float:
    """rationale 长度 30-80 字为满分；超出按距离衰减。"""
    if not plan.steps:
        return 0.0
    scores = []
    for s in plan.steps:
        if s.kind == "depart":
            continue
        L = len(s.rationale or "")
        if 30 <= L <= 80:
            scores.append(1.0)
        elif L < 30:
            scores.append(max(0.0, L / 30.0))
        else:
            scores.append(max(0.0, 1.0 - (L - 80) / 80.0))
    return sum(scores) / max(1, len(scores))


# ============================================================
# 主接口
# ============================================================

def plan_tot(
    user_input: str,
    persona: str = "family",
    prefs: Optional[UserPreferences] = None,
    area_anchor: str = "五道营-雍和宫片区",
    client: Optional[LLMClient] = None,
    branches: Optional[list[dict]] = None,
    max_workers: int = 3,
    branch_planner: Optional[BranchPlanner] = None,
) -> tuple[Plan, list[BranchScore]]:
    """实验性多分支 planner：生成 K 分支并用确定性规则选优。

    该入口没有接入 PlanningService/HTTP/job 主链。调用者若在请求级预算、
    trace 或 capture 中执行，并发分支会显式继承同一份请求上下文。

    Args:
        branches: list[{label, hint, temperature}]；None 走 DEFAULT_BRANCHES
        max_workers: 并发线程数；LongCat RPM=10 由 limiter 强制串行化，
                     这里 max_workers 主要影响 mock 模式速度
        branch_planner: 测试/评测注入点；生产代码保持 None

    Returns:
        (best_plan, branch_scores)
        best_plan.summary 末尾会附 ToT 调试信息（前若干个分支分数）

    Raises:
        RuntimeError 当所有分支都失败
    """
    prefs = prefs or UserPreferences(persona=persona, raw_input=user_input)
    client = client or get_llm_client()
    branches = DEFAULT_BRANCHES if branches is None else branches
    branches = _validate_branches(branches, max_workers=max_workers)
    branch_planner = branch_planner or make_plan

    with trace_span("planner.plan_tot", attrs={
        "n_branches": len(branches), "max_workers": max_workers,
        "area_anchor": area_anchor, "client": client.name,
        "execution_mode": "experimental_multi_branch_v1",
    }):
        return _plan_tot_inner(user_input, persona, prefs, area_anchor,
                                client, branches, max_workers, branch_planner)


def _plan_tot_inner(user_input, persona, prefs, area_anchor, client,
                     branches, max_workers, branch_planner):
    scores: list[BranchScore] = []

    def _run_branch(b: dict) -> BranchScore:
        bs = BranchScore(label=b["label"])
        with trace_span("tot.branch", attrs={
            "label": b["label"], "temperature": b.get("temperature", 0.3),
        }) as sp:
            try:
                p = branch_planner(
                    user_input=user_input,
                    persona=persona,
                    prefs=prefs,
                    area_anchor=area_anchor,
                    client=client,
                    branch_hint=b.get("hint", ""),
                    temperature=b.get("temperature", 0.3),
                )
                bs.plan = p
                bs.score, bs.breakdown = score_plan(p, prefs)
                sp.set_attribute("score", round(bs.score, 3))
            except ExecutionBudgetExceeded as exc:
                bs.error = f"{type(exc).__name__}: {exc}"
                sp.set_status("error", bs.error)
                raise
            except Exception as exc:  # noqa: BLE001
                bs.error = f"{type(exc).__name__}: {exc}"
                sp.set_status("error", bs.error)
        return bs

    if max_workers > 1 and len(branches) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            # Context 对象不能被多个线程同时进入，因此每个分支复制一份；
            # 其中预算 tracker / capture sink 是共享且各自带锁的请求级对象。
            futs = {
                ex.submit(contextvars.copy_context().run, _run_branch, b): b
                for b in branches
            }
            for fut in as_completed(futs):
                scores.append(fut.result())
    else:
        scores = [_run_branch(b) for b in branches]

    # 排序：按 score 降序，失败分支排最后
    scores.sort(key=lambda s: s.score, reverse=True)

    best = scores[0]
    if best.plan is None:
        errs = "; ".join(f"{s.label}={s.error}" for s in scores if s.error)
        raise RuntimeError(f"ToT 全分支失败：{errs}")

    # 把分支调试信息写到 summary
    debug_parts = [f"{s.label}={s.score:.2f}"
                   if s.plan else f"{s.label}=ERR"
                   for s in scores[:6]]
    best.plan.summary = (best.plan.summary or "") + (
        f" | ToT[{best.label}] " + " ".join(debug_parts)
    )

    return best.plan, scores


def _validate_branches(branches: list[dict], *, max_workers: int) -> list[dict]:
    """Fail closed on an unbounded or ambiguous experimental fan-out."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("max_workers must be an integer")
    if not 1 <= max_workers <= MAX_BRANCHES:
        raise ValueError(f"max_workers must be between 1 and {MAX_BRANCHES}")
    if not isinstance(branches, list) or not 1 <= len(branches) <= MAX_BRANCHES:
        raise ValueError(f"branches must contain between 1 and {MAX_BRANCHES} items")

    labels: set[str] = set()
    validated: list[dict] = []
    for index, branch in enumerate(branches):
        if not isinstance(branch, dict):
            raise ValueError(f"branch #{index + 1} must be an object")
        label = branch.get("label")
        hint = branch.get("hint", "")
        temperature = branch.get("temperature", 0.3)
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"branch #{index + 1} label must be a non-empty string")
        label = label.strip()
        if label in labels:
            raise ValueError(f"duplicate branch label: {label}")
        if not isinstance(hint, str):
            raise ValueError(f"branch {label} hint must be a string")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not 0.0 <= float(temperature) <= 1.0
        ):
            raise ValueError(f"branch {label} temperature must be between 0 and 1")
        labels.add(label)
        validated.append(
            {"label": label, "hint": hint, "temperature": float(temperature)}
        )
    return validated


# ============================================================
# helpers
# ============================================================

def _step_to_dict(s) -> dict:
    return {
        "step_index": s.step_index,
        "kind": s.kind,
        "poi_id": s.poi_id,
        "poi_name": s.poi_name,
        "start_time": s.start_time,
        "duration_min": s.duration_min,
        "mode_to_here": s.mode_to_here,
    }


def _hh(time_str) -> Optional[int]:
    if not time_str:
        return None
    import re
    m = re.match(r"(\d{1,2}):(\d{2})", time_str)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _haversine_km(lng1, lat1, lng2, lat2) -> float:
    from math import radians, sin, cos, asin, sqrt
    if any(v is None for v in (lng1, lat1, lng2, lat2)):
        return 0.0
    lng1, lat1, lng2, lat2 = map(radians, [lng1, lat1, lng2, lat2])
    dlng, dlat = lng2 - lng1, lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))
