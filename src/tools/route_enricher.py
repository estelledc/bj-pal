"""Fail-closed route enrichment for a complete plan snapshot.

Route lookup may return tracked cache data or a deterministic estimate. This
module never describes either as a live observation. Its core invariant is
that a refresh clears every old leg before computing replacements, so a
rerouted POI cannot inherit an adjacent leg calculated for the old POI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from agents.types import Plan, UserPreferences
from loader import get_conn

from .route_lookup import RouteLeg, lookup_routes, pick_best_mode


CoordinateResolver = Callable[[list[str]], dict[str, tuple[float, float]]]
RouteLookup = Callable[[float, float, float, float], dict[str, RouteLeg]]
ModePicker = Callable[..., tuple[str, str]]


@dataclass(frozen=True)
class RouteRefreshEvidence:
    """JSON-safe evidence describing one full-plan route refresh."""

    version: str
    scope: str
    status: str
    changed_step_idx: Optional[int]
    total_poi_step_count: int
    resolved_poi_step_count: int
    refreshed_leg_count: int
    impacted_step_positions: tuple[int, ...]
    missing_coordinate_step_positions: tuple[int, ...]
    refreshed_legs: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "scope": self.scope,
            "status": self.status,
            "changed_step_idx": self.changed_step_idx,
            "total_poi_step_count": self.total_poi_step_count,
            "resolved_poi_step_count": self.resolved_poi_step_count,
            "refreshed_leg_count": self.refreshed_leg_count,
            "impacted_step_positions": list(self.impacted_step_positions),
            "missing_coordinate_step_positions": list(
                self.missing_coordinate_step_positions
            ),
            "refreshed_legs": [dict(leg) for leg in self.refreshed_legs],
            "warnings": list(self.warnings),
        }


def resolve_poi_coordinates(
    poi_ids: list[str],
) -> dict[str, tuple[float, float]]:
    """Resolve tracked POI coordinates without leaking a connection."""
    if not poi_ids:
        return {}
    unique_ids = list(dict.fromkeys(poi_ids))
    placeholders = ",".join(["?"] * len(unique_ids))
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT id, longitude, latitude FROM pois WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall()
    finally:
        conn.close()
    return {
        row["id"]: (row["longitude"], row["latitude"])
        for row in rows
        if row["longitude"] is not None and row["latitude"] is not None
    }


def refresh_plan_routes(
    plan: Plan,
    prefs: UserPreferences,
    *,
    changed_step_idx: Optional[int] = None,
    coordinate_resolver: CoordinateResolver = resolve_poi_coordinates,
    route_lookup: RouteLookup = lookup_routes,
    mode_picker: ModePicker = pick_best_mode,
) -> RouteRefreshEvidence:
    """Replace all route fields and return auditable refresh evidence.

    The plan is tiny, so a reroute recomputes the full plan instead of trying
    to patch two fields in place. Only adjacent list positions are connected:
    a missing POI or coordinate blocks that leg and cannot be skipped over.
    """
    for step in plan.steps:
        step.travel_time_min = 0
        step.travel_distance_m = 0
        step.travel_options = {}

    poi_positions = [
        position for position, step in enumerate(plan.steps) if step.poi_id
    ]
    poi_ids = [plan.steps[position].poi_id for position in poi_positions]
    warnings: list[str] = []
    refreshed_legs: list[dict[str, Any]] = []

    try:
        coords = coordinate_resolver(
            [poi_id for poi_id in poi_ids if poi_id is not None]
        )
    except Exception as exc:  # route evidence must fail closed, not hide stale data
        coords = {}
        warnings.append(f"coordinate_resolution_failed:{type(exc).__name__}")

    missing_positions = tuple(
        position
        for position in poi_positions
        if plan.steps[position].poi_id not in coords
    )
    warnings.extend(
        f"missing_coordinate:step_position={position}"
        for position in missing_positions
    )

    for destination_position in range(1, len(plan.steps)):
        origin_position = destination_position - 1
        origin = plan.steps[origin_position]
        destination = plan.steps[destination_position]
        if not origin.poi_id or not destination.poi_id:
            continue
        origin_coords = coords.get(origin.poi_id)
        destination_coords = coords.get(destination.poi_id)
        if origin_coords is None or destination_coords is None:
            continue

        try:
            legs = route_lookup(*origin_coords, *destination_coords)
            best_mode, reason = mode_picker(
                legs,
                has_child=prefs.has_child,
                walk_radius_km=prefs.walk_radius_km,
            )
            chosen = legs.get(best_mode)
            if chosen is None:
                warnings.append(
                    "route_mode_missing:"
                    f"destination_step_position={destination_position}:mode={best_mode}"
                )
                continue
            options = {mode: leg.to_dict() for mode, leg in legs.items()}
        except Exception as exc:  # preserve cleared fields and expose degradation
            warnings.append(
                "route_lookup_failed:"
                f"destination_step_position={destination_position}:"
                f"{type(exc).__name__}"
            )
            continue

        destination.mode_to_here = best_mode  # type: ignore[assignment]
        destination.travel_time_min = chosen.duration_min
        destination.travel_distance_m = chosen.distance_m
        destination.travel_options = options
        refreshed_legs.append(
            {
                "origin_step_position": origin_position,
                "destination_step_position": destination_position,
                "origin_poi_id": origin.poi_id,
                "destination_poi_id": destination.poi_id,
                "selected_mode": best_mode,
                "selection_reason": reason,
                "distance_m": chosen.distance_m,
                "duration_min": chosen.duration_min,
                "selected_source": chosen.source,
                "mode_sources": {
                    mode: leg.source for mode, leg in legs.items()
                },
            }
        )

    if changed_step_idx is None:
        impacted_positions = tuple(poi_positions)
    else:
        impacted_positions = tuple(
            position
            for position in (changed_step_idx, changed_step_idx + 1)
            if 0 <= position < len(plan.steps)
        )

    resolved_count = sum(
        1
        for position in poi_positions
        if plan.steps[position].poi_id in coords
    )
    return RouteRefreshEvidence(
        version="route_refresh_v1",
        scope="full_plan",
        status="complete" if not warnings else "partial",
        changed_step_idx=changed_step_idx,
        total_poi_step_count=len(poi_positions),
        resolved_poi_step_count=resolved_count,
        refreshed_leg_count=len(refreshed_legs),
        impacted_step_positions=impacted_positions,
        missing_coordinate_step_positions=missing_positions,
        refreshed_legs=tuple(refreshed_legs),
        warnings=tuple(warnings),
    )
