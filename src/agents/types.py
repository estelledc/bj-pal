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
    travel_time_min: int = 0          # 从上一站到这里的缓存或估算路由时长
    travel_distance_m: int = 0        # 上一站到这里的距离
    travel_options: dict = field(default_factory=dict)  # {mode: {duration_min, distance_m}}（v2 改 6B）
    rationale: str = ""
    is_rerouted: bool = False
    reroute_reason: str = ""           # queue / weather / closed / user_dissent（v2 改 4）
    risk_tags: list[str] = field(default_factory=list)
    # v2 改 3 mock 真实感：从 mock_book 回填
    booking: Optional[dict] = None     # {booking_id, seat_no, menu_preview, photos_url, ...}
    # v4.2：兼容旧字段名，但语义是“证据支持度”，不是已校准成功概率。
    confidence: Optional[float] = None
    confidence_source: str = ""
    confidence_factors: dict = field(default_factory=dict)
    # Weather risk is evaluated against this explicit shelter class instead of
    # reconstructing a category from an LLM-produced step name.
    weather_shelter: str = "unknown"


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
    # v4.3：数据面显式说明每类证据来自哪里、是否新鲜、是否可预订。
    data_provenance: list[dict[str, Any]] = field(default_factory=list)
    data_warnings: list[dict[str, Any]] = field(default_factory=list)
    # Exact provider snapshot shared by ranking and downstream probe/reroute.
    weather_context: Optional[dict[str, Any]] = None
    # v5.2：最近一次全方案路由刷新证据；用于识别 partial/degraded 状态。
    route_context: dict[str, Any] = field(default_factory=dict)
    # v5.3：路线感知的时间轴、停留压缩和超时证据。
    schedule_context: dict[str, Any] = field(default_factory=dict)
    # v6.9：模型输出在进入 Plan 前的 strict schema/candidate-bound 证据。
    # None 仅用于读取 v6.8 及更早 artifact，或纯确定性/测试构造的 Plan。
    model_output_context: Optional[dict[str, Any]] = None

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
            data_provenance=list(d.get("data_provenance") or []),
            data_warnings=list(d.get("data_warnings") or []),
            weather_context=(dict(d["weather_context"]) if d.get("weather_context") else None),
            route_context=dict(d.get("route_context") or {}),
            schedule_context=dict(d.get("schedule_context") or {}),
            model_output_context=(
                dict(d["model_output_context"])
                if d.get("model_output_context")
                else None
            ),
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
    replacement_policy: dict[str, Any] = field(default_factory=dict)
    route_refresh: dict[str, Any] = field(default_factory=dict)
    schedule_refresh: dict[str, Any] = field(default_factory=dict)
