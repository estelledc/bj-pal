"""AddOn Agent（v2 改 7）：扫 plan + prefs 主动提议加分项。

不调 LLM——按规则触发，避免"鸡汤感"建议；每条建议都基于 plan 上下文。

接口：
    suggest_addons(plan, prefs, weather_state) -> list[AddOnSuggestion]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .types import Plan, UserPreferences

AddOnKind = Literal[
    "guided_tour",   # 亲子讲解 / 文化讲解场次
    "snack_break",   # 加餐建议
    "cake_surprise", # 蛋糕惊喜（命题字面）
    "umbrella",      # 雨伞
    "water_bottle",  # 水壶
    "early_pickup",  # 提前打车
    "merch_addon",   # 文创周边
]


@dataclass
class AddOnSuggestion:
    kind: AddOnKind
    title: str
    description: str
    target_step: Optional[int]   # 关联到哪一步 step_index
    action_label: str = "加入计划"
    reasoning: str = ""           # 为什么触发这条
    cost_estimate: Optional[float] = None  # ¥


# ============================================================
# 主接口
# ============================================================

def suggest_addons(
    plan: Plan,
    prefs: UserPreferences,
    weather_rain: bool = True,    # 14:00-15:30 demo 硬编码
    weather_temp_c: float = 28.0, # demo 硬编码 28°C
) -> list[AddOnSuggestion]:
    """主入口：返回 1-3 条建议。"""
    out: list[AddOnSuggestion] = []

    meals = [s for s in plan.steps if s.kind == "meal"]
    cultures = [s for s in plan.steps if s.kind in ("culture", "citywalk")]
    has_outdoor = any(s.kind in ("citywalk", "culture") for s in plan.steps)

    # 1. 文化场所亲子讲解（家庭画像 + 有 culture step）
    if prefs.has_child and prefs.child_age and prefs.child_age >= 4 and cultures:
        target = cultures[0]
        out.append(AddOnSuggestion(
            kind="guided_tour",
            title=f"{target.poi_name} 14:30 亲子讲解一场",
            description=f"5 岁娃看建筑容易枯燥，加 30 分钟讲解能听懂朝代故事。无需额外票，提前 10 分钟到入口集合。",
            target_step=target.step_index,
            reasoning=f"family + child_age={prefs.child_age} + 有 culture step",
            cost_estimate=0,
            action_label="预约讲解（免费）",
        ))

    # 2. 雨伞（天气窗 + 有户外）
    if weather_rain and has_outdoor:
        out.append(AddOnSuggestion(
            kind="umbrella",
            title="14:00-15:30 有小阵雨预警",
            description="美团秒送 30 分钟可达，¥18 一把折叠伞送到你接下来要去的餐厅前台代收。",
            target_step=None,
            reasoning="weather_rain + has_outdoor",
            cost_estimate=18.0,
            action_label="下单送伞",
        ))

    # 3. 水壶（高温 + 带娃）
    if weather_temp_c >= 28 and prefs.has_child:
        out.append(AddOnSuggestion(
            kind="water_bottle",
            title=f"今天北京 {weather_temp_c:.0f}°C，给娃带个水壶？",
            description="500ml 儿童保温壶 ¥45，美团买菜 1 小时送到家，出门前正好接上。",
            target_step=None,
            reasoning=f"temp={weather_temp_c}°C + has_child",
            cost_estimate=45.0,
            action_label="顺手买一个",
        ))

    # 4. 加餐（连续 culture/citywalk ≥ 2 步且无 snack/rest 间隔）
    long_walk_streaks = _detect_long_culture_walk(plan)
    if long_walk_streaks and prefs.has_child:
        idx = long_walk_streaks[0]
        out.append(AddOnSuggestion(
            kind="snack_break",
            title="连续 2 段步行，建议中间加 15 分钟加餐",
            description="在第 X 步和第 X+1 步之间插入「附近便利店补货 / 路边小吃」15 分钟，避免娃低血糖闹脾气。",
            target_step=idx,
            reasoning="long culture/citywalk streak + has_child",
            action_label="插入加餐节点",
        ))

    # 5. 蛋糕惊喜（命题字面字眼，但只在某些 trigger 下出现）
    # 这里走"被动触发"——用户自己点 +蛋糕鲜花 时给出（不主动建议）

    # 6. 文创周边（有 culture step + 朋友画像 / 拍照偏好）
    if cultures and prefs.persona == "friends":
        target = cultures[-1]
        out.append(AddOnSuggestion(
            kind="merch_addon",
            title=f"{target.poi_name} 出口有文创小店",
            description="预算内的小礼物，朋友合影后顺手买，¥20-50 / 件，比景区门口贵不多。",
            target_step=target.step_index,
            reasoning="culture + persona=friends",
            cost_estimate=30.0,
            action_label="路过看看",
        ))

    # 7. 提前打车（最后 step 是 depart 且总时长 > 4h）
    departs = [s for s in plan.steps if s.kind == "depart"]
    if departs:
        # 假设总时长触发条件
        out.append(AddOnSuggestion(
            kind="early_pickup",
            title="返程前 15 分钟提前叫车？",
            description="周六傍晚雍和宫片区打车排队 5-15min，提前叫车能省 10min 等待。",
            target_step=departs[0].step_index,
            reasoning="depart step exists",
            cost_estimate=None,
            action_label="预约 18:15 接驾",
        ))

    # 限制 1-3 条（避免太多打扰）
    return out[:3]


# ============================================================
# helpers
# ============================================================

def _detect_long_culture_walk(plan: Plan) -> list[int]:
    """找连续 ≥ 2 步 citywalk/culture 且无 rest/snack 中断的位置。"""
    streaks = []
    streak_start = None
    streak_len = 0
    for s in plan.steps:
        if s.kind in ("citywalk", "culture"):
            if streak_start is None:
                streak_start = s.step_index
            streak_len += 1
        else:
            if streak_len >= 2:
                streaks.append(streak_start)
            streak_start = None
            streak_len = 0
    if streak_len >= 2 and streak_start is not None:
        streaks.append(streak_start)
    return streaks


def addon_to_dict(a: AddOnSuggestion) -> dict:
    return {
        "kind": a.kind,
        "title": a.title,
        "description": a.description,
        "target_step": a.target_step,
        "action_label": a.action_label,
        "reasoning": a.reasoning,
        "cost_estimate": a.cost_estimate,
    }
