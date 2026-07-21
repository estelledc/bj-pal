"""Deterministic request normalization and clarification before tool execution.

The gate intentionally handles only execution-critical ambiguity with high-precision
rules. Optional preferences remain reversible planning assumptions; unresolved
references or contradictory locations stop before the planner fans out to tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal


if TYPE_CHECKING:
    from .constraint_ledger import ConstraintLedger
    from .contracts import PlanRequest


RequirementStatus = Literal[
    "proceed",
    "proceed_with_assumptions",
    "clarification_required",
]

REQUIREMENT_GATE_VERSION = "requirement_gate_v1"


@dataclass(frozen=True)
class RequirementSignal:
    code: str
    field: str
    evidence: str
    resolved_value: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "field": self.field,
            "evidence": self.evidence,
            "resolved_value": self.resolved_value,
        }


@dataclass(frozen=True)
class RequirementAssumption:
    code: str
    field: str
    value: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "field": self.field,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class UnresolvedRequirement:
    code: str
    field: str
    evidence: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "field": self.field,
            "evidence": self.evidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ClarificationQuestion:
    code: str
    field: str
    prompt: str
    options: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if not 2 <= len(self.options) <= 3:
            raise ValueError("clarification questions require two or three options")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "field": self.field,
            "prompt": self.prompt,
            "options": list(self.options),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RequirementDecision:
    version: str
    status: RequirementStatus
    normalized_input: str
    resolved_area_anchor: str
    signals: tuple[RequirementSignal, ...] = ()
    assumptions: tuple[RequirementAssumption, ...] = ()
    unresolved: tuple[UnresolvedRequirement, ...] = ()
    questions: tuple[ClarificationQuestion, ...] = ()

    def __post_init__(self) -> None:
        if self.version != REQUIREMENT_GATE_VERSION:
            raise ValueError("unsupported requirement gate version")
        if self.status == "clarification_required":
            if not self.unresolved or not self.questions:
                raise ValueError("clarification decisions need unresolved evidence and a question")
        elif self.unresolved or self.questions:
            raise ValueError("proceed decisions must not contain unresolved questions")
        if self.status == "proceed_with_assumptions" and not self.assumptions:
            raise ValueError("assumption status requires at least one explicit assumption")

    @property
    def requires_clarification(self) -> bool:
        return self.status == "clarification_required"

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "status": self.status,
            "normalized_input": self.normalized_input,
            "resolved_area_anchor": self.resolved_area_anchor,
            "signals": [item.to_dict() for item in self.signals],
            "assumptions": [item.to_dict() for item in self.assumptions],
            "unresolved": [item.to_dict() for item in self.unresolved],
            "questions": [item.to_dict() for item in self.questions],
        }


class PlanningClarificationRequired(RuntimeError):
    """Expected control-flow result: execution-critical input is unresolved."""

    def __init__(
        self,
        decision: RequirementDecision,
        *,
        constraints: ConstraintLedger | None = None,
    ) -> None:
        super().__init__("planning requirements need clarification before execution")
        self.decision = decision
        self.constraints = constraints


# Canonical anchors match the planner/tool layer. Aliases are deliberately small and
# deterministic: this is a public reproducible gate, not a claim of city-wide NER.
AREA_ALIASES: dict[str, tuple[str, ...]] = {
    "五道营-雍和宫片区": ("五道营", "雍和宫", "国子监", "地坛"),
    "三里屯片区": ("三里屯", "工体", "蓝色港湾"),
    "什刹海-鼓楼片区": ("什刹海", "鼓楼", "南锣鼓巷", "南锣", "钟楼"),
    "王府井-东单片区": ("王府井", "东单", "东方新天地"),
    "国贸-CBD片区": ("国贸", "CBD", "中央商务区"),
    "798艺术区片区": ("798", "酒仙桥"),
    "天安门-故宫片区": ("天安门", "故宫", "紫禁城"),
    "颐和园-清华片区": ("颐和园", "清华", "圆明园"),
    "望京片区": ("望京",),
    "亮马桥片区": ("亮马桥",),
    "西单片区": ("西单",),
    "牛街片区": ("牛街",),
}

_LOCATION_REFERENCE_PATTERNS = (
    re.compile(r"(?:上次|之前|刚才)(?:去过|提过|说的|选的)?(?:的)?(?:那个地方|那家店|那里|那儿|老地方)"),
    re.compile(r"还是(?:上次|之前)?(?:那个地方|那家店|那里|那儿|老地方)"),
)
_PLAN_REFERENCE_PATTERNS = (
    re.compile(r"(?:按|照|沿用|继续)(?:上次|之前|刚才|前面)(?:的)?(?:方案|路线|安排|行程)"),
    re.compile(r"(?:上次|之前|刚才|前面)(?:的)?(?:方案|路线|安排|行程)"),
)
_SELECTION_REFERENCE_PATTERNS = (
    re.compile(r"(?:第[一二三四五1-5]个|前一个|后一个)(?:方案|选项|地点|地方|店|行程)(?:吧|呢|就行)?"),
)
_RELATIVE_LOCATION_PATTERNS = (
    re.compile(r"别离(?:家|公司|学校)太远"),
    re.compile(r"离(?:家|公司|学校)(?:近|不远)"),
    re.compile(r"(?:家|公司|学校|当前位置)(?:的)?附近"),
    re.compile(r"(?:按|以)(?:我)?(?:当前位置|定位)"),
    re.compile(
        r"[\u4e00-\u9fffA-Za-z0-9]{2,16}"
        r"(?:大学|学院|商场|公园|地铁站|车站|胡同|路|街|桥|门|馆|店)附近"
    ),
)
_AREA_RESOLUTION_CODES = (
    "multiple_area_candidates",
    "conflicting_area_anchor",
    "unresolved_location_reference",
    "missing_location_reference",
)


class RequirementNormalizer:
    """Resolve safe defaults and stop only execution-critical ambiguity."""

    def normalize(self, request: PlanRequest) -> RequirementDecision:
        text = _normalize_whitespace(request.user_input)
        area_is_explicit = "area_anchor" in request.provided_fields
        detected_areas = _detect_areas(text)
        requested_area = request.area_anchor
        requested_canonical = _canonicalize_area(requested_area)
        signals: list[RequirementSignal] = []
        area_resolution = next(
            (
                resolution
                for code in _AREA_RESOLUTION_CODES
                if (
                    resolution := request.resolution_for(
                        code=code,
                        field="area_anchor",
                    )
                )
                is not None
            ),
            None,
        )
        if area_resolution is not None:
            if not isinstance(area_resolution.value, str):
                raise ValueError("clarification resolution for area_anchor is invalid")
            resolved_from_clarification = _canonicalize_area(area_resolution.value)
            if not resolved_from_clarification or len(resolved_from_clarification) > 100:
                raise ValueError("clarification resolution for area_anchor is invalid")
            signals.append(
                RequirementSignal(
                    code="area_from_clarification",
                    field="area_anchor",
                    evidence=area_resolution.answer,
                    resolved_value=resolved_from_clarification,
                )
            )
        else:
            resolved_from_clarification = None

        if resolved_from_clarification is None and len(detected_areas) > 1 and not (
            area_is_explicit and requested_canonical in detected_areas
        ):
            evidence = "、".join(detected_areas)
            return _clarify_area(
                text=text,
                requested_area=requested_area,
                code="multiple_area_candidates",
                evidence=evidence,
                reason="短时方案只能有一个主活动片区，但输入同时出现多个候选。",
                candidates=detected_areas,
            )

        detected_area = detected_areas[0] if len(detected_areas) == 1 else None
        if (
            resolved_from_clarification is None
            and detected_area
            and area_is_explicit
            and requested_canonical != detected_area
        ):
            return _clarify_area(
                text=text,
                requested_area=requested_area,
                code="conflicting_area_anchor",
                evidence=f"文本={detected_area}; 字段={requested_area}",
                reason="自然语言片区与结构化 area_anchor 不一致，不能静默选择其一。",
                candidates=(detected_area, requested_area),
            )

        if resolved_from_clarification is not None:
            resolved_area = resolved_from_clarification
        elif detected_area:
            resolved_area = detected_area
            signals.append(
                RequirementSignal(
                    code="area_from_text",
                    field="area_anchor",
                    evidence=_first_area_evidence(text, detected_area),
                    resolved_value=detected_area,
                )
            )
        else:
            resolved_area = request.area_anchor

        plan_reference = _first_match(text, _PLAN_REFERENCE_PATTERNS)
        selection_reference = _first_match(text, _SELECTION_REFERENCE_PATTERNS)
        if plan_reference or selection_reference:
            evidence = plan_reference or selection_reference
            code = "unresolved_plan_reference" if plan_reference else "unresolved_selection_reference"
            if request.resolution_for(code=code, field="user_input") is None:
                return _clarify_reference(
                    text=text,
                    area=resolved_area,
                    code=code,
                    field="user_input",
                    evidence=evidence,
                    prompt="请补充你想沿用或选择的具体方案信息。",
                    options=("粘贴原方案或选项", "只保留片区并重新生成", "重新描述本次需求"),
                    reason="当前请求没有可解析的历史方案或候选列表上下文。",
                    signals=tuple(signals),
                )

        location_reference = _first_match(text, _LOCATION_REFERENCE_PATTERNS)
        relative_location = _first_match(text, _RELATIVE_LOCATION_PATTERNS)
        location_is_resolved = (
            area_is_explicit
            or detected_area is not None
            or resolved_from_clarification is not None
        )
        if (location_reference or relative_location) and not location_is_resolved:
            evidence = location_reference or relative_location
            code = (
                "unresolved_location_reference"
                if location_reference
                else "missing_location_reference"
            )
            return _clarify_area(
                text=text,
                requested_area=requested_area,
                code=code,
                evidence=evidence,
                reason="相对位置没有可用的家庭、公司、定位或历史地点上下文。",
                candidates=(),
            )

        if (
            not area_is_explicit
            and detected_area is None
            and resolved_from_clarification is None
        ):
            assumption = RequirementAssumption(
                code="default_area_anchor",
                field="area_anchor",
                value=request.area_anchor,
                reason="请求未提供可解析片区，使用公开演示默认片区；该选择可在重试时覆盖。",
            )
            return RequirementDecision(
                version=REQUIREMENT_GATE_VERSION,
                status="proceed_with_assumptions",
                normalized_input=text,
                resolved_area_anchor=resolved_area,
                signals=tuple(signals),
                assumptions=(assumption,),
            )

        return RequirementDecision(
            version=REQUIREMENT_GATE_VERSION,
            status="proceed",
            normalized_input=text,
            resolved_area_anchor=resolved_area,
            signals=tuple(signals),
        )


def _clarify_area(
    *,
    text: str,
    requested_area: str,
    code: str,
    evidence: str,
    reason: str,
    candidates: tuple[str, ...],
) -> RequirementDecision:
    options = _unique_options(
        [*(f"使用{candidate}" for candidate in candidates), f"使用{requested_area}", "补充其他片区或具体地点"]
    )
    if len(options) < 2:
        options.append("补充家、公司或当前位置")
    return RequirementDecision(
        version=REQUIREMENT_GATE_VERSION,
        status="clarification_required",
        normalized_input=text,
        resolved_area_anchor=requested_area,
        unresolved=(
            UnresolvedRequirement(
                code=code,
                field="area_anchor",
                evidence=evidence,
                reason=reason,
            ),
        ),
        questions=(
            ClarificationQuestion(
                code=f"clarify_{code}",
                field="area_anchor",
                prompt="你希望以哪个片区或具体地点作为本次活动中心？",
                options=tuple(options[:3]),
                reason=reason,
            ),
        ),
    )


def _clarify_reference(
    *,
    text: str,
    area: str,
    code: str,
    field: str,
    evidence: str,
    prompt: str,
    options: tuple[str, ...],
    reason: str,
    signals: tuple[RequirementSignal, ...],
) -> RequirementDecision:
    return RequirementDecision(
        version=REQUIREMENT_GATE_VERSION,
        status="clarification_required",
        normalized_input=text,
        resolved_area_anchor=area,
        signals=signals,
        unresolved=(
            UnresolvedRequirement(
                code=code,
                field=field,
                evidence=evidence,
                reason=reason,
            ),
        ),
        questions=(
            ClarificationQuestion(
                code=f"clarify_{code}",
                field=field,
                prompt=prompt,
                options=options,
                reason=reason,
            ),
        ),
    )


def _detect_areas(text: str) -> tuple[str, ...]:
    found = []
    upper_text = text.upper()
    for canonical, aliases in AREA_ALIASES.items():
        if any(alias.upper() in upper_text for alias in aliases):
            found.append(canonical)
    return tuple(found)


def _canonicalize_area(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).upper()
    for canonical, aliases in AREA_ALIASES.items():
        if normalized == re.sub(r"\s+", "", canonical).upper():
            return canonical
        if any(alias.upper() in normalized for alias in aliases):
            return canonical
    return value.strip()


def _first_area_evidence(text: str, canonical: str) -> str:
    upper_text = text.upper()
    return next(
        alias
        for alias in AREA_ALIASES[canonical]
        if alias.upper() in upper_text
    )


def _first_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return ""


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def _unique_options(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
