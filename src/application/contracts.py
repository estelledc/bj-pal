"""Stable planning contracts at the BJ-Pal application boundary."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from agents.types import Plan, Persona, RerouteEvent, UserPreferences
from data_profile import DataProfile

from .constraint_ledger import ConstraintLedger
from .execution_observation import ExecutionObservation
from .resolution import ClarificationResolution
from .requirement_gate import RequirementDecision


TokenCallback = Callable[[str], None]
PlanCallback = Callable[[Plan], None]
CancellationProbe = Callable[[], bool]
DeadlineProbe = Callable[[], bool]

PREFERENCE_PROVIDED_FIELDS = frozenset(
    {
        "preferences.party_size",
        "preferences.has_child",
        "preferences.child_age",
        "preferences.diet_flags",
        "preferences.walk_radius_km",
        "preferences.budget_per_person",
        "preferences.target_start",
        "preferences.duration_hours",
    }
)


class PlanningCancelled(RuntimeError):
    """The durable caller requested cancellation at a safe workflow boundary."""


class PlanningDeadlineExceeded(RuntimeError):
    """The durable caller's absolute deadline passed at a safe workflow boundary."""


@dataclass(frozen=True)
class PlanRequest:
    """Everything required to execute one end-to-end planning use case."""

    user_input: str
    persona: Persona = "family"
    preferences: UserPreferences = field(default_factory=UserPreferences)
    area_anchor: str = "五道营-雍和宫片区"
    user_id: Optional[str] = None
    auto_reroute: bool = True
    provided_fields: frozenset[str] = field(
        default_factory=lambda: frozenset({"user_input"})
    )
    resolutions: tuple[ClarificationResolution, ...] = ()

    def __post_init__(self) -> None:
        normalized_input = self.user_input.strip()
        normalized_area = self.area_anchor.strip()
        if not normalized_input:
            raise ValueError("user_input must not be empty")
        if not normalized_area:
            raise ValueError("area_anchor must not be empty")
        if self.preferences.persona != self.persona:
            raise ValueError(
                "request persona must match preferences.persona "
                f"({self.persona!r} != {self.preferences.persona!r})"
            )
        allowed_fields = {
            "user_input",
            "persona",
            "preferences",
            "area_anchor",
            "user_id",
            "auto_reroute",
        }
        if any(
            field_name not in allowed_fields
            and not field_name.startswith("preferences.")
            for field_name in self.provided_fields
        ):
            raise ValueError("provided_fields contains an unknown request field")
        object.__setattr__(self, "user_input", normalized_input)
        object.__setattr__(self, "area_anchor", normalized_area)
        object.__setattr__(self, "provided_fields", frozenset(self.provided_fields))
        normalized_resolutions = tuple(self.resolutions)
        if len(normalized_resolutions) > 20:
            raise ValueError("a planning request may contain at most 20 resolutions")
        if any(not isinstance(item, ClarificationResolution) for item in normalized_resolutions):
            raise ValueError("resolutions must contain ClarificationResolution values")
        object.__setattr__(self, "resolutions", normalized_resolutions)

    def with_area_anchor(self, area_anchor: str) -> "PlanRequest":
        """Return a normalized copy while preserving caller field provenance."""
        return replace(self, area_anchor=area_anchor)

    def with_user_input(self, user_input: str) -> "PlanRequest":
        """Return a clarified request while preserving the original audit session."""
        preferences = replace(self.preferences, raw_input=user_input.strip())
        return replace(self, user_input=user_input, preferences=preferences)

    def with_resolution(self, resolution: ClarificationResolution) -> "PlanRequest":
        """Append an immutable resolution; latest matching item is authoritative."""
        return replace(self, resolutions=(*self.resolutions, resolution))

    def resolution_for(
        self,
        *,
        code: str,
        field: str,
    ) -> ClarificationResolution | None:
        for resolution in reversed(self.resolutions):
            if resolution.code == code and resolution.field == field:
                return resolution
        return None

    def with_preferences(
        self,
        preferences: UserPreferences,
        *,
        persona: Persona | None = None,
    ) -> "PlanRequest":
        """Return normalized preferences without inventing caller provenance."""
        return replace(
            self,
            persona=persona or preferences.persona,
            preferences=preferences,
        )

    def to_dict(self) -> dict:
        return {
            "user_input": self.user_input,
            "persona": self.persona,
            "preferences": {
                "party_size": self.preferences.party_size,
                "has_child": self.preferences.has_child,
                "child_age": self.preferences.child_age,
                "diet_flags": list(self.preferences.diet_flags),
                "walk_radius_km": self.preferences.walk_radius_km,
                "budget_per_person": self.preferences.budget_per_person,
                "target_start": self.preferences.target_start,
                "duration_hours": self.preferences.duration_hours,
            },
            "area_anchor": self.area_anchor,
            "user_id": self.user_id,
            "auto_reroute": self.auto_reroute,
            "provided_fields": sorted(self.provided_fields),
            "resolutions": [item.to_dict() for item in self.resolutions],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanRequest":
        preferences = payload.get("preferences") or {}
        persona = payload.get("persona") or "family"
        provided_payload = payload.get("provided_fields")
        if provided_payload is None:
            # v5.3 and older durable payloads did not preserve provenance. Do not
            # invent explicit user intent from a serialized schema default.
            provided_fields = frozenset({"user_input"})
        else:
            if not isinstance(provided_payload, (list, tuple, set, frozenset)):
                raise ValueError("provided_fields must be an array of field names")
            provided_fields = frozenset(str(item) for item in provided_payload)
        resolution_payload = payload.get("resolutions") or []
        if not isinstance(resolution_payload, list):
            raise ValueError("resolutions must be an array")
        return cls(
            user_input=str(payload.get("user_input") or ""),
            persona=persona,
            preferences=UserPreferences(
                persona=persona,
                party_size=int(preferences.get("party_size", 3)),
                has_child=bool(preferences.get("has_child", False)),
                child_age=preferences.get("child_age"),
                diet_flags=list(preferences.get("diet_flags") or []),
                walk_radius_km=float(preferences.get("walk_radius_km", 1.5)),
                budget_per_person=preferences.get("budget_per_person"),
                target_start=str(preferences.get("target_start") or "14:00"),
                duration_hours=float(preferences.get("duration_hours", 4.5)),
                raw_input=str(payload.get("user_input") or ""),
            ),
            area_anchor=str(payload.get("area_anchor") or "五道营-雍和宫片区"),
            user_id=payload.get("user_id"),
            auto_reroute=bool(payload.get("auto_reroute", True)),
            provided_fields=provided_fields,
            resolutions=tuple(
                ClarificationResolution.from_dict(item)
                for item in resolution_payload
            ),
        )


@dataclass(frozen=True)
class PlanningCallbacks:
    """Optional delivery callbacks; the use case itself stays UI-agnostic."""

    on_token: Optional[TokenCallback] = None
    on_progress: Optional[TokenCallback] = None
    on_stream_event: Optional[TokenCallback] = None
    on_initial_plan: Optional[PlanCallback] = None
    should_cancel: Optional[CancellationProbe] = None
    should_timeout: Optional[DeadlineProbe] = None
    correlation_id: Optional[str] = None


@dataclass(frozen=True)
class PlanResult:
    """Canonical output consumed by every delivery adapter."""

    request: PlanRequest
    initial_plan: Plan
    final_plan: Plan
    reroute_events: tuple[RerouteEvent, ...]
    data_profile: DataProfile
    requirements: RequirementDecision
    constraints: ConstraintLedger = field(default_factory=ConstraintLedger)
    execution: ExecutionObservation = field(default_factory=ExecutionObservation.not_observed)

    @property
    def was_adjusted(self) -> bool:
        return bool(self.reroute_events)

    def to_dict(self) -> dict:
        return {
            "request": self.request.to_dict(),
            "initial_plan": self.initial_plan.to_dict(),
            "final_plan": self.final_plan.to_dict(),
            "reroute_events": [
                {
                    "failed_step_idx": event.failed_step_idx,
                    "failed_poi_name": event.failed_poi_name,
                    "reason": event.reason,
                    "evidence": list(event.evidence),
                    "replacement_poi_name": event.replacement_poi_name,
                    "change_magnitude": event.change_magnitude,
                    "change_summary_zh": event.change_summary_zh,
                    "unchanged_steps": list(event.unchanged_steps),
                    "notify_strategy": event.notify_strategy,
                    "replacement_policy": dict(event.replacement_policy),
                    "route_refresh": dict(event.route_refresh),
                    "schedule_refresh": dict(event.schedule_refresh),
                }
                for event in self.reroute_events
            ],
            "data_profile": {
                "name": self.data_profile.name,
                "classification": self.data_profile.classification,
                "public_reproducible": self.data_profile.public_reproducible,
                "limitations": list(self.data_profile.limitations),
            },
            "requirements": self.requirements.to_dict(),
            "constraints": self.constraints.to_dict(),
            "execution": self.execution.to_dict(),
        }
