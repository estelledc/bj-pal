"""Travel time must be part of the executable itinerary timeline."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.schedule_reconciler import reconcile_plan_schedule  # noqa: E402
from agents.types import Plan, Step, UserPreferences  # noqa: E402


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _overlapping_demo_plan() -> Plan:
    steps = [
        Step(
            step_index=1,
            kind="citywalk",
            poi_id="poi-a",
            poi_name="A",
            start_time="14:00",
            duration_min=60,
        ),
        Step(
            step_index=2,
            kind="meal",
            poi_id="poi-b",
            poi_name="B",
            start_time="15:00",
            duration_min=75,
            travel_time_min=5,
            travel_options={"walking": {"duration_min": 5, "source": "estimated"}},
        ),
        Step(
            step_index=3,
            kind="culture",
            poi_id="poi-c",
            poi_name="C",
            start_time="16:15",
            duration_min=90,
            travel_time_min=3,
            travel_options={"walking": {"duration_min": 3, "source": "estimated"}},
        ),
        Step(
            step_index=4,
            kind="rest",
            poi_id="poi-d",
            poi_name="D",
            start_time="17:45",
            duration_min=45,
            travel_time_min=2,
            travel_options={"walking": {"duration_min": 2, "source": "estimated"}},
        ),
        Step(
            step_index=5,
            kind="depart",
            poi_name="返程",
            start_time="18:30",
            duration_min=0,
        ),
    ]
    return Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=steps,
        route_context={"version": "route_refresh_v1", "status": "complete"},
    )


def test_reconcile_includes_travel_and_fits_requested_window() -> None:
    plan = _overlapping_demo_plan()

    evidence = reconcile_plan_schedule(
        plan,
        UserPreferences(
            persona="family",
            target_start="14:00",
            duration_hours=4.0,
        ),
    )

    assert evidence.status == "complete"
    assert evidence.overrun_minutes == 0
    assert evidence.total_elapsed_minutes == 240
    assert evidence.travel_minutes == 10
    assert evidence.dwell_minutes_before == 270
    assert evidence.dwell_minutes_after == 230
    assert plan.steps[3].duration_min == 20
    assert plan.steps[2].duration_min == 75
    assert plan.steps[-1].start_time == "18:00"
    assert evidence.to_dict()["duration_adjustments"] == [
        {
            "step_position": 3,
            "kind": "rest",
            "before_min": 45,
            "after_min": 20,
            "reason": "fit_requested_window",
        },
        {
            "step_position": 2,
            "kind": "culture",
            "before_min": 90,
            "after_min": 75,
            "reason": "fit_requested_window",
        },
    ]

    for previous, current in zip(plan.steps, plan.steps[1:]):
        incoming = (
            current.travel_time_min
            if previous.poi_id and current.poi_id
            else 0
        )
        assert _minutes(current.start_time) >= (
            _minutes(previous.start_time) + previous.duration_min + incoming
        )


def test_unverified_adjacent_route_is_explicit_partial_not_fake_zero_cost() -> None:
    plan = _overlapping_demo_plan()
    plan.route_context = {"version": "route_refresh_v1", "status": "partial"}
    plan.steps[2].travel_time_min = 0
    plan.steps[2].travel_options = {}

    evidence = reconcile_plan_schedule(
        plan,
        UserPreferences(persona="family", target_start="14:00", duration_hours=5.0),
    )

    assert evidence.status == "partial"
    assert any("route_refresh_status=partial" in warning for warning in evidence.warnings)
    assert any(
        "unverified_travel:destination_step_position=2" in warning
        for warning in evidence.warnings
    )


def test_impossible_window_returns_overrun_after_safe_reductions() -> None:
    plan = Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        route_context={"version": "route_refresh_v1", "status": "complete"},
        steps=[
            Step(
                step_index=1,
                kind="meal",
                poi_id="meal-a",
                poi_name="A",
                start_time="14:00",
                duration_min=60,
            ),
            Step(
                step_index=2,
                kind="meal",
                poi_id="meal-b",
                poi_name="B",
                start_time="15:00",
                duration_min=60,
                travel_time_min=10,
                travel_options={"walking": {"duration_min": 10, "source": "estimated"}},
            ),
        ],
    )

    evidence = reconcile_plan_schedule(
        plan,
        UserPreferences(persona="family", target_start="14:00", duration_hours=1.0),
    )

    assert evidence.status == "overrun"
    assert evidence.overrun_minutes == 70
    assert evidence.total_elapsed_minutes == 130
    assert any("schedule_overrun:70min" in warning for warning in evidence.warnings)


def test_date_rollover_is_visible_when_step_schema_only_has_clock_time() -> None:
    plan = Plan(
        persona="friends",
        area_anchor="三里屯片区",
        route_context={"version": "route_refresh_v1", "status": "complete"},
        steps=[
            Step(
                step_index=1,
                kind="rest",
                poi_id="late-a",
                poi_name="A",
                start_time="23:30",
                duration_min=60,
            ),
            Step(
                step_index=2,
                kind="snack",
                poi_id="late-b",
                poi_name="B",
                start_time="00:40",
                duration_min=30,
                travel_time_min=10,
                travel_options={"walking": {"duration_min": 10, "source": "estimated"}},
            ),
        ],
    )

    evidence = reconcile_plan_schedule(
        plan,
        UserPreferences(persona="friends", target_start="23:30", duration_hours=2.0),
    )

    assert evidence.status == "partial"
    assert plan.steps[1].start_time == "00:40"
    assert evidence.planned_end == "01:10"
    assert evidence.planned_end_day_offset == 1
    assert "date_rollover_not_representable_in_step_start_time" in evidence.warnings
