"""Build a sanitized, recomputable quality proxy for one fixed live plan.

The artifact stores only a fixed synthetic scenario ID, deterministic POI facts,
and a projection of the generated plan. It deliberately excludes request text,
prompt, rationale, summary, raw model output, auth material, and user identity.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping

from agents.types import Plan
from application import PlanResult
from loader import get_conn

from .scenarios import LiveModelScenario


SCHEMA_ID = "bj-pal.live-plan-quality"
SCHEMA_VERSION = 1
CLASSIFICATION = "fixed_synthetic_live_plan_quality_proxy"
EVIDENCE_LEVEL = "deterministic_rules_over_sanitized_live_plan_projection"
_STATUS_VALUES = {"pass", "fail", "not_applicable", "not_evaluable"}


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _plan_projection(plan: Plan) -> dict[str, Any]:
    return {
        "persona": plan.persona,
        "area_anchor": plan.area_anchor,
        "steps": [
            {
                "step_index": step.step_index,
                "kind": step.kind,
                "poi_id": step.poi_id,
                "poi_name": step.poi_name,
                "start_time": step.start_time,
                "duration_min": step.duration_min,
                "mode_to_here": step.mode_to_here,
                "travel_time_min": step.travel_time_min,
                "travel_distance_m": step.travel_distance_m,
                "weather_shelter": step.weather_shelter,
            }
            for step in plan.steps
        ],
        "route": {
            "version": plan.route_context.get("version"),
            "status": plan.route_context.get("status"),
            "resolved_poi_step_count": plan.route_context.get(
                "resolved_poi_step_count"
            ),
            "total_poi_step_count": plan.route_context.get("total_poi_step_count"),
        },
        "schedule": {
            "version": plan.schedule_context.get("version"),
            "status": plan.schedule_context.get("status"),
            "target_start": plan.schedule_context.get("target_start"),
            "window_minutes": plan.schedule_context.get("window_minutes"),
            "planned_end": plan.schedule_context.get("planned_end"),
            "total_elapsed_minutes": plan.schedule_context.get(
                "total_elapsed_minutes"
            ),
            "overrun_minutes": plan.schedule_context.get("overrun_minutes"),
        },
    }


def _selected_poi_facts(plan: Plan) -> list[dict[str, Any]]:
    selected_ids = [step.poi_id for step in plan.steps if step.poi_id]
    if not selected_ids:
        return []
    placeholders = ",".join("?" for _ in selected_ids)
    conn = get_conn()
    try:
        poi_rows = conn.execute(
            f"""
            SELECT id, name, category_lv1, category_lv2, rating, avg_price
            FROM pois
            WHERE id IN ({placeholders})
            """,
            selected_ids,
        ).fetchall()
        names = [str(row["name"]) for row in poi_rows]
        tag_rows = []
        if names:
            name_placeholders = ",".join("?" for _ in names)
            tag_rows = conn.execute(
                f"""
                SELECT poi_name, sentiment, confidence, needs_review,
                       normalized_value_json
                FROM ugc_aspects
                WHERE poi_name IN ({name_placeholders})
                """,
                names,
            ).fetchall()
    finally:
        conn.close()

    positive_tags: dict[str, set[str]] = {name: set() for name in names}
    risk_tags: dict[str, set[str]] = {name: set() for name in names}
    for row in tag_rows:
        try:
            normalized = json.loads(row["normalized_value_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        name = str(row["poi_name"])
        if (
            row["sentiment"] == "positive"
            and float(row["confidence"] or 0) >= 0.6
            and not bool(row["needs_review"])
        ):
            for key in ("taste_tags", "scene_tags", "facility_tags"):
                values = normalized.get(key) or []
                if isinstance(values, list):
                    positive_tags.setdefault(name, set()).update(
                        value for value in values if isinstance(value, str) and value
                    )
        values = normalized.get("risk_tags") or []
        if isinstance(values, list):
            risk_tags.setdefault(name, set()).update(
                value for value in values if isinstance(value, str) and value
            )

    by_id = {str(row["id"]): row for row in poi_rows}
    facts = []
    for poi_id in selected_ids:
        row = by_id.get(str(poi_id))
        if row is None:
            continue
        name = str(row["name"])
        facts.append(
            {
                "poi_id": str(row["id"]),
                "poi_name": name,
                "category_lv1": row["category_lv1"],
                "category_lv2": row["category_lv2"],
                "rating": (
                    round(float(row["rating"]), 3)
                    if row["rating"] is not None
                    else None
                ),
                "avg_price": (
                    round(float(row["avg_price"]), 2)
                    if row["avg_price"] is not None
                    else None
                ),
                "positive_evidence_tags": sorted(positive_tags.get(name, set())),
                "risk_tags": sorted(risk_tags.get(name, set())),
            }
        )
    return facts


def _check(
    check_id: str,
    *,
    required: bool,
    status: str,
    observed: Any,
) -> dict[str, Any]:
    if status not in _STATUS_VALUES:
        raise ValueError("unsupported quality check status")
    return {
        "check_id": check_id,
        "required": required,
        "status": status,
        "observed": observed,
    }


def evaluate_projection(
    projection: Mapping[str, Any],
    selected_poi_facts: list[dict[str, Any]],
    policy: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compute deterministic constraint proxies from raw sanitized fields."""
    steps = list(projection.get("steps") or [])
    activities = [step for step in steps if step.get("kind") != "depart"]
    fact_by_id = {fact["poi_id"]: fact for fact in selected_poi_facts}
    checks: list[dict[str, Any]] = []

    identity_ok = (
        projection.get("persona") == policy.get("persona")
        and projection.get("area_anchor") == policy.get("area_anchor")
    )
    checks.append(
        _check(
            "identity_match",
            required=True,
            status="pass" if identity_ok else "fail",
            observed={
                "persona": projection.get("persona"),
                "area_anchor": projection.get("area_anchor"),
            },
        )
    )

    minimum = int(policy["minimum_activity_steps"])
    checks.append(
        _check(
            "minimum_activity_steps",
            required=True,
            status="pass" if len(activities) >= minimum else "fail",
            observed={"count": len(activities), "minimum": minimum},
        )
    )

    depart = [step for step in steps if step.get("kind") == "depart"]
    depart_ok = (
        len(depart) == 1
        and steps
        and steps[-1] is depart[0]
        and depart[0].get("poi_id") is None
        and depart[0].get("poi_name") == "返程"
        and depart[0].get("duration_min") == 0
        and depart[0].get("mode_to_here") == "transit"
        and [step.get("step_index") for step in steps]
        == list(range(1, len(steps) + 1))
    )
    checks.append(
        _check(
            "depart_and_sequence_contract",
            required=True,
            status="pass" if depart_ok else "fail",
            observed={"depart_count": len(depart), "step_count": len(steps)},
        )
    )

    grounded = True
    grounding_mismatches = []
    for step in activities:
        fact = fact_by_id.get(step.get("poi_id"))
        if fact is None or fact.get("poi_name") != step.get("poi_name"):
            grounded = False
            grounding_mismatches.append(step.get("step_index"))
    if len(fact_by_id) != len(activities):
        grounded = False
    checks.append(
        _check(
            "selected_poi_grounding",
            required=True,
            status="pass" if grounded else "fail",
            observed={
                "activity_count": len(activities),
                "fact_count": len(fact_by_id),
                "mismatch_step_indices": grounding_mismatches,
            },
        )
    )

    temporal_ok = bool(steps) and steps[0].get("start_time") == policy.get(
        "target_start"
    )
    temporal_violations = []
    previous_start = None
    previous_duration = None
    for step in steps:
        try:
            start = _minutes(str(step.get("start_time")))
            duration = int(step.get("duration_min"))
            travel = int(step.get("travel_time_min"))
        except (TypeError, ValueError, AttributeError):
            temporal_ok = False
            temporal_violations.append(step.get("step_index"))
            continue
        if (
            previous_start is not None
            and start < previous_start + int(previous_duration or 0) + travel
        ):
            temporal_ok = False
            temporal_violations.append(step.get("step_index"))
        previous_start = start
        previous_duration = duration
    checks.append(
        _check(
            "travel_aware_timeline",
            required=True,
            status="pass" if temporal_ok else "fail",
            observed={
                "target_start": policy.get("target_start"),
                "violation_step_indices": temporal_violations,
            },
        )
    )

    schedule = dict(projection.get("schedule") or {})
    duration_ok = (
        schedule.get("version") == "schedule_reconcile_v1"
        and schedule.get("status") == "complete"
        and schedule.get("target_start") == policy.get("target_start")
        and schedule.get("window_minutes") == policy.get("duration_minutes")
        and schedule.get("overrun_minutes") == 0
        and isinstance(schedule.get("total_elapsed_minutes"), int)
        and schedule["total_elapsed_minutes"] <= policy.get("duration_minutes")
    )
    checks.append(
        _check(
            "duration_window",
            required=True,
            status="pass" if duration_ok else "fail",
            observed=schedule,
        )
    )

    route = dict(projection.get("route") or {})
    route_ok = (
        route.get("version") == "route_refresh_v1"
        and route.get("status") == "complete"
        and route.get("resolved_poi_step_count")
        == route.get("total_poi_step_count")
        == len(activities)
    )
    checks.append(
        _check(
            "route_completeness",
            required=True,
            status="pass" if route_ok else "fail",
            observed=route,
        )
    )

    walking_limit = round(
        int(policy["walk_radius_m"])
        * float(policy["walking_tolerance_multiplier"])
    )
    walking_legs = [
        int(step.get("travel_distance_m") or 0)
        for step in steps
        if step.get("mode_to_here") == "walking"
    ]
    maximum_walking_leg = max(walking_legs, default=0)
    checks.append(
        _check(
            "walking_leg_proxy",
            required=True,
            status="pass" if maximum_walking_leg <= walking_limit else "fail",
            observed={
                "maximum_walking_leg_m": maximum_walking_leg,
                "limit_m": walking_limit,
                "tolerance_multiplier": policy["walking_tolerance_multiplier"],
            },
        )
    )

    observed_kinds = sorted({str(step.get("kind")) for step in activities})
    required_kinds = list(policy.get("required_kinds") or [])
    missing_kinds = sorted(set(required_kinds) - set(observed_kinds))
    checks.append(
        _check(
            "required_activity_kinds",
            required=bool(required_kinds),
            status=(
                "pass"
                if required_kinds and not missing_kinds
                else "fail"
                if required_kinds
                else "not_applicable"
            ),
            observed={"required": required_kinds, "missing": missing_kinds},
        )
    )

    priced_steps = [
        (step, fact_by_id.get(step.get("poi_id")))
        for step in activities
        if step.get("kind") in {"meal", "snack", "rest"}
    ]
    price_cap = policy.get("budget_per_person")
    unknown_price_steps = [
        step.get("step_index")
        for step, fact in priced_steps
        if fact is None or fact.get("avg_price") is None
    ]
    over_price_steps = [
        step.get("step_index")
        for step, fact in priced_steps
        if fact is not None
        and fact.get("avg_price") is not None
        and price_cap is not None
        and float(fact["avg_price"]) > float(price_cap)
    ]
    if price_cap is None or not priced_steps:
        price_status = "not_applicable"
        price_required = False
    elif unknown_price_steps:
        price_status = "not_evaluable"
        price_required = True
    else:
        price_status = "pass" if not over_price_steps else "fail"
        price_required = True
    checks.append(
        _check(
            "selected_food_price_cap",
            required=price_required,
            status=price_status,
            observed={
                "cap": price_cap,
                "unknown_step_indices": unknown_price_steps,
                "over_cap_step_indices": over_price_steps,
            },
        )
    )

    selected_positive_tags = sorted(
        {
            tag
            for fact in selected_poi_facts
            for tag in fact.get("positive_evidence_tags") or []
        }
    )
    required_diets = list(policy.get("required_diet_evidence_tags") or [])
    missing_diets = sorted(set(required_diets) - set(selected_positive_tags))
    checks.append(
        _check(
            "diet_evidence",
            required=bool(required_diets),
            status=(
                "pass"
                if required_diets and not missing_diets
                else "not_evaluable"
                if required_diets
                else "not_applicable"
            ),
            observed={"required": required_diets, "missing": missing_diets},
        )
    )

    any_tags = list(policy.get("required_any_positive_tags") or [])
    matched_any = sorted(set(any_tags).intersection(selected_positive_tags))
    checks.append(
        _check(
            "scenario_positive_evidence",
            required=bool(any_tags),
            status=(
                "pass"
                if any_tags and matched_any
                else "not_evaluable"
                if any_tags
                else "not_applicable"
            ),
            observed={"accepted_any": any_tags, "matched": matched_any},
        )
    )

    indoor_kind = policy.get("required_indoor_kind")
    matching_indoor = [
        step.get("step_index")
        for step in activities
        if step.get("kind") == indoor_kind
        and step.get("weather_shelter") in {"full_indoor", "mostly_indoor"}
    ]
    checks.append(
        _check(
            "indoor_activity_evidence",
            required=indoor_kind is not None,
            status=(
                "pass"
                if indoor_kind is not None and matching_indoor
                else "fail"
                if indoor_kind is not None
                else "not_applicable"
            ),
            observed={
                "required_kind": indoor_kind,
                "matching_step_indices": matching_indoor,
            },
        )
    )

    required_checks = [item for item in checks if item["required"]]
    metrics = {
        "check_count": len(checks),
        "required_check_count": len(required_checks),
        "required_pass_count": sum(
            item["status"] == "pass" for item in required_checks
        ),
        "required_fail_or_unknown_count": sum(
            item["status"] != "pass" for item in required_checks
        ),
        "not_evaluable_count": sum(
            item["status"] == "not_evaluable" for item in checks
        ),
        "hard_gate_pass": bool(required_checks)
        and all(item["status"] == "pass" for item in required_checks),
    }
    return checks, metrics


def build_live_plan_quality_artifact(
    *,
    observed_at: str,
    scenario: LiveModelScenario,
    provider: Mapping[str, str],
    linked_observation_sha256: str,
    result: PlanResult,
) -> dict[str, Any]:
    datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if len(linked_observation_sha256) != 64:
        raise ValueError("linked observation SHA-256 must be a hex digest")
    projection = _plan_projection(result.final_plan)
    facts = _selected_poi_facts(result.final_plan)
    policy = scenario.quality_policy()
    checks, metrics = evaluate_projection(projection, facts, policy)
    artifact: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "classification": CLASSIFICATION,
        "evidence_level": EVIDENCE_LEVEL,
        "observed_at": observed_at,
        "scenario_id": scenario.scenario_id,
        "provider": dict(provider),
        "linked_observation_sha256": linked_observation_sha256,
        "data_profile": {
            "name": result.data_profile.name,
            "classification": result.data_profile.classification,
            "public_reproducible": result.data_profile.public_reproducible,
        },
        "policy": policy,
        "plan_projection": projection,
        "selected_poi_facts": facts,
        "checks": checks,
        "metrics": metrics,
        "privacy": {
            "fixed_synthetic_scenario_only": True,
            "request_text_persisted": False,
            "raw_prompt_persisted": False,
            "raw_model_output_persisted": False,
            "rationale_or_summary_persisted": False,
            "auth_material_persisted": False,
        },
        "limitations": [
            "The checks are deterministic quality proxies, not a human preference judgment.",
            "POI, UGC, route, and weather inputs are public demo synthetic fixtures.",
            "A passing gate does not estimate user success, satisfaction, or production quality.",
            "Rationale, summary, prompt, raw output, request text, and auth material are excluded.",
            "Configured provider identity is linked to an unsigned local observation.",
        ],
    }
    artifact["artifact_sha256"] = _sha(artifact)
    return artifact
