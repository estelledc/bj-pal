"""Deterministic timeline reconciliation after route enrichment.

`Step.start_time` means arrival at that step. The reconciler therefore adds
the destination step's incoming travel time after the previous dwell. It can
shrink flexible dwell times down to explicit floors, but never silently drops
a stop or pretends an impossible requested window is feasible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .types import Plan, UserPreferences


DEFAULT_MINIMUM_DWELL_MIN: dict[str, int] = {
    "depart": 0,
    "snack": 15,
    "rest": 20,
    "citywalk": 30,
    "shopping": 30,
    "culture": 45,
    "meal": 60,
}
DEFAULT_REDUCTION_PRIORITY = (
    "rest",
    "snack",
    "shopping",
    "culture",
    "citywalk",
    "meal",
)


@dataclass(frozen=True)
class ScheduleRefreshEvidence:
    version: str
    policy_version: str
    status: str
    target_start: str
    window_minutes: int
    window_end: str
    window_end_day_offset: int
    planned_end: str
    planned_end_day_offset: int
    total_elapsed_minutes: int
    travel_minutes: int
    dwell_minutes_before: int
    dwell_minutes_after: int
    overrun_minutes: int
    reflowed_step_positions: tuple[int, ...]
    duration_adjustments: tuple[dict[str, Any], ...]
    time_adjustments: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "policy_version": self.policy_version,
            "status": self.status,
            "target_start": self.target_start,
            "window_minutes": self.window_minutes,
            "window_end": self.window_end,
            "window_end_day_offset": self.window_end_day_offset,
            "planned_end": self.planned_end,
            "planned_end_day_offset": self.planned_end_day_offset,
            "total_elapsed_minutes": self.total_elapsed_minutes,
            "travel_minutes": self.travel_minutes,
            "dwell_minutes_before": self.dwell_minutes_before,
            "dwell_minutes_after": self.dwell_minutes_after,
            "overrun_minutes": self.overrun_minutes,
            "reflowed_step_positions": list(self.reflowed_step_positions),
            "duration_adjustments": [
                dict(adjustment) for adjustment in self.duration_adjustments
            ],
            "time_adjustments": [
                dict(adjustment) for adjustment in self.time_adjustments
            ],
            "warnings": list(self.warnings),
        }


def reconcile_plan_schedule(
    plan: Plan,
    prefs: UserPreferences,
    *,
    minimum_dwell_min: Optional[Mapping[str, int]] = None,
    reduction_priority: tuple[str, ...] = DEFAULT_REDUCTION_PRIORITY,
) -> ScheduleRefreshEvidence:
    """Mutate a plan into a travel-aware sequential timeline.

    The function is deliberately independent from route lookup. It consumes
    the current route snapshot, which makes route failure and scheduling
    failure separately observable and testable.
    """
    dwell_policy = dict(DEFAULT_MINIMUM_DWELL_MIN)
    if minimum_dwell_min is not None:
        dwell_policy.update(minimum_dwell_min)

    base_minute = _parse_clock(prefs.target_start)
    try:
        window_minutes = int(round(prefs.duration_hours * 60))
    except (TypeError, ValueError, OverflowError):
        window_minutes = 0
    invalid_warnings = _validate_inputs(
        plan,
        base_minute=base_minute,
        window_minutes=window_minutes,
        dwell_policy=dwell_policy,
    )
    if invalid_warnings:
        return _invalid_evidence(
            prefs,
            plan=plan,
            window_minutes=max(0, window_minutes),
            warnings=invalid_warnings,
        )
    assert base_minute is not None

    warnings = _route_warnings(plan)
    dwell_before = sum(step.duration_min for step in plan.steps)
    travel_minutes = sum(
        _incoming_travel_minutes(plan, position)
        for position in range(1, len(plan.steps))
    )
    required_reduction = max(
        0,
        dwell_before + travel_minutes - window_minutes,
    )
    remaining_reduction = required_reduction
    duration_adjustments: list[dict[str, Any]] = []

    for kind in reduction_priority:
        for position in range(len(plan.steps) - 1, -1, -1):
            if remaining_reduction <= 0:
                break
            step = plan.steps[position]
            if step.kind != kind:
                continue
            floor = min(step.duration_min, dwell_policy.get(kind, step.duration_min))
            reducible = step.duration_min - floor
            if reducible <= 0:
                continue
            reduction = min(remaining_reduction, reducible)
            before = step.duration_min
            step.duration_min -= reduction
            remaining_reduction -= reduction
            duration_adjustments.append(
                {
                    "step_position": position,
                    "kind": step.kind,
                    "before_min": before,
                    "after_min": step.duration_min,
                    "reason": "fit_requested_window",
                }
            )

    original_starts = [step.start_time for step in plan.steps]
    absolute_starts: list[int] = []
    if plan.steps:
        absolute_starts.append(base_minute)
        for position in range(1, len(plan.steps)):
            previous = plan.steps[position - 1]
            absolute_starts.append(
                absolute_starts[-1]
                + previous.duration_min
                + _incoming_travel_minutes(plan, position)
            )

    time_adjustments: list[dict[str, Any]] = []
    for position, (step, absolute_start) in enumerate(
        zip(plan.steps, absolute_starts)
    ):
        adjusted, day_offset = _format_clock(absolute_start)
        if step.start_time != adjusted or day_offset:
            time_adjustments.append(
                {
                    "step_position": position,
                    "before": original_starts[position],
                    "after": adjusted,
                    "day_offset": day_offset,
                    "reason": "travel_aware_reflow",
                }
            )
        step.start_time = adjusted

    dwell_after = sum(step.duration_min for step in plan.steps)
    if plan.steps:
        planned_end_minute = absolute_starts[-1] + plan.steps[-1].duration_min
    else:
        planned_end_minute = base_minute
    total_elapsed = planned_end_minute - base_minute
    overrun = max(0, total_elapsed - window_minutes)
    if overrun:
        warnings.append(f"schedule_overrun:{overrun}min")
    if any(absolute_start >= 24 * 60 for absolute_start in absolute_starts):
        warnings.append("date_rollover_not_representable_in_step_start_time")

    if overrun:
        status = "overrun"
    elif warnings:
        status = "partial"
    else:
        status = "complete"

    window_end, window_day_offset = _format_clock(base_minute + window_minutes)
    planned_end, planned_day_offset = _format_clock(planned_end_minute)
    return ScheduleRefreshEvidence(
        version="schedule_reconcile_v1",
        policy_version="minimum_dwell_v1",
        status=status,
        target_start=prefs.target_start,
        window_minutes=window_minutes,
        window_end=window_end,
        window_end_day_offset=window_day_offset,
        planned_end=planned_end,
        planned_end_day_offset=planned_day_offset,
        total_elapsed_minutes=total_elapsed,
        travel_minutes=travel_minutes,
        dwell_minutes_before=dwell_before,
        dwell_minutes_after=dwell_after,
        overrun_minutes=overrun,
        reflowed_step_positions=tuple(
            adjustment["step_position"] for adjustment in time_adjustments
        ),
        duration_adjustments=tuple(duration_adjustments),
        time_adjustments=tuple(time_adjustments),
        warnings=tuple(warnings),
    )


def _incoming_travel_minutes(plan: Plan, destination_position: int) -> int:
    origin = plan.steps[destination_position - 1]
    destination = plan.steps[destination_position]
    if not origin.poi_id or not destination.poi_id:
        return 0
    return destination.travel_time_min


def _route_warnings(plan: Plan) -> list[str]:
    adjacent_destinations = [
        position
        for position in range(1, len(plan.steps))
        if plan.steps[position - 1].poi_id and plan.steps[position].poi_id
    ]
    if not adjacent_destinations:
        return []

    warnings: list[str] = []
    route_version = plan.route_context.get("version")
    route_status = plan.route_context.get("status")
    if route_version != "route_refresh_v1":
        warnings.append("route_context_missing_or_unsupported")
    if route_status != "complete":
        warnings.append(f"route_refresh_status={route_status or 'missing'}")
    for position in adjacent_destinations:
        if not plan.steps[position].travel_options:
            warnings.append(
                f"unverified_travel:destination_step_position={position}"
            )
    return warnings


def _validate_inputs(
    plan: Plan,
    *,
    base_minute: Optional[int],
    window_minutes: int,
    dwell_policy: Mapping[str, int],
) -> list[str]:
    warnings: list[str] = []
    if base_minute is None:
        warnings.append("invalid_target_start")
    if window_minutes <= 0:
        warnings.append("invalid_duration_window")
    for position, step in enumerate(plan.steps):
        if step.duration_min < 0:
            warnings.append(f"negative_duration:step_position={position}")
        if step.travel_time_min < 0:
            warnings.append(f"negative_travel:step_position={position}")
    for kind, value in dwell_policy.items():
        if value < 0:
            warnings.append(f"negative_minimum_dwell:kind={kind}")
    return warnings


def _invalid_evidence(
    prefs: UserPreferences,
    *,
    plan: Plan,
    window_minutes: int,
    warnings: list[str],
) -> ScheduleRefreshEvidence:
    return ScheduleRefreshEvidence(
        version="schedule_reconcile_v1",
        policy_version="minimum_dwell_v1",
        status="invalid",
        target_start=prefs.target_start,
        window_minutes=window_minutes,
        window_end="",
        window_end_day_offset=0,
        planned_end="",
        planned_end_day_offset=0,
        total_elapsed_minutes=0,
        travel_minutes=0,
        dwell_minutes_before=sum(step.duration_min for step in plan.steps),
        dwell_minutes_after=sum(step.duration_min for step in plan.steps),
        overrun_minutes=0,
        reflowed_step_positions=(),
        duration_adjustments=(),
        time_adjustments=(),
        warnings=tuple(warnings),
    )


def _parse_clock(value: str) -> Optional[int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _format_clock(absolute_minute: int) -> tuple[str, int]:
    day_offset, minute_of_day = divmod(absolute_minute, 24 * 60)
    hour, minute = divmod(minute_of_day, 60)
    return f"{hour:02d}:{minute:02d}", day_offset
