"""Verify fixed-scenario live plan quality artifacts from raw projections."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .quality import evaluate_projection
from .verify import verify_live_model_observation


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_FIELDS = {
    "schema_id",
    "schema_version",
    "classification",
    "evidence_level",
    "observed_at",
    "scenario_id",
    "provider",
    "linked_observation_sha256",
    "data_profile",
    "policy",
    "plan_projection",
    "selected_poi_facts",
    "checks",
    "metrics",
    "privacy",
    "limitations",
    "artifact_sha256",
}
_STEP_FIELDS = {
    "step_index",
    "kind",
    "poi_id",
    "poi_name",
    "start_time",
    "duration_min",
    "mode_to_here",
    "travel_time_min",
    "travel_distance_m",
    "weather_shelter",
}
_FACT_FIELDS = {
    "poi_id",
    "poi_name",
    "category_lv1",
    "category_lv2",
    "rating",
    "avg_price",
    "positive_evidence_tags",
    "risk_tags",
}
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "password",
    "prompt",
    "rationale",
    "raw_model_output",
    "raw_output",
    "request_body",
    "secret",
    "summary",
    "token",
    "user_id",
    "user_input",
}
_EXPECTED_POLICIES = {
    "synthetic-friends-sanlitun-3h-budget": {
        "persona": "friends",
        "area_anchor": "三里屯片区",
        "target_start": "14:00",
        "duration_minutes": 180,
        "budget_per_person": 200,
        "walk_radius_m": 800,
        "walking_tolerance_multiplier": 1.5,
        "minimum_activity_steps": 2,
        "required_kinds": [],
        "required_diet_evidence_tags": [],
        "required_any_positive_tags": [],
        "required_indoor_kind": None,
    },
    "synthetic-family-wudaoying-4h-child-diet": {
        "persona": "family",
        "area_anchor": "五道营-雍和宫片区",
        "target_start": "14:00",
        "duration_minutes": 240,
        "budget_per_person": 150,
        "walk_radius_m": 800,
        "walking_tolerance_multiplier": 1.5,
        "minimum_activity_steps": 2,
        "required_kinds": ["meal"],
        "required_diet_evidence_tags": ["no_spicy"],
        "required_any_positive_tags": ["child_friendly", "family_meal"],
        "required_indoor_kind": None,
    },
    "synthetic-solo-798-3h-indoor": {
        "persona": "solo",
        "area_anchor": "798艺术区片区",
        "target_start": "14:00",
        "duration_minutes": 180,
        "budget_per_person": 300,
        "walk_radius_m": 1200,
        "walking_tolerance_multiplier": 1.5,
        "minimum_activity_steps": 2,
        "required_kinds": ["culture"],
        "required_diet_evidence_tags": [],
        "required_any_positive_tags": [],
        "required_indoor_kind": "culture",
    },
}


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _reject_forbidden_keys(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden persisted field at {path}.{key}")
            _reject_forbidden_keys(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_keys(item, path=f"{path}[{index}]")


def _verify_projection(projection: dict[str, Any]) -> None:
    if set(projection) != {"persona", "area_anchor", "steps", "route", "schedule"}:
        raise ValueError("live quality projection fields differ")
    steps = projection.get("steps")
    if not isinstance(steps, list) or not 2 <= len(steps) <= 8:
        raise ValueError("live quality projection step count is invalid")
    for step in steps:
        if not isinstance(step, dict) or set(step) != _STEP_FIELDS:
            raise ValueError("live quality projection step fields differ")
        if not isinstance(step.get("poi_name"), str) or not step["poi_name"]:
            raise ValueError("live quality projection POI name is invalid")
        for field in ("step_index", "duration_min", "travel_time_min", "travel_distance_m"):
            value = step.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"live quality projection {field} is invalid")
    if not isinstance(projection.get("route"), dict) or not isinstance(
        projection.get("schedule"), dict
    ):
        raise ValueError("live quality route or schedule projection is invalid")


def _verify_facts(facts: Any) -> None:
    if not isinstance(facts, list):
        raise ValueError("selected POI facts must be an array")
    ids = []
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != _FACT_FIELDS:
            raise ValueError("selected POI fact fields differ")
        if not isinstance(fact.get("poi_id"), str) or not fact["poi_id"]:
            raise ValueError("selected POI fact ID is invalid")
        if not isinstance(fact.get("poi_name"), str) or not fact["poi_name"]:
            raise ValueError("selected POI fact name is invalid")
        ids.append(fact["poi_id"])
        for field in ("positive_evidence_tags", "risk_tags"):
            values = fact.get(field)
            if (
                not isinstance(values, list)
                or values != sorted(set(values))
                or not all(isinstance(value, str) and value for value in values)
            ):
                raise ValueError(f"selected POI {field} is not canonical")
    if len(ids) != len(set(ids)):
        raise ValueError("selected POI facts contain duplicate IDs")


def verify_live_plan_quality(
    quality_path: Path,
    observation_path: Path,
) -> dict[str, Any]:
    artifact = json.loads(quality_path.read_text(encoding="utf-8"))
    canonical = deepcopy(artifact)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _sha(canonical):
        raise ValueError("live plan quality SHA-256 mismatch")
    _reject_forbidden_keys(artifact)
    if set(artifact) != _ARTIFACT_FIELDS:
        raise ValueError("live plan quality artifact fields differ")
    if artifact.get("schema_id") != "bj-pal.live-plan-quality":
        raise ValueError("unexpected live plan quality schema")
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported live plan quality schema version")
    if artifact.get("classification") != "fixed_synthetic_live_plan_quality_proxy":
        raise ValueError("unexpected live plan quality classification")
    if (
        artifact.get("evidence_level")
        != "deterministic_rules_over_sanitized_live_plan_projection"
    ):
        raise ValueError("live plan quality evidence level overclaims provenance")
    datetime.fromisoformat(str(artifact.get("observed_at")).replace("Z", "+00:00"))

    scenario_id = artifact.get("scenario_id")
    if scenario_id not in _EXPECTED_POLICIES:
        raise ValueError("live plan quality scenario is not fixed")
    if artifact.get("policy") != _EXPECTED_POLICIES[scenario_id]:
        raise ValueError("live plan quality policy differs from fixed registry")

    observation = verify_live_model_observation(observation_path)
    if observation["result"]["outcome"] not in {"accepted", "accepted_after_repair"}:
        raise ValueError("live plan quality cannot link to a rejected observation")
    if (
        artifact.get("linked_observation_sha256") != observation["artifact_sha256"]
        or artifact.get("scenario_id") != observation["scenario_id"]
        or artifact.get("observed_at") != observation["observed_at"]
        or artifact.get("provider") != observation["provider"]
    ):
        raise ValueError("live plan quality observation link mismatch")
    if not _HEX64.fullmatch(str(artifact.get("linked_observation_sha256"))):
        raise ValueError("live plan quality observation SHA is invalid")

    provider = artifact.get("provider")
    if not isinstance(provider, dict):
        raise ValueError("live plan quality provider is missing")
    endpoint = urlsplit(str(provider.get("endpoint_origin") or ""))
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.path not in {"", "/"}
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("live plan quality endpoint is not an HTTPS origin")

    if artifact.get("data_profile") != {
        "name": "demo",
        "classification": "synthetic",
        "public_reproducible": True,
    }:
        raise ValueError("live plan quality data profile must be public demo synthetic")

    projection = artifact.get("plan_projection")
    if not isinstance(projection, dict):
        raise ValueError("live plan quality projection is missing")
    _verify_projection(projection)
    facts = artifact.get("selected_poi_facts")
    _verify_facts(facts)
    expected_checks, expected_metrics = evaluate_projection(
        projection,
        facts,
        artifact["policy"],
    )
    if artifact.get("checks") != expected_checks:
        raise ValueError("live plan quality checks do not match raw projection")
    if artifact.get("metrics") != expected_metrics:
        raise ValueError("live plan quality metrics do not match raw checks")

    if artifact.get("privacy") != {
        "fixed_synthetic_scenario_only": True,
        "request_text_persisted": False,
        "raw_prompt_persisted": False,
        "raw_model_output_persisted": False,
        "rationale_or_summary_persisted": False,
        "auth_material_persisted": False,
    }:
        raise ValueError("live plan quality privacy boundary differs")
    limitations = artifact.get("limitations")
    if not isinstance(limitations, list) or len(limitations) < 5:
        raise ValueError("live plan quality limitations are incomplete")
    return artifact


def verify_live_plan_quality_suite(
    pairs: list[tuple[Path, Path]] | tuple[tuple[Path, Path], ...],
) -> dict[str, Any]:
    artifacts = [verify_live_plan_quality(quality, observation) for quality, observation in pairs]
    scenario_ids = [artifact["scenario_id"] for artifact in artifacts]
    if len(artifacts) != 3 or set(scenario_ids) != set(_EXPECTED_POLICIES):
        raise ValueError("live plan quality suite scenario set is incomplete or duplicated")
    if len({artifact["artifact_sha256"] for artifact in artifacts}) != 3:
        raise ValueError("live plan quality suite artifacts must be distinct")
    if {artifact["provider"]["configured_client"] for artifact in artifacts} != {"dpsk"}:
        raise ValueError("live plan quality suite client differs")
    if {artifact["provider"]["configured_model"] for artifact in artifacts} != {
        "deepseek-v4-pro"
    }:
        raise ValueError("live plan quality suite model differs")
    ordered = sorted(artifacts, key=lambda artifact: artifact["scenario_id"])
    gate_pass_count = sum(artifact["metrics"]["hard_gate_pass"] for artifact in ordered)
    return {
        "classification": "three_fixed_synthetic_live_plan_quality_proxies",
        "case_count": len(ordered),
        "hard_gate_pass_count": gate_pass_count,
        "not_evaluable_count": sum(
            artifact["metrics"]["not_evaluable_count"] for artifact in ordered
        ),
        "suite_gate_pass": gate_pass_count == len(ordered),
        "cases": [
            {
                "scenario_id": artifact["scenario_id"],
                "hard_gate_pass": artifact["metrics"]["hard_gate_pass"],
                "required_check_count": artifact["metrics"]["required_check_count"],
                "required_pass_count": artifact["metrics"]["required_pass_count"],
                "not_evaluable_count": artifact["metrics"]["not_evaluable_count"],
                "quality_artifact_sha256": artifact["artifact_sha256"],
                "observation_artifact_sha256": artifact[
                    "linked_observation_sha256"
                ],
            }
            for artifact in ordered
        ],
        "limitations": [
            "Counts cover exactly three fixed synthetic scenarios run once each.",
            "The deterministic gate is not a success-rate or human-quality estimate.",
            "The suite does not evaluate rationale, freshness, or user outcomes.",
            "Provider identity is configured-client evidence, not a signed receipt.",
        ],
    }
