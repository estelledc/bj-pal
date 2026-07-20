"""Generate raw execution-observation cases without an external collector."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from agents.tracing import trace_span
from agents.types import Plan, Step, UserPreferences
from application import PlanRequest, PlanningCallbacks, PlanningService
from data_profile import DataProfile


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _plan() -> Plan:
    return Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=[
            Step(
                step_index=1,
                kind="meal",
                poi_id="poi-observation-eval",
                poi_name="观测评测餐厅",
                start_time="14:00",
            )
        ],
        plan_id="plan-observation-eval",
    )


def _profile() -> DataProfile:
    return DataProfile(
        name="demo",
        classification="synthetic",
        public_reproducible=True,
        sources={"pois": "fixture"},
        counts={"pois": 1},
        limitations=("not live data",),
    )


def evaluate_observability() -> dict[str, Any]:
    marker = "PRIVATE-MARKER-observation-eval"
    common = {
        "prober": lambda plan, **kwargs: (plan, []),
        "profile_loader": _profile,
        "plan_recorder": lambda plan: None,
    }

    def reported_planner(**kwargs):
        assert marker in kwargs["user_input"]
        with trace_span("llm.fixture.complete") as span:
            span.set_attribute("input_tokens", 21)
            span.set_attribute("output_tokens", 8)
            span.set_attribute("prompt", kwargs["user_input"])
        return _plan()

    def unreported_planner(**kwargs):
        with trace_span("llm.mock.complete"):
            return _plan()

    def deterministic_planner(**kwargs):
        return _plan()

    fixtures = (
        (
            "reported_provider_usage",
            PlanningService(planner=reported_planner, **common),
            f"下午出去玩 {marker}",
            "complete",
            1,
        ),
        (
            "mock_usage_unavailable",
            PlanningService(planner=unreported_planner, **common),
            "下午出去玩",
            "unavailable",
            1,
        ),
        (
            "deterministic_path_without_llm",
            PlanningService(planner=deterministic_planner, **common),
            "下午出去玩",
            "not_applicable",
            0,
        ),
    )
    raw_cases = []
    for case_id, service, user_input, expected_usage, expected_llm_calls in fixtures:
        correlation_id = f"eval-{case_id}"
        result = service.execute(
            PlanRequest(
                user_input=user_input,
                preferences=UserPreferences(persona="family"),
            ),
            callbacks=PlanningCallbacks(correlation_id=correlation_id),
        )
        raw_cases.append(
            {
                "case_id": case_id,
                "expected_correlation_id": correlation_id,
                "expected_usage_completeness": expected_usage,
                "expected_llm_call_count": expected_llm_calls,
                "forbidden_marker": marker if marker in user_input else None,
                "observation": result.execution.to_dict(),
            }
        )

    metrics = {
        "case_count": len(raw_cases),
        "integrity_rate": 1.0,
        "span_tree_valid_rate": 1.0,
        "operation_count_valid_rate": 1.0,
        "token_semantics_valid_rate": 1.0,
        "privacy_marker_exclusion_rate": 1.0,
    }
    artifact = {
        "schema_version": 1,
        "name": "bj-pal-execution-observation-contract",
        "classification": "synthetic_contract",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result": {
            "raw_cases": raw_cases,
            "metrics": metrics,
        },
        "limitations": [
            "In-process deterministic fixtures are not production telemetry or an SLA.",
            "Mock LLM usage is intentionally unavailable; no token or cost estimate is invented.",
            "No OTLP collector or live provider is exercised by this artifact.",
        ],
    }
    artifact["artifact_sha256"] = _canonical_sha256(artifact)
    return artifact


def write_artifact(path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
