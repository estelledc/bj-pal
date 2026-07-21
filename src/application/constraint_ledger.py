"""Deterministic, typed preservation of execution constraints from user text.

The ledger is deliberately narrow: it extracts only constraints that the current
planner can enforce. It does not claim open-domain intent understanding. Caller
fields remain authoritative evidence; a contradictory text value is surfaced as
a conflict instead of being silently overwritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import time
from typing import TYPE_CHECKING, Any, Literal

from agents.types import Persona, UserPreferences


if TYPE_CHECKING:
    from .contracts import PlanRequest


CONSTRAINT_LEDGER_VERSION = "constraint_ledger_v1"

ConstraintSource = Literal[
    "explicit_structured",
    "user_text",
    "user_clarification",
    "default",
]
ConstraintOutcome = Literal[
    "applied",
    "matched",
    "kept_explicit",
    "merged",
    "resolved",
    "default",
]


@dataclass(frozen=True)
class ConstraintEntry:
    field: str
    value: Any
    source: ConstraintSource
    evidence: str
    hardness: Literal["hard", "soft"]
    outcome: ConstraintOutcome
    text_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        value = list(self.value) if isinstance(self.value, tuple) else self.value
        text_value = (
            list(self.text_value)
            if isinstance(self.text_value, tuple)
            else self.text_value
        )
        return {
            "field": self.field,
            "value": value,
            "source": self.source,
            "evidence": self.evidence,
            "hardness": self.hardness,
            "outcome": self.outcome,
            "text_value": text_value,
        }


@dataclass(frozen=True)
class ConstraintConflict:
    field: str
    structured_value: Any
    text_value: Any
    evidence: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "structured_value": self.structured_value,
            "text_value": self.text_value,
            "evidence": self.evidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ConstraintLedger:
    version: str = CONSTRAINT_LEDGER_VERSION
    raw_input: str = ""
    rewritten_query: str = ""
    entries: tuple[ConstraintEntry, ...] = ()
    conflicts: tuple[ConstraintConflict, ...] = ()
    warnings: tuple[str, ...] = ()
    applied_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.version != CONSTRAINT_LEDGER_VERSION:
            raise ValueError("unsupported constraint ledger version")

    @property
    def requires_clarification(self) -> bool:
        return bool(self.conflicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "raw_input": self.raw_input,
            "rewritten_query": self.rewritten_query,
            "entries": [item.to_dict() for item in self.entries],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "warnings": list(self.warnings),
            "applied_fields": list(self.applied_fields),
        }


@dataclass(frozen=True)
class ConstraintNormalizationResult:
    request: PlanRequest
    ledger: ConstraintLedger

    @property
    def requires_clarification(self) -> bool:
        return self.ledger.requires_clarification


@dataclass(frozen=True)
class _TextConstraint:
    value: Any
    evidence: str


_FIELDS: tuple[tuple[str, str, Literal["hard", "soft"]], ...] = (
    ("persona", "persona", "soft"),
    ("preferences.party_size", "party_size", "hard"),
    ("preferences.has_child", "has_child", "hard"),
    ("preferences.child_age", "child_age", "hard"),
    ("preferences.diet_flags", "diet_flags", "hard"),
    ("preferences.walk_radius_km", "walk_radius_km", "hard"),
    ("preferences.budget_per_person", "budget_per_person", "hard"),
    ("preferences.target_start", "target_start", "hard"),
    ("preferences.duration_hours", "duration_hours", "hard"),
)

_CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_NUMBER = r"[0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十]+"


class ConstraintNormalizer:
    """Map supported text constraints into ``UserPreferences`` with provenance."""

    def normalize(self, request: PlanRequest) -> ConstraintNormalizationResult:
        extracted, warnings = _extract_constraints(request.user_input)
        prefs = request.preferences
        values: dict[str, Any] = {
            "persona": request.persona,
            "party_size": prefs.party_size,
            "has_child": prefs.has_child,
            "child_age": prefs.child_age,
            "diet_flags": list(prefs.diet_flags),
            "walk_radius_km": prefs.walk_radius_km,
            "budget_per_person": prefs.budget_per_person,
            "target_start": prefs.target_start,
            "duration_hours": prefs.duration_hours,
        }
        entries: list[ConstraintEntry] = []
        conflicts: list[ConstraintConflict] = []
        applied_fields: list[str] = []

        for field_name, attr_name, hardness in _FIELDS:
            current = values[attr_name]
            parsed = extracted.get(field_name)
            explicit = _is_explicit(request, field_name)
            resolution = request.resolution_for(
                code="constraint_conflict",
                field=field_name,
            )

            if resolution is not None:
                resolved_value = _validate_resolution_value(
                    field_name,
                    resolution.value,
                )
                values[attr_name] = resolved_value
                applied_fields.append(field_name)
                entries.append(
                    ConstraintEntry(
                        field=field_name,
                        value=_ledger_value(resolved_value),
                        source="user_clarification",
                        evidence=resolution.answer,
                        hardness=hardness,
                        outcome="resolved",
                        text_value=(
                            _ledger_value(parsed.value) if parsed is not None else None
                        ),
                    )
                )
                continue

            if parsed is None:
                entries.append(
                    ConstraintEntry(
                        field=field_name,
                        value=_ledger_value(current),
                        source="explicit_structured" if explicit else "default",
                        evidence="caller field" if explicit else "schema default",
                        hardness=hardness,
                        outcome="kept_explicit" if explicit else "default",
                        text_value=None,
                    )
                )
                continue

            if field_name == "preferences.diet_flags":
                parsed_flags = list(parsed.value)
                merged = _unique([*current, *parsed_flags])
                values[attr_name] = merged
                if explicit:
                    outcome: ConstraintOutcome = (
                        "matched" if current == parsed_flags else "merged"
                    )
                    source: ConstraintSource = "explicit_structured"
                else:
                    outcome = "applied"
                    source = "user_text"
                if not explicit or outcome == "merged":
                    applied_fields.append(field_name)
                entries.append(
                    ConstraintEntry(
                        field=field_name,
                        value=tuple(merged),
                        source=source,
                        evidence=parsed.evidence,
                        hardness=hardness,
                        outcome=outcome,
                        text_value=tuple(parsed_flags),
                    )
                )
                continue

            if explicit and not _same_value(current, parsed.value):
                conflicts.append(
                    ConstraintConflict(
                        field=field_name,
                        structured_value=current,
                        text_value=parsed.value,
                        evidence=parsed.evidence,
                        reason="自然语言与调用方显式字段不一致，不能静默选择其一。",
                    )
                )
                entries.append(
                    ConstraintEntry(
                        field=field_name,
                        value=_ledger_value(current),
                        source="explicit_structured",
                        evidence=parsed.evidence,
                        hardness=hardness,
                        outcome="kept_explicit",
                        text_value=_ledger_value(parsed.value),
                    )
                )
                continue

            if explicit:
                entries.append(
                    ConstraintEntry(
                        field=field_name,
                        value=_ledger_value(current),
                        source="explicit_structured",
                        evidence=parsed.evidence,
                        hardness=hardness,
                        outcome="matched",
                        text_value=_ledger_value(parsed.value),
                    )
                )
                continue

            values[attr_name] = parsed.value
            applied_fields.append(field_name)
            entries.append(
                ConstraintEntry(
                    field=field_name,
                    value=_ledger_value(parsed.value),
                    source="user_text",
                    evidence=parsed.evidence,
                    hardness=hardness,
                    outcome="applied",
                    text_value=_ledger_value(parsed.value),
                )
            )

        normalized_persona: Persona = values["persona"]
        if extracted:
            normalized_preferences = replace(
                prefs,
                persona=normalized_persona,
                party_size=values["party_size"],
                has_child=values["has_child"],
                child_age=values["child_age"],
                diet_flags=list(values["diet_flags"]),
                walk_radius_km=values["walk_radius_km"],
                budget_per_person=values["budget_per_person"],
                target_start=values["target_start"],
                duration_hours=values["duration_hours"],
                raw_input=request.user_input,
            )
            normalized_request = request.with_preferences(
                normalized_preferences,
                persona=normalized_persona,
            )
        else:
            # Preserve the established application contract (including object
            # identity) when no text constraint was recognized.
            normalized_request = request
        ledger = ConstraintLedger(
            raw_input=request.user_input,
            rewritten_query=_rewrite_query(request.user_input, normalized_request, entries),
            entries=tuple(entries),
            conflicts=tuple(conflicts),
            warnings=tuple(warnings),
            applied_fields=tuple(applied_fields),
        )
        return ConstraintNormalizationResult(request=normalized_request, ledger=ledger)


def _extract_constraints(
    text: str,
) -> tuple[dict[str, _TextConstraint], list[str]]:
    result: dict[str, _TextConstraint] = {}
    warnings: list[str] = []

    persona = _extract_persona(text)
    if persona:
        result["persona"] = persona

    party = _first_numeric_match(
        text,
        (
            re.compile(rf"(?<!第)(?<!前)(?<!后)({_NUMBER})\s*(?:个)?人"),
            re.compile(rf"一家\s*({_NUMBER})\s*口"),
            re.compile(
                rf"^\s*({_NUMBER})\s*个(?:朋友|同学|同事).{{0,12}}(?:出去|出行|玩|逛|去)"
            ),
        ),
        minimum=1,
        maximum=20,
        integer=True,
    )
    if party:
        result["preferences.party_size"] = party

    child = re.search(rf"({_NUMBER})\s*岁(?:的)?(?:娃|孩子|儿童|小朋友)", text)
    if child:
        age = _parse_number(child.group(1))
        if age is not None and 0 <= age <= 17:
            result["preferences.has_child"] = _TextConstraint(True, child.group(0))
            result["preferences.child_age"] = _TextConstraint(int(age), child.group(0))
    elif re.search(r"(?:带娃|带孩子|有孩子|亲子|小朋友同行)", text):
        evidence = re.search(r"(?:带娃|带孩子|有孩子|亲子|小朋友同行)", text)
        assert evidence is not None
        result["preferences.has_child"] = _TextConstraint(True, evidence.group(0))

    diet = _extract_diet_flags(text)
    if diet:
        flags, evidence = diet
        result["preferences.diet_flags"] = _TextConstraint(flags, evidence)

    walk = _extract_walk_radius(text)
    if walk:
        result["preferences.walk_radius_km"] = walk

    budget_match = re.search(
        rf"(?:人均|每人)(?:预算|消费|花费|控制在|不超过|最多|大概|约)?\s*({_NUMBER})\s*(?:元|块)?",
        text,
    )
    if budget_match:
        value = _parse_number(budget_match.group(1))
        if value is not None and 0 <= value <= 100_000:
            result["preferences.budget_per_person"] = _TextConstraint(
                _clean_number(value), budget_match.group(0)
            )

    start = _extract_start_time(text)
    if start:
        result["preferences.target_start"] = start

    duration_match = re.search(rf"({_NUMBER})\s*(?:个)?(半)?\s*小时", text)
    if duration_match:
        value = _parse_number(duration_match.group(1))
        if value is not None:
            value += 0.5 if duration_match.group(2) else 0
            if 0 < value <= 24:
                result["preferences.duration_hours"] = _TextConstraint(
                    _clean_number(value), duration_match.group(0)
                )

    return result, warnings


def _extract_persona(text: str) -> _TextConstraint | None:
    patterns: tuple[tuple[Persona, re.Pattern[str]], ...] = (
        ("with_parents", re.compile(r"(?:陪|带|和)(?:爸妈|父母|老人|长辈)")),
        (
            "family",
            re.compile(r"(?:一家\S{0,4}|家庭出行|亲子|带\s*(?:\d+|[一二两三四五六七八九十]+)?\s*岁?(?:娃|孩子)|带孩子)"),
        ),
        ("friends", re.compile(r"(?:和|跟|同)(?:朋友|同学|同事|闺蜜)")),
        ("solo", re.compile(r"(?:独自|独行|一个人)(?:出行|出发|去|逛|玩|走|$|[，,。])")),
    )
    for persona, pattern in patterns:
        match = pattern.search(text)
        if match:
            return _TextConstraint(persona, match.group(0))
    return None


def _extract_diet_flags(text: str) -> tuple[list[str], str] | None:
    patterns = (
        ("no_spicy", re.compile(r"(?:不吃辣|不要辣|不能吃辣|忌辣|免辣)")),
        ("vegetarian", re.compile(r"(?:素食|吃素|全素)")),
        ("halal", re.compile(r"(?:清真|清真餐)")),
        ("no_lactose", re.compile(r"(?:乳糖不耐|不喝奶|不要乳制品)")),
        ("no_seafood", re.compile(r"(?:不吃海鲜|不要海鲜|海鲜过敏)")),
        (
            "light_diet",
            re.compile(r"(?<!不想只吃)(?<!不要)(?:口味清淡|饮食清淡|清淡一点)"),
        ),
    )
    found: list[tuple[int, str, str]] = []
    for flag, pattern in patterns:
        for match in pattern.finditer(text):
            found.append((match.start(), flag, match.group(0)))
    if not found:
        return None
    found.sort(key=lambda item: item[0])
    return _unique([item[1] for item in found]), "；".join(item[2] for item in found)


def _extract_walk_radius(text: str) -> _TextConstraint | None:
    patterns = (
        re.compile(
            rf"步行(?:距离)?(?:不超过|最多|控制在|少于|小于|约)?\s*({_NUMBER})\s*(公里|千米|km|KM|米)"
        ),
        re.compile(
            rf"({_NUMBER})\s*(公里|千米|km|KM|米)(?:以内|之内)(?:步行|走路)?"
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        value = _parse_number(match.group(1))
        if value is None:
            continue
        if match.group(2) == "米":
            value /= 1000
        if 0 < value <= 20:
            return _TextConstraint(float(value), match.group(0))
    return None


def _extract_start_time(text: str) -> _TextConstraint | None:
    clock = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", text)
    if clock:
        return _TextConstraint(
            f"{int(clock.group(1)):02d}:{int(clock.group(2)):02d}",
            clock.group(0),
        )

    period_match = re.search(
        rf"(凌晨|早上|上午|中午|下午|傍晚|晚上)\s*({_NUMBER})\s*点(半|[0-5]?\d分)?",
        text,
    )
    if not period_match:
        return None
    hour_value = _parse_number(period_match.group(2))
    if hour_value is None or int(hour_value) != hour_value:
        return None
    hour = int(hour_value)
    suffix = period_match.group(3) or ""
    minute = 30 if suffix == "半" else int(suffix[:-1] or 0) if suffix.endswith("分") else 0
    period = period_match.group(1)
    if period in {"下午", "傍晚", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period == "凌晨" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23:
        return None
    return _TextConstraint(f"{hour:02d}:{minute:02d}", period_match.group(0))


def _first_numeric_match(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
    *,
    minimum: float,
    maximum: float,
    integer: bool,
) -> _TextConstraint | None:
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        value = _parse_number(match.group(1))
        if value is None or not minimum <= value <= maximum:
            continue
        if integer and int(value) != value:
            continue
        return _TextConstraint(int(value) if integer else value, match.group(0))
    return None


def _parse_number(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        pass
    if raw == "十":
        return 10.0
    if "十" in raw:
        tens, ones = raw.split("十", 1)
        tens_value = _CHINESE_DIGITS.get(tens, 1 if tens == "" else -1)
        ones_value = _CHINESE_DIGITS.get(ones, 0 if ones == "" else -1)
        if tens_value < 0 or ones_value < 0:
            return None
        return float(tens_value * 10 + ones_value)
    if len(raw) == 1 and raw in _CHINESE_DIGITS:
        return float(_CHINESE_DIGITS[raw])
    return None


def _is_explicit(request: PlanRequest, field_name: str) -> bool:
    if field_name == "persona":
        return "persona" in request.provided_fields
    return field_name in request.provided_fields


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 1e-9
    return left == right


def _validate_resolution_value(field_name: str, value: Any) -> Any:
    label = f"clarification resolution for {field_name}"
    if field_name == "persona":
        if value not in {"family", "friends", "solo", "with_parents"}:
            raise ValueError(f"{label} is invalid")
        return value
    if field_name == "preferences.party_size":
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20:
            raise ValueError(f"{label} is invalid")
        return value
    if field_name == "preferences.has_child":
        if not isinstance(value, bool):
            raise ValueError(f"{label} is invalid")
        return value
    if field_name == "preferences.child_age":
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 17
        ):
            raise ValueError(f"{label} is invalid")
        return value
    if field_name == "preferences.diet_flags":
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{label} is invalid")
        flags = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 64:
                raise ValueError(f"{label} is invalid")
            if item.strip() not in flags:
                flags.append(item.strip())
        return flags
    if field_name == "preferences.walk_radius_km":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 < float(value) <= 20
        ):
            raise ValueError(f"{label} is invalid")
        return float(value)
    if field_name == "preferences.budget_per_person":
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= float(value) <= 100_000
        ):
            raise ValueError(f"{label} is invalid")
        return _clean_number(float(value))
    if field_name == "preferences.target_start":
        if not isinstance(value, str) or len(value) != 5:
            raise ValueError(f"{label} is invalid")
        try:
            time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{label} is invalid") from exc
        return value
    if field_name == "preferences.duration_hours":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 < float(value) <= 24
        ):
            raise ValueError(f"{label} is invalid")
        return _clean_number(float(value))
    raise ValueError(f"unsupported {label}")


def _ledger_value(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _clean_number(value: float) -> int | float:
    return int(value) if int(value) == value else value


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _rewrite_query(
    raw_input: str,
    request: PlanRequest,
    entries: list[ConstraintEntry],
) -> str:
    active = {item.field: item for item in entries if item.outcome != "default"}
    parts = [raw_input, f"片区={request.area_anchor}"]
    labels = (
        ("persona", "人群"),
        ("preferences.party_size", "人数"),
        ("preferences.has_child", "儿童同行"),
        ("preferences.child_age", "儿童年龄"),
        ("preferences.walk_radius_km", "步行半径"),
        ("preferences.budget_per_person", "人均预算"),
        ("preferences.target_start", "开始"),
        ("preferences.duration_hours", "时长"),
    )
    for field_name, label in labels:
        entry = active.get(field_name)
        if entry is None:
            continue
        value = entry.value
        if field_name == "preferences.has_child":
            value = "是" if value else "否"
        elif field_name == "preferences.walk_radius_km":
            value = f"{value:g}公里"
        elif field_name == "preferences.budget_per_person":
            value = f"{value:g}元"
        elif field_name == "preferences.duration_hours":
            value = f"{value:g}小时"
        parts.append(f"{label}={value}")
    diet = active.get("preferences.diet_flags")
    if diet is not None:
        parts.append(f"忌口={','.join(diet.value)}")
    return " | ".join(parts)
