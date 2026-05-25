"""Agent 层共用 schema：Plan / Step / UserPreferences / RerouteEvent。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional

StepKind = Literal["citywalk", "meal", "culture", "rest", "shopping", "depart", "snack"]
TransportMode = Literal["walking", "bicycling", "driving", "transit"]
Persona = Literal["family", "friends", "solo", "with_parents"]


@dataclass
class Step:
    step_index: int = 0
    poi_name: str = ""
    start_time: str = ""              # "14:00"
    kind: StepKind = "citywalk"
    poi_id: Optional[str] = None
    duration_min: int = 60            # 在该 POI 停留时长
    mode_to_here: TransportMode = "walking"
    travel_time_min: int = 0          # 从上一站到这里的真实路由时长（v2 改 1）
    travel_distance_m: int = 0        # 上一站到这里的距离
    travel_options: dict = field(default_factory=dict)  # {mode: {duration_min, distance_m}}（v2 改 6B）
    rationale: str = ""
    is_rerouted: bool = False
    reroute_reason: str = ""           # queue / weather / closed / user_dissent（v2 改 4）
    risk_tags: list[str] = field(default_factory=list)
    # v2 改 3 mock 真实感：从 mock_book 回填
    booking: Optional[dict] = None     # {booking_id, seat_no, menu_preview, photos_url, ...}


@dataclass
class Plan:
    persona: Persona
    area_anchor: str
    steps: list[Step]
    fallback_strategies: dict = field(default_factory=dict)
    summary: str = ""
    rerouted_at_step: Optional[int] = None
    # v2.4 D1：plan 唯一 id，给 plan_tracer 关联用；LLM 不需要填，自动生成
    plan_id: str = field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:12]}")

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        steps = [Step(**{k: v for k, v in s.items() if k in Step.__dataclass_fields__})
                 for s in d.get("steps", [])]
        return cls(
            persona=d.get("persona", "family"),
            area_anchor=d.get("area_anchor", ""),
            steps=steps,
            fallback_strategies=d.get("fallback_strategies", {}),
            summary=d.get("summary", ""),
            rerouted_at_step=d.get("rerouted_at_step"),
            plan_id=d.get("plan_id") or f"plan-{uuid.uuid4().hex[:12]}",
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UserPreferences:
    persona: Persona = "family"
    party_size: int = 3
    has_child: bool = False
    child_age: Optional[int] = None
    diet_flags: list[str] = field(default_factory=list)  # ["light_diet", "no_spicy", ...]
    walk_radius_km: float = 1.5
    budget_per_person: Optional[float] = None
    target_start: str = "14:00"
    duration_hours: float = 4.5
    raw_input: str = ""


@dataclass
class RerouteEvent:
    failed_step_idx: int
    failed_poi_name: str
    reason: str                # "queue_85min" / "closed" / "weather"
    evidence: list[str] = field(default_factory=list)  # UGC 引用
    replacement_poi_name: Optional[str] = None
    # P0.4：改动幅度分流（信号 9）
    change_magnitude: Literal["small", "medium", "large", "none"] = "small"
    change_summary_zh: str = ""           # 一句话给人看
    unchanged_steps: list[int] = field(default_factory=list)
    notify_strategy: Literal["group_direct", "private_first", "warn_only"] = "group_direct"
