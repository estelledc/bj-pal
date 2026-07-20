from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.tracing import capture_execution, trace_span  # noqa: E402
from agents.types import Plan, Step, UserPreferences  # noqa: E402
from application import (  # noqa: E402
    ExecutionObservation,
    PlanRequest,
    PlanningCallbacks,
    PlanningService,
)
from data_profile import DataProfile  # noqa: E402
from jobs import PlanningJobRepository, PlanningJobService  # noqa: E402
from tools import tool_call_log  # noqa: E402


def _plan() -> Plan:
    return Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=[
            Step(
                step_index=1,
                kind="meal",
                poi_id="poi-observed",
                poi_name="可观测餐厅",
                start_time="14:00",
            )
        ],
        plan_id="plan-observed",
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


def test_service_returns_self_checking_privacy_minimized_execution_ledger() -> None:
    private_input = "和 Alice 去机密地点 private-secret"
    private_user = "alice@example.com"

    def planner(**kwargs):
        assert kwargs["user_input"] == private_input
        assert kwargs["user_id"] == private_user
        with trace_span("llm.fixture.complete") as span:
            span.set_attribute("input_tokens", 13)
            span.set_attribute("output_tokens", 7)
            span.set_attribute("prompt", private_input)
        return _plan()

    result = PlanningService(
        planner=planner,
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
    ).execute(
        PlanRequest(
            user_input=private_input,
            user_id=private_user,
            preferences=UserPreferences(persona="family"),
        ),
        callbacks=PlanningCallbacks(correlation_id="req-observation-1"),
    )

    observation = result.execution
    payload = observation.to_dict()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert observation.verify_integrity() is True
    assert observation.correlation_id == "req-observation-1"
    assert observation.status == "succeeded"
    assert observation.operation_counts["span_count"] == len(observation.spans)
    assert observation.operation_counts["llm_call_count"] == 1
    assert observation.token_usage.completeness == "complete"
    assert observation.token_usage.input_tokens == 13
    assert observation.token_usage.output_tokens == 7
    assert private_input not in serialized
    assert private_user not in serialized
    assert [span.name for span in observation.spans][0] == "planning.execute"
    assert {
        "planning.preflight",
        "planning.generate",
        "planning.probe_and_replan",
        "planning.persist_trace",
        "planning.load_data_profile",
    }.issubset({span.name for span in observation.spans})

    tampered = observation.to_dict()
    tampered["duration_ms"] += 1
    rebuilt = ExecutionObservation(
        version=tampered["version"],
        status=tampered["status"],
        execution_id=tampered["execution_id"],
        correlation_id=tampered["correlation_id"],
        trace_id=tampered["trace_id"],
        started_at=tampered["started_at"],
        duration_ms=tampered["duration_ms"],
        spans=observation.spans,
        operation_counts=observation.operation_counts,
            business_counts=observation.business_counts,
            token_usage=observation.token_usage,
            execution_budget=observation.execution_budget,
            artifact_sha256=observation.artifact_sha256,
    )
    assert rebuilt.verify_integrity() is False


def test_mock_llm_usage_is_unavailable_instead_of_invented() -> None:
    def planner(**kwargs):
        with trace_span("llm.mock.complete"):
            return _plan()

    observation = PlanningService(
        planner=planner,
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
    ).execute(PlanRequest(user_input="下午出去玩")).execution

    assert observation.operation_counts["llm_call_count"] == 1
    assert observation.token_usage.completeness == "unavailable"
    assert observation.token_usage.reported_calls == 0
    assert observation.token_usage.input_tokens is None
    assert observation.token_usage.output_tokens is None


def test_tool_span_is_counted_and_failure_trace_keeps_error_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool_call_log, "LOG_DB", tmp_path / "tool-calls.db")

    def planner(**kwargs):
        with tool_call_log.timed_call("fixture.lookup") as record:
            record["response"] = {"ok": True}
        return _plan()

    observation = PlanningService(
        planner=planner,
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
    ).execute(PlanRequest(user_input="下午出去玩")).execution
    assert observation.operation_counts["tool_call_count"] == 1
    assert any(span.name == "tool.fixture.lookup" for span in observation.spans)

    with capture_execution("req-error") as capture:
        with pytest.raises(RuntimeError, match="boom"):
            with trace_span("planning.execute"):
                with trace_span("planning.generate"):
                    raise RuntimeError("boom")
    failed = ExecutionObservation.from_trace_snapshot(
        capture.snapshot(),
        status="failed",
    )
    assert failed.status == "failed"
    assert failed.verify_integrity() is True
    assert {span.status for span in failed.spans} == {"error"}


def test_durable_worker_correlates_execution_to_job_id(tmp_path: Path) -> None:
    service = PlanningService(
        planner=lambda **kwargs: _plan(),
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
    )
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=service,
    )
    submitted = jobs.submit(
        request=PlanRequest(user_input="下午出去玩"),
        request_id="request-for-job",
    )
    completed = jobs.run_once(worker_id="observation-worker")

    assert completed is not None and completed.status == "succeeded"
    execution = completed.result_payload["execution"]
    assert execution["correlation_id"] == submitted.job_id
    assert execution["status"] == "succeeded"
