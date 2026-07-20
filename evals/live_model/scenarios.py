"""Fixed synthetic scenarios for bounded live-model acceptance checks.

The registry keeps live calls comparable and prevents arbitrary user prompts
from entering checked-in observations. Observation artifacts retain only the
scenario ID, never the request text below.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.types import UserPreferences
from application import PlanRequest


@dataclass(frozen=True)
class LiveModelScenario:
    scenario_id: str
    user_input: str
    persona: str
    area_anchor: str
    preferences: UserPreferences
    provided_fields: frozenset[str]
    minimum_activity_steps: int = 2
    required_kinds: tuple[str, ...] = ()
    required_diet_evidence_tags: tuple[str, ...] = ()
    required_any_positive_tags: tuple[str, ...] = ()
    required_indoor_kind: str | None = None

    def request(self) -> PlanRequest:
        return PlanRequest(
            user_input=self.user_input,
            persona=self.persona,
            preferences=self.preferences,
            area_anchor=self.area_anchor,
            provided_fields=self.provided_fields,
        )

    def quality_policy(self) -> dict[str, object]:
        return {
            "persona": self.persona,
            "area_anchor": self.area_anchor,
            "target_start": self.preferences.target_start,
            "duration_minutes": int(self.preferences.duration_hours * 60),
            "budget_per_person": self.preferences.budget_per_person,
            "walk_radius_m": int(self.preferences.walk_radius_km * 1000),
            "walking_tolerance_multiplier": 1.5,
            "minimum_activity_steps": self.minimum_activity_steps,
            "required_kinds": list(self.required_kinds),
            "required_diet_evidence_tags": list(
                self.required_diet_evidence_tags
            ),
            "required_any_positive_tags": list(
                self.required_any_positive_tags
            ),
            "required_indoor_kind": self.required_indoor_kind,
        }


_COMMON_FIELDS = frozenset(
    {
        "user_input",
        "persona",
        "area_anchor",
        "preferences.party_size",
        "preferences.walk_radius_km",
        "preferences.budget_per_person",
        "preferences.target_start",
        "preferences.duration_hours",
    }
)


def _scenario(
    *,
    scenario_id: str,
    user_input: str,
    persona: str,
    area_anchor: str,
    party_size: int,
    walk_radius_km: float,
    budget_per_person: float,
    target_start: str,
    duration_hours: float,
    has_child: bool = False,
    child_age: int | None = None,
    diet_flags: tuple[str, ...] = (),
    minimum_activity_steps: int = 2,
    required_kinds: tuple[str, ...] = (),
    required_diet_evidence_tags: tuple[str, ...] = (),
    required_any_positive_tags: tuple[str, ...] = (),
    required_indoor_kind: str | None = None,
) -> LiveModelScenario:
    provided = set(_COMMON_FIELDS)
    if has_child:
        provided.add("preferences.has_child")
    if child_age is not None:
        provided.add("preferences.child_age")
    if diet_flags:
        provided.add("preferences.diet_flags")
    return LiveModelScenario(
        scenario_id=scenario_id,
        user_input=user_input,
        persona=persona,
        area_anchor=area_anchor,
        preferences=UserPreferences(
            persona=persona,
            party_size=party_size,
            has_child=has_child,
            child_age=child_age,
            diet_flags=list(diet_flags),
            walk_radius_km=walk_radius_km,
            budget_per_person=budget_per_person,
            target_start=target_start,
            duration_hours=duration_hours,
            raw_input=user_input,
        ),
        provided_fields=frozenset(provided),
        minimum_activity_steps=minimum_activity_steps,
        required_kinds=required_kinds,
        required_diet_evidence_tags=required_diet_evidence_tags,
        required_any_positive_tags=required_any_positive_tags,
        required_indoor_kind=required_indoor_kind,
    )


SCENARIOS = {
    item.scenario_id: item
    for item in (
        _scenario(
            scenario_id="synthetic-friends-sanlitun-3h-budget",
            user_input="周六下午两个人在三里屯玩三小时，预算人均200元，少走路",
            persona="friends",
            area_anchor="三里屯片区",
            party_size=2,
            walk_radius_km=0.8,
            budget_per_person=200,
            target_start="14:00",
            duration_hours=3.0,
        ),
        _scenario(
            scenario_id="synthetic-family-wudaoying-4h-child-diet",
            user_input="周六下午两位大人带一个5岁孩子在五道营玩四小时，人均预算150元，不吃辣，少走路",
            persona="family",
            area_anchor="五道营-雍和宫片区",
            party_size=3,
            has_child=True,
            child_age=5,
            diet_flags=("no_spicy",),
            walk_radius_km=0.8,
            budget_per_person=150,
            target_start="14:00",
            duration_hours=4.0,
            required_kinds=("meal",),
            required_diet_evidence_tags=("no_spicy",),
            required_any_positive_tags=(
                "child_friendly",
                "family_meal",
            ),
        ),
        _scenario(
            scenario_id="synthetic-solo-798-3h-indoor",
            user_input="周日下午一个人在798艺术区逛三小时，人均预算300元，优先室内展览，少排队",
            persona="solo",
            area_anchor="798艺术区片区",
            party_size=1,
            walk_radius_km=1.2,
            budget_per_person=300,
            target_start="14:00",
            duration_hours=3.0,
            required_kinds=("culture",),
            required_indoor_kind="culture",
        ),
    )
}


DEFAULT_SCENARIO_ID = "synthetic-friends-sanlitun-3h-budget"


def get_scenario(scenario_id: str) -> LiveModelScenario:
    try:
        return SCENARIOS[scenario_id]
    except KeyError as exc:
        raise ValueError(f"unknown fixed live-model scenario: {scenario_id}") from exc
