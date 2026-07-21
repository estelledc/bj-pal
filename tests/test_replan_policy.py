from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.replanner import _is_full_meal, replan_step  # noqa: E402
from agents.types import Plan, Step, UserPreferences  # noqa: E402
from tools.availability_probe import ProbeResult  # noqa: E402


def _plan(*, kind: str, poi_id: str, poi_name: str) -> Plan:
    return Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=[
            Step(
                step_index=1,
                kind=kind,
                poi_id=poi_id,
                poi_name=poi_name,
                start_time="15:00",
                rationale="policy fixture",
                weather_shelter="open" if kind == "citywalk" else "full_indoor",
            )
        ],
    )


def _probe(*, poi_id: str, poi_name: str, reason: str) -> ProbeResult:
    return ProbeResult(
        poi_id=poi_id,
        poi_name=poi_name,
        status="weather_block" if reason == "weather" else "unavailable",
        wait_min=65,
        party_size=3,
        target_time="15:00",
        evidence=["policy fixture failure"],
        risk_tags=[reason],
        fallback_action="reroute",
        reason=reason,
    )


def test_meal_reroute_cannot_degrade_into_cafe_or_snack() -> None:
    plan = _plan(
        kind="meal",
        poi_id="P_FZC69",
        poi_name="方砖厂69号炸酱面(雍和宫店)",
    )

    updated, event = replan_step(
        plan,
        0,
        _probe(
            poi_id="P_FZC69",
            poi_name="方砖厂69号炸酱面(雍和宫店)",
            reason="queue",
        ),
        UserPreferences(persona="family"),
    )

    assert event.replacement_poi_name is not None
    assert updated.steps[0].kind == "meal"
    assert _is_full_meal(_lookup_replacement(event.replacement_poi_name)) is True
    assert event.replacement_policy["version"] == "constraint_preserving_replan_v1"
    assert event.replacement_policy["require_full_meal"] is True
    assert event.replacement_policy["semantic_eligible_count"] > 0


def test_weather_reroute_crosses_category_but_requires_shelter() -> None:
    plan = _plan(kind="citywalk", poi_id="P_WDY", poi_name="五道营胡同")

    updated, event = replan_step(
        plan,
        0,
        _probe(poi_id="P_WDY", poi_name="五道营胡同", reason="weather"),
        UserPreferences(persona="family"),
    )

    assert event.replacement_poi_name is not None
    assert updated.steps[0].kind in {"culture", "shopping"}
    assert updated.steps[0].weather_shelter in {
        "covered",
        "subway_direct",
        "full_indoor",
    }
    assert event.replacement_policy["source_categories"] == ("museum", "shopping")
    assert "open" not in event.replacement_policy["allowed_shelters"]
    assert event.change_magnitude in {"medium", "large"}


def test_reroute_refreshes_both_adjacent_legs_and_exposes_evidence() -> None:
    plan = Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=[
            Step(
                step_index=1,
                kind="culture",
                poi_id="P_GZJ",
                poi_name="国子监",
                start_time="14:00",
            ),
            Step(
                step_index=2,
                kind="meal",
                poi_id="P_FZC",
                poi_name="方砖厂69号炸酱面(雍和宫店)",
                start_time="15:00",
                travel_time_min=88,
                travel_distance_m=12_345,
                travel_options={"walking": {"duration_min": 88}},
            ),
            Step(
                step_index=3,
                kind="culture",
                poi_id="P_DTGY",
                poi_name="地坛公园",
                start_time="17:00",
                travel_time_min=77,
                travel_distance_m=54_321,
                travel_options={"walking": {"duration_min": 77}},
            ),
        ],
    )

    updated, event = replan_step(
        plan,
        1,
        _probe(
            poi_id="P_FZC",
            poi_name="方砖厂69号炸酱面(雍和宫店)",
            reason="queue",
        ),
        UserPreferences(persona="family"),
    )

    assert event.replacement_poi_name is not None
    assert event.route_refresh["version"] == "route_refresh_v1"
    assert event.route_refresh["status"] == "complete"
    assert event.route_refresh["impacted_step_positions"] == [1, 2]
    assert event.route_refresh["refreshed_leg_count"] == 2
    assert event.schedule_refresh["version"] == "schedule_reconcile_v1"
    assert event.schedule_refresh["status"] == "complete"
    assert event.schedule_refresh["overrun_minutes"] == 0
    assert updated.route_context == event.route_refresh
    assert updated.schedule_context == event.schedule_refresh
    assert updated.steps[1].travel_distance_m != 12_345
    assert updated.steps[2].travel_distance_m != 54_321
    assert updated.steps[1].travel_options
    assert updated.steps[2].travel_options
    for previous, current in zip(updated.steps, updated.steps[1:]):
        previous_hour, previous_minute = map(int, previous.start_time.split(":"))
        current_hour, current_minute = map(int, current.start_time.split(":"))
        incoming = current.travel_time_min if previous.poi_id and current.poi_id else 0
        assert current_hour * 60 + current_minute >= (
            previous_hour * 60
            + previous_minute
            + previous.duration_min
            + incoming
        )


def _lookup_replacement(name: str):
    from tools.amap_search import search_pois
    from tools.types import SearchConstraints

    candidates = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=SearchConstraints(),
        limit=30,
    )
    return next(candidate for candidate in candidates if candidate.name == name)
