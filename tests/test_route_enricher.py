"""Route refresh must replace adjacent-leg data as one coherent snapshot."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.types import Plan, Step, UserPreferences  # noqa: E402
from tools.route_enricher import refresh_plan_routes  # noqa: E402
from tools.route_lookup import RouteLeg  # noqa: E402


def _plan_with_stale_routes() -> Plan:
    return Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=[
            Step(
                step_index=index + 1,
                poi_id=poi_id,
                poi_name=poi_id,
                start_time=f"{14 + index}:00",
                travel_time_min=99,
                travel_distance_m=9_999,
                travel_options={"walking": {"duration_min": 99}},
            )
            for index, poi_id in enumerate(("poi-a", "poi-b", "poi-c"))
        ],
    )


def _fixture_lookup(
    origin_lng: float,
    origin_lat: float,
    dest_lng: float,
    dest_lat: float,
) -> dict[str, RouteLeg]:
    del origin_lat, dest_lat
    distance = int((dest_lng - origin_lng) * 1_000)
    return {
        mode: RouteLeg(
            mode=mode,  # type: ignore[arg-type]
            distance_m=distance,
            duration_min=distance // 10 + offset,
            source="estimated",
            summary="deterministic fixture",
        )
        for offset, mode in enumerate(("walking", "bicycling", "driving", "transit"), 1)
    }


def test_refresh_recomputes_incoming_and_outgoing_legs_after_replacement() -> None:
    plan = _plan_with_stale_routes()
    coords = {
        "poi-a": (0.0, 0.0),
        "poi-b": (1.0, 0.0),
        "poi-c": (3.0, 0.0),
    }

    evidence = refresh_plan_routes(
        plan,
        UserPreferences(persona="family"),
        changed_step_idx=1,
        coordinate_resolver=lambda _ids: coords,
        route_lookup=_fixture_lookup,
        mode_picker=lambda _legs, **_kwargs: ("walking", "fixture policy"),
    )

    assert plan.steps[0].travel_time_min == 0
    assert plan.steps[0].travel_options == {}
    assert plan.steps[1].travel_distance_m == 1_000
    assert plan.steps[2].travel_distance_m == 2_000
    assert plan.steps[1].travel_time_min == 101
    assert plan.steps[2].travel_time_min == 201
    assert plan.steps[1].travel_options["walking"]["source"] == "estimated"
    assert evidence.refreshed_leg_count == 2
    assert evidence.to_dict()["impacted_step_positions"] == [1, 2]
    assert evidence.to_dict()["warnings"] == []


def test_missing_coordinate_clears_stale_data_without_bridging_over_step() -> None:
    plan = _plan_with_stale_routes()
    calls: list[tuple[float, float]] = []

    def lookup(origin_lng, _origin_lat, dest_lng, _dest_lat):
        calls.append((origin_lng, dest_lng))
        return _fixture_lookup(origin_lng, 0.0, dest_lng, 0.0)

    evidence = refresh_plan_routes(
        plan,
        UserPreferences(persona="family"),
        changed_step_idx=1,
        coordinate_resolver=lambda _ids: {
            "poi-a": (0.0, 0.0),
            "poi-c": (3.0, 0.0),
        },
        route_lookup=lookup,
    )

    assert calls == []
    assert all(step.travel_time_min == 0 for step in plan.steps)
    assert all(step.travel_distance_m == 0 for step in plan.steps)
    assert all(step.travel_options == {} for step in plan.steps)
    assert evidence.refreshed_leg_count == 0
    assert evidence.status == "partial"
    assert evidence.to_dict()["missing_coordinate_step_positions"] == [1]
    assert any("missing_coordinate" in warning for warning in evidence.warnings)


def test_lookup_failure_does_not_restore_any_stale_route_fields() -> None:
    plan = _plan_with_stale_routes()

    def failing_lookup(*_args):
        raise RuntimeError("fixture route backend unavailable")

    evidence = refresh_plan_routes(
        plan,
        UserPreferences(persona="family"),
        coordinate_resolver=lambda _ids: {
            "poi-a": (0.0, 0.0),
            "poi-b": (1.0, 0.0),
            "poi-c": (3.0, 0.0),
        },
        route_lookup=failing_lookup,
    )

    assert all(step.travel_time_min == 0 for step in plan.steps)
    assert all(step.travel_distance_m == 0 for step in plan.steps)
    assert all(step.travel_options == {} for step in plan.steps)
    assert evidence.refreshed_leg_count == 0
    assert len(evidence.warnings) == 2
    assert all("route_lookup_failed" in warning for warning in evidence.warnings)


def test_non_poi_step_breaks_adjacency_instead_of_creating_shortcut() -> None:
    plan = _plan_with_stale_routes()
    plan.steps[1].poi_id = None
    plan.steps[1].kind = "depart"
    calls = []

    def lookup(*args):
        calls.append(args)
        return _fixture_lookup(*args)

    evidence = refresh_plan_routes(
        plan,
        UserPreferences(persona="family"),
        coordinate_resolver=lambda _ids: {
            "poi-a": (0.0, 0.0),
            "poi-c": (3.0, 0.0),
        },
        route_lookup=lookup,
    )

    assert calls == []
    assert evidence.refreshed_leg_count == 0
    assert all(step.travel_options == {} for step in plan.steps)
