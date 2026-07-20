"""Replanner：触发 reroute 时把 failed_step 替换为同片区同类型的 fallback。

设计：
- 不重新规划全 plan（避免 demo 时间过长 + LLM 不稳定）
- 从 ranking 顶部里挑一个**和 failed POI 同 category** 但**不在 TRAP_POIS** 的
- 原方案其他 step 时间槽不变（如果新 POI 距离差太多，下一步起始时间会顺延，但 W1 不做这层精算）

W2 D3 可选升级：让 LLM 看 probe_result + reasons 后给更精细的 rerationale 文本。
"""

from __future__ import annotations

import sys
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.availability_probe import ProbeResult, list_trap_pois, probe  # noqa: E402
from tools.rank_fuse import fuse_and_rank  # noqa: E402
from tools.route_enricher import refresh_plan_routes  # noqa: E402
from tools.types import SearchConstraints  # noqa: E402

from .types import Plan, RerouteEvent, Step, UserPreferences  # noqa: E402
from .schedule_reconciler import reconcile_plan_schedule  # noqa: E402


@dataclass(frozen=True)
class ReplacementPolicy:
    """Deterministic hard filters applied before replacement ranking."""

    version: str
    reason: str
    original_kind: str
    source_categories: tuple[str, ...]
    require_full_meal: bool = False
    reject_full_meal: bool = False
    allowed_shelters: tuple[str, ...] = ()

    def accepts(self, poi) -> bool:
        if self.require_full_meal and not _is_full_meal(poi):
            return False
        if self.reject_full_meal and _is_full_meal(poi):
            return False
        if self.allowed_shelters and _weather_shelter(poi) not in self.allowed_shelters:
            return False
        return True


def replan_step(
    original: Plan,
    failed_step_idx: int,
    probe_result: ProbeResult,
    prefs: Optional[UserPreferences] = None,
    excluded_poi_names: Optional[set[str]] = None,
) -> tuple[Plan, RerouteEvent]:
    """把 original.steps[failed_step_idx] 替换为同片区同类型的备选。"""
    prefs = prefs or UserPreferences()
    new_plan = deepcopy(original)
    failed = new_plan.steps[failed_step_idx]

    # 1) 普通失败保持同类替换；weather 必须跨类寻找室内地点，
    # 否则“公园换公园”会让 reroute 看似成功但风险仍然存在。
    policy = _replacement_policy(failed.kind, probe_result.reason)
    constraints = _prefs_to_constraints(prefs)
    raw_candidates = [
        poi
        for category in policy.source_categories
        for poi in search_pois(
            area_anchor=new_plan.area_anchor,
            category=category,
            constraints=constraints,
            limit=20,
        )
    ]
    candidates = list({poi.id: poi for poi in raw_candidates}.values())
    unique_count = len(candidates)
    # 排除：failed POI / trap POI / plan 里其他 step 已选的 POI（避免重复）
    used_names = {s.poi_name for s in new_plan.steps if s.poi_name and s.step_index != failed.step_index}
    session_exclusions = set(excluded_poi_names or set())
    trap_set = {failed.poi_name, *list_trap_pois(), *used_names, *session_exclusions}
    candidates = [c for c in candidates if c.name not in trap_set]
    identity_eligible_count = len(candidates)
    candidates = [candidate for candidate in candidates if policy.accepts(candidate)]
    policy_evidence = {
        **asdict(policy),
        "raw_candidate_count": len(raw_candidates),
        "unique_candidate_count": unique_count,
        "identity_eligible_count": identity_eligible_count,
        "semantic_eligible_count": len(candidates),
    }

    if not candidates:
        # 极端情况：找不到替补，把 step 标记为 cancel
        failed.is_rerouted = True
        failed.rationale = _no_alternative_rationale(
            failed,
            probe_result,
            had_session_exclusions=bool(session_exclusions),
        )
        unchanged = [i for i in range(len(new_plan.steps)) if i != failed_step_idx]
        return new_plan, RerouteEvent(
            failed_step_idx=failed_step_idx,
            failed_poi_name=failed.poi_name,
            reason=f"{probe_result.status}_no_alt",
            evidence=list(probe_result.evidence),
            replacement_poi_name=None,
            change_magnitude="none",
            change_summary_zh=f"找不到 {failed.poi_name} 的替补，第 {failed_step_idx + 1} 站维持原计划但请留意风险",
            unchanged_steps=unchanged,
            notify_strategy="warn_only",
            replacement_policy=policy_evidence,
        )

    # 2) Ranking 选 top 1
    center = resolve_area_center(new_plan.area_anchor)
    ranked = fuse_and_rank(candidates, constraints, center=center)
    if not ranked:
        new = candidates[0]
    else:
        new = ranked[0].poi

    # 3) 替换
    # v2 改 4：根据 reroute reason 写不同 rationale
    user_msg = probe_result.evidence[0] if probe_result.evidence else "换一个"
    reason_text = {
        "queue": f"原 POI 排队 {probe_result.wait_min}min，超阈值",
        "weather": f"⛅ 天气不宜：{probe_result.evidence[0] if probe_result.evidence else '雨天户外'}",
        "closed": "🚫 商家临时停业（设备维护 / 拒单）",
        "user_dissent": f"👤 用户反馈「{user_msg}」",
        "merchant_reject": "❌ 商家拒单（人数超限 / 时间冲突）",
        "none": probe_result.status,
    }.get(probe_result.reason, probe_result.status)

    price_label = f"¥{new.avg_price:.0f}" if new.avg_price else "免费/不详"
    rating_label = f"{new.rating:.1f}" if new.rating else "未评分"
    new_step = Step(
        step_index=failed.step_index,
        kind=_replacement_kind(failed.kind, new, probe_result.reason),
        poi_id=new.id,
        poi_name=new.name,
        start_time=failed.start_time,
        duration_min=failed.duration_min,
        mode_to_here=failed.mode_to_here,
        rationale=(
            f"⚠️ reroute（{reason_text}）→ 切换到 {new.name}"
            f"（{new.category_lv2 or '同类'}, rating {rating_label}, {price_label}）"
        ),
        is_rerouted=True,
        reroute_reason=probe_result.reason,
        risk_tags=[],
        weather_shelter=_weather_shelter(new),
    )
    new_plan.steps[failed_step_idx] = new_step
    new_plan.rerouted_at_step = failed_step_idx
    route_refresh = refresh_plan_routes(
        new_plan,
        prefs,
        changed_step_idx=failed_step_idx,
    ).to_dict()
    new_plan.route_context = route_refresh
    schedule_refresh = reconcile_plan_schedule(new_plan, prefs).to_dict()
    new_plan.schedule_context = schedule_refresh

    # P0.4：判定改动幅度 + 一句话总结 + 通知策略
    magnitude = _classify_magnitude(failed, new, original_area=original.area_anchor,
                                    new_area=new_plan.area_anchor)
    summary_zh = _summary_zh(
        failed,
        new,
        probe_result,
        magnitude,
        replacement_start_time=new_plan.steps[failed_step_idx].start_time,
    )
    if schedule_refresh["time_adjustments"] or schedule_refresh["duration_adjustments"]:
        summary_zh += (
            f"；路线刷新后时间轴已重排，预计 {schedule_refresh['planned_end']} 结束"
        )
    if schedule_refresh["status"] == "overrun":
        summary_zh += f"，仍超出目标 {schedule_refresh['overrun_minutes']} 分钟"
    unchanged = [i for i in range(len(new_plan.steps)) if i != failed_step_idx]
    notify = {
        "small": "group_direct",
        "medium": "private_first",
        "large": "private_first",
    }.get(magnitude, "group_direct")

    return new_plan, RerouteEvent(
        failed_step_idx=failed_step_idx,
        failed_poi_name=failed.poi_name,
        reason=probe_result.reason or f"{probe_result.status}_{probe_result.wait_min}min",
        evidence=list(probe_result.evidence),
        replacement_poi_name=new.name,
        change_magnitude=magnitude,
        change_summary_zh=summary_zh,
        unchanged_steps=unchanged,
        notify_strategy=notify,
        replacement_policy=policy_evidence,
        route_refresh=route_refresh,
        schedule_refresh=schedule_refresh,
    )


def _classify_magnitude(failed: Step, new, original_area: str, new_area: str) -> str:
    """判定改动幅度：small=同片区同类 / medium=换片区或换 category / large=两者都换。

    Args:
        failed: 原 step
        new: 新 POI（POI 对象）
        original_area / new_area: 片区（当前实现 area 不变，但保留参数兼容）
    """
    # 当前 replan_step 强约束 same area + same kind，理论上都是 small。
    # 但当 new POI 的 business_area 和原 area_anchor 不一致 → medium。
    failed_kind = failed.kind
    new_cat = (new.category_lv2 or "").lower()
    new_kind_match = _category_matches_kind(new_cat, failed_kind)

    # 用 business_area 简单匹配 area_anchor
    same_area = True
    if new.business_area and original_area:
        same_area = original_area in (new.business_area or "") or \
                    (new.business_area or "") in original_area

    if new_kind_match and same_area:
        return "small"
    if new_kind_match or same_area:
        return "medium"
    return "large"


def _no_alternative_rationale(
    failed: Step,
    probe_result: ProbeResult,
    *,
    had_session_exclusions: bool,
) -> str:
    """User-facing copy when reroute cannot find a fresh replacement."""
    if probe_result.reason == "user_dissent":
        scope = "已经看过或换过的地点" if had_session_exclusions else "当前方案里的地点"
        return (
            f"⚠️ 暂时没有新的同类替补。系统已避开{scope}，"
            f"当前第 {failed.step_index} 站先保留为 {failed.poi_name}。"
        )
    if probe_result.reason == "queue":
        return (
            f"⚠️ 暂时没有可替换的同类地点。原 POI 预计排队 "
            f"{probe_result.wait_min} 分钟，第 {failed.step_index} 站先保留但请留意风险。"
        )
    return (
        f"⚠️ 暂时没有可替换的同类地点，第 {failed.step_index} 站先保留为 "
        f"{failed.poi_name}，请留意现场状态。"
    )


def _category_matches_kind(category: str, kind: str) -> bool:
    """category 文本和 step.kind 是否同类。"""
    mapping = {
        "meal": ["餐饮", "中餐", "西餐", "餐厅", "饭店"],
        "snack": ["小吃", "甜品", "饮品", "面包"],
        "rest": ["咖啡", "茶", "饮品"],
        "citywalk": ["街区", "胡同", "公园", "广场", "景点"],
        "culture": ["博物馆", "美术馆", "纪念馆", "寺庙", "景点", "文化"],
        "shopping": ["商场", "购物", "店铺"],
    }
    keys = mapping.get(kind, [])
    return any(k in category for k in keys) or not keys


def _summary_zh(
    failed: Step,
    new,
    probe_result: ProbeResult,
    magnitude: str,
    *,
    replacement_start_time: Optional[str] = None,
) -> str:
    """一句话给人看的改动说明。

    例：原 14:00 国子监改为 14:00 雍和宫，因为国子监周末爆 UGC 排队 60min
    """
    cause_map = {
        "queue": f"原 POI 排队 {probe_result.wait_min}min 超阈值",
        "weather": "雨天户外不宜",
        "closed": "商家临时停业",
        "user_dissent": "群里有人否决",
        "merchant_reject": "商家拒单",
    }
    cause = cause_map.get(probe_result.reason, probe_result.status)
    scope = {
        "small": "同片区同类换 1 站",
        "medium": "片区或活动类型变化",
        "large": "片区和活动类型都变化",
    }.get(magnitude, "")
    adjusted_start = replacement_start_time or failed.start_time
    return (
        f"原 {failed.start_time} {failed.poi_name} 改为 {adjusted_start} {new.name}，"
        f"因为{cause}（{scope}，其余地点维持）"
    )


def probe_plan(
    plan: Plan,
    prefs: Optional[UserPreferences] = None,
    auto_reroute: bool = True,
) -> tuple[Plan, list[RerouteEvent]]:
    """扫描整个 plan 的每一步，触发风险时自动 reroute。

    返回：(新 plan, reroute 事件列表)
    """
    prefs = prefs or UserPreferences()
    current_plan = plan
    events: list[RerouteEvent] = []

    # 一次最多 reroute 2 步——避免无限循环
    for attempt in range(2):
        rerouted_this_round = False
        for idx, step in enumerate(current_plan.steps):
            if step.kind == "depart" or not step.poi_id:
                continue
            # 用极简 POI 占位（probe 只需要 name + id）
            from tools.types import POI
            stub_poi = POI(
                id=step.poi_id, name=step.poi_name,
                category_lv1=None, category_lv2=None, category_lv3=None,
                typecode=None, district=None, business_area=None, address=None,
                longitude=None, latitude=None, rating=None, avg_price=None,
                open_time=None, phone=None, photos=[],
            )
            from providers import decision_weather_context

            result = probe(
                stub_poi,
                party_size=prefs.party_size,
                target_time=step.start_time,
                weather_context=decision_weather_context(plan.weather_context, step.start_time),
                weather_shelter=step.weather_shelter,
            )
            if result.fallback_action == "reroute" and auto_reroute:
                current_plan, ev = replan_step(current_plan, idx, result, prefs)
                events.append(ev)
                rerouted_this_round = True
                break  # 重新扫描，避免后续步骤索引错位
        if not rerouted_this_round:
            break
    return current_plan, events


# ============================================================
# helpers
# ============================================================

_FULL_MEAL_KEYWORDS = (
    "中餐厅", "西餐厅", "烤鸭", "烤肉", "火锅", "牛排", "海鲜",
    "本帮菜", "粤菜", "湘菜", "川菜", "鲁菜", "东北菜", "私房菜",
)

_SNACK_KEYWORDS = (
    "小吃", "面馆", "面庄", "饺子", "包子", "炸鸡", "甜品", "蛋糕",
    "饮品", "茶饮", "咖啡", "奶茶", "Cafe", "café",
)


def _is_full_meal(poi) -> bool:
    """判定一个 POI 是否是正餐（区别于 snack/rest）。"""
    blob = f"{poi.name or ''} {poi.category_lv2 or ''} {poi.category_lv3 or ''}"
    if any(kw in blob for kw in _SNACK_KEYWORDS):
        return False
    if any(kw in blob for kw in _FULL_MEAL_KEYWORDS):
        return True
    # 高客单价（>120）通常是正餐
    if poi.avg_price and poi.avg_price > 120:
        return True
    return False


def _weather_shelter(poi) -> str:
    from tools.weather_shelter import classify_poi

    return classify_poi(poi)


def _replacement_kind(original_kind: str, poi, reason: str) -> str:
    if reason != "weather":
        return original_kind
    if poi.category_lv1 == "购物服务":
        return "shopping"
    if poi.category_lv1 == "餐饮服务":
        return "rest"
    return "culture"


def _replacement_policy(original_kind: str, reason: str) -> ReplacementPolicy:
    if reason == "weather":
        return ReplacementPolicy(
            version="constraint_preserving_replan_v1",
            reason=reason,
            original_kind=original_kind,
            source_categories=("museum", "shopping"),
            allowed_shelters=("covered", "subway_direct", "full_indoor"),
        )
    return ReplacementPolicy(
        version="constraint_preserving_replan_v1",
        reason=reason,
        original_kind=original_kind,
        source_categories=(_kind_to_category(original_kind),),
        require_full_meal=original_kind == "meal",
        reject_full_meal=original_kind in {"snack", "rest"},
    )


def _kind_to_category(kind: str) -> str:
    return {
        "meal": "food",
        "snack": "food",
        "rest": "food",      # 咖啡 / 茶饮归 food 类
        "citywalk": "scenic",
        "culture": "scenic",
        "shopping": "shopping",
    }.get(kind, "all")


def _prefs_to_constraints(p: UserPreferences) -> SearchConstraints:
    return SearchConstraints(
        persona=p.persona,
        party_size=p.party_size,
        has_child=p.has_child,
        child_age=p.child_age,
        diet_flags=list(p.diet_flags),
        walk_radius_km=p.walk_radius_km,
        budget_per_person=p.budget_per_person,
        min_rating=4.0,
    )
