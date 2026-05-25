"""偏好镜子（Preference Mirror）：自然语言反问澄清，把模糊偏好实时变成约束向量。

设计：
- 走 LLM（system 含 "Preference Mirror"），输出 JSON
    若需追问 → {needs_clarification: true, clarify_question, options[]}
    若已明确 → {needs_clarification: false, extracted_constraint: {diet_flags / ...}}
- mock client 走规则；真 LongCat 走 LLM
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .llm_client import LLMClient, get_llm_client  # noqa: E402
from .types import UserPreferences  # noqa: E402

PREF_MIRROR_SYSTEM = """你是 Preference Mirror，BJ-Pal 行程规划的偏好澄清助手。

任务：用户给一句模糊的偏好（如"老婆减脂"、"娃喜欢动物"），你判断：
- 是否已足够明确可直接转为搜索约束
- 是否需要追问 1 个二选一/三选一的关键问题

输出 JSON：
```
{
  "needs_clarification": true|false,
  "clarify_question": "...",          // 当需要时
  "options": ["选项A", "选项B"],       // 当需要时
  "default_assumption": "...",         // 当不澄清时默认怎么处理
  "extracted_constraint": {            // 当不需要追问时
    "diet_flags": ["light_diet"],
    "child_age": 5,
    "..."
  }
}
```

只输出 JSON。
"""


@dataclass
class ClarifyResult:
    needs_clarification: bool
    clarify_question: str = ""
    options: list[str] = field(default_factory=list)
    default_assumption: str = ""
    extracted_constraint: dict = field(default_factory=dict)
    mode: str = "planning"   # P0.2: 'planning' 全自动 plan / 'screening' 仅候选筛选


# ============================================================
# P0.2 重要场合 → 筛选模式（信号 5：5/5 一致）
# ============================================================

# 关键词触发筛选模式：用户在重要场合只想要候选 + 理由，不想交给 AI 全自动规划
SCREENING_KEYWORDS = (
    "生日", "纪念日", "周年", "结婚", "求婚",
    "老人首次", "老人第一次", "双方父母", "见家长",
    "家宴", "聚餐", "宴请",
    # 人数 ≥ 6 视为重要场合
    "6 人", "6人", "六人", "7 人", "7人", "七人",
    "8 人", "8人", "八人", "9 人", "9人", "10 人", "10人",
)

# 人数提取
import re as _re

_PARTY_SIZE_PATTERNS = [
    _re.compile(r"(\d+)\s*人(?!均)"),    # "6 人" 但不是 "人均"
    _re.compile(r"我们\s*(\d+)\s*个"),
]


def detect_party_size(raw: str) -> Optional[int]:
    """从原话里抽人数。返回 None 表示没明示。"""
    for pat in _PARTY_SIZE_PATTERNS:
        m = pat.search(raw)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


# 老人参与触发关键词（P1.3 信号 - 李慧珍场景）
ELDERLY_KEYWORDS = (
    "老人", "外婆", "外公", "爷爷", "奶奶", "姥姥", "姥爷",
    "父母", "爸妈", "妈妈", "爸爸",
    "退休", "年纪大", "老年",
)


def detect_has_elderly(raw_preference: str) -> bool:
    """检测是否有老人参与。

    Returns:
        True: 切 elderly_friendly 卡片样式
    """
    return any(kw in (raw_preference or "") for kw in ELDERLY_KEYWORDS)


# ============================================================
# v2.4 S4 / D5: 时段识别（工作日 vs 周末）
#
# 来源：USER_RESEARCH_FINDINGS 信号 4 — "工作日不属于这个 App"（4/5）
# BJ-Pal 主台词聚焦周六下午；工作日 query 应触发偏好镜子澄清，
# 不直接出 plan。
# ============================================================

WEEKDAY_KEYWORDS = [
    "周一", "周二", "周三", "周四", "周五",
    "礼拜一", "礼拜二", "礼拜三", "礼拜四", "礼拜五",
    "工作日", "上班", "下班", "午休", "中午请假", "调休",
    "工作中", "上着班",
]
WEEKEND_KEYWORDS = [
    "周六", "周日", "周末", "礼拜六", "礼拜天", "礼拜日",
    "休息日", "双休", "假期",
]


def detect_weekday_context(raw: str) -> dict:
    """识别用户 query 的时段信号 — 工作日 vs 周末。

    BJ-Pal 主战场是周六下午；检测到工作日 + 没有周末覆盖时，
    UI 应该提示偏好镜子澄清（"工作日下午是临时约还是有半天假？"）
    而不是直接出 plan。

    Args:
        raw: 用户原话

    Returns:
        {
            "is_weekday_signal": bool,        # 工作日关键词命中
            "is_weekend_signal": bool,        # 周末关键词命中
            "day_keyword": str,               # 命中的具体词
            "should_clarify": bool,           # UI 是否应该澄清
            "suggested_clarification": str,   # 建议追问语
        }
    """
    text = raw or ""
    weekday_hit = next((kw for kw in WEEKDAY_KEYWORDS if kw in text), "")
    weekend_hit = next((kw for kw in WEEKEND_KEYWORDS if kw in text), "")

    is_weekday = bool(weekday_hit)
    is_weekend = bool(weekend_hit)

    # 应澄清：工作日命中 + 没有周末覆盖（避免"周六中午请假"误触发）
    should_clarify = is_weekday and not is_weekend

    if should_clarify:
        suggested = (
            f"BJ-Pal 主台词是周六下午。检测到「{weekday_hit}」— "
            "是临时约还是请了半天假？要不要切到「快速选餐」模式？"
        )
    else:
        suggested = ""

    return {
        "is_weekday_signal": is_weekday,
        "is_weekend_signal": is_weekend,
        "day_keyword": weekday_hit or weekend_hit,
        "should_clarify": should_clarify,
        "suggested_clarification": suggested,
    }


def detect_screening_mode(raw_preference: str) -> bool:
    """关键词触发：重要场合 → 切筛选模式。

    Returns:
        True: 需要切筛选模式（候选 + 理由，不出 plan）
        False: 默认全自动 plan
    """
    lower = raw_preference or ""
    if any(kw in lower for kw in SCREENING_KEYWORDS):
        return True
    # 人数 ≥ 6
    party = detect_party_size(lower)
    if party and party >= 6:
        return True
    return False


def clarify_preference(
    raw_preference: str,
    current_prefs: Optional[UserPreferences] = None,
    client: Optional[LLMClient] = None,
) -> ClarifyResult:
    """一次反问：给一段用户原话，输出"是否需要追问 + 追问内容/已提取的约束"。"""
    client = client or get_llm_client()
    ctx = {
        "raw_preference": raw_preference,
        "current_persona": (current_prefs or UserPreferences()).persona,
    }
    user_msg = f"<context>{json.dumps(ctx, ensure_ascii=False)}</context>"
    resp = client.complete(
        system=PREF_MIRROR_SYSTEM,
        user=user_msg,
        json_schema={"clarify": True},
        temperature=0.2,
    )
    parsed = resp.parsed or _safe_parse_json(resp.text) or {}
    mode = "screening" if detect_screening_mode(raw_preference) else "planning"
    return ClarifyResult(
        needs_clarification=bool(parsed.get("needs_clarification", False)),
        clarify_question=parsed.get("clarify_question", ""),
        options=list(parsed.get("options") or []),
        default_assumption=parsed.get("default_assumption", ""),
        extracted_constraint=dict(parsed.get("extracted_constraint") or {}),
        mode=mode,
    )


def apply_clarification(
    prefs: UserPreferences,
    user_choice: str,
    raw_preference: str,
) -> UserPreferences:
    """用户选了选项后，把对应约束写回 prefs。"""
    choice = user_choice.lower()
    raw = raw_preference.lower()

    if "减脂" in raw or "低脂" in raw:
        if "低糖" in choice:
            if "low_sugar" not in prefs.diet_flags:
                prefs.diet_flags.append("low_sugar")
        elif "低油" in choice:
            if "low_oil" not in prefs.diet_flags:
                prefs.diet_flags.append("low_oil")
        elif "都要" in choice or "严格" in choice:
            for f in ["low_sugar", "low_oil", "light_diet"]:
                if f not in prefs.diet_flags:
                    prefs.diet_flags.append(f)
        else:
            if "light_diet" not in prefs.diet_flags:
                prefs.diet_flags.append("light_diet")

    if "辣" in raw and "no_spicy" not in prefs.diet_flags:
        prefs.diet_flags.append("no_spicy")

    # 孩子年龄
    if "孩子" in raw or "娃" in raw:
        for age in [3, 4, 5, 6, 7, 8, 9, 10]:
            if str(age) in choice:
                prefs.has_child = True
                prefs.child_age = age
                break

    return prefs


def _safe_parse_json(text: str) -> Optional[dict]:
    """LLM JSON 输出鲁棒解析（含截断恢复）。"""
    from .llm_robust import repair_json
    return repair_json(text)
