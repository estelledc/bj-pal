from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.execution_budget import (  # noqa: E402
    ExecutionBudgetExceeded,
    ExecutionBudgetPolicy,
    enforce_execution_budget,
    max_transport_attempts,
)
from agents.llm_client import AnthropicClient  # noqa: E402
from agents.tracing import trace_span  # noqa: E402
from agents.types import Plan, Step  # noqa: E402
from application import PlanRequest, PlanningService  # noqa: E402
from data_profile import DataProfile  # noqa: E402
from http_api.app import create_app  # noqa: E402
from http_api.schemas import ReadinessResponse  # noqa: E402
from jobs import PlanningJobRepository, PlanningJobService  # noqa: E402


def _plan() -> Plan:
    return Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=[
            Step(
                step_index=1,
                kind="meal",
                poi_id="poi-budget",
                poi_name="预算测试餐厅",
                start_time="14:00",
            )
        ],
        plan_id="plan-budget",
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


def _service(*, planner, policy: ExecutionBudgetPolicy, clock=None) -> PlanningService:
    return PlanningService(
        planner=planner,
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
        execution_budget_policy=policy,
        budget_clock=clock,
    )


def test_successful_budget_is_bound_to_execution_observation() -> None:
    def planner(**kwargs):
        with trace_span("planner.collect_data"):
            pass
        with trace_span("llm.fixture.complete") as span:
            span.set_attribute("input_tokens", 9)
            span.set_attribute("output_tokens", 4)
        return _plan()

    result = _service(
        planner=planner,
        policy=ExecutionBudgetPolicy(),
    ).execute(PlanRequest(user_input="下午出去玩"))

    snapshot = result.execution.execution_budget
    assert snapshot is not None
    assert snapshot.verify_integrity() is True
    assert snapshot.status == "succeeded"
    assert snapshot.termination_reason == "completed"
    assert snapshot.usage.llm_call_count == 1
    assert snapshot.usage.data_provider_batch_count == 1
    assert snapshot.usage.reported_token_call_count == 1
    assert snapshot.usage.reported_total_tokens == 13
    assert result.execution.verify_integrity() is True


def test_llm_limit_stops_before_n_plus_one_body_executes() -> None:
    second_body_executed = False

    def planner(**kwargs):
        nonlocal second_body_executed
        with trace_span("llm.fixture.complete"):
            pass
        with trace_span("llm.fixture.complete"):
            second_body_executed = True
        return _plan()

    with pytest.raises(ExecutionBudgetExceeded) as raised:
        _service(
            planner=planner,
            policy=ExecutionBudgetPolicy(max_llm_calls=1),
        ).execute(PlanRequest(user_input="下午出去玩"))

    snapshot = raised.value.snapshot
    assert second_body_executed is False
    assert snapshot.termination_reason == "llm_call_limit"
    assert snapshot.usage.llm_call_count == 2
    assert snapshot.verify_integrity() is True


def test_reported_token_limit_stops_after_usage_is_known() -> None:
    after_call_executed = False

    def planner(**kwargs):
        nonlocal after_call_executed
        with trace_span("llm.fixture.complete") as span:
            span.set_attribute("input_tokens", 7)
            span.set_attribute("output_tokens", 4)
        after_call_executed = True
        return _plan()

    with pytest.raises(ExecutionBudgetExceeded) as raised:
        _service(
            planner=planner,
            policy=ExecutionBudgetPolicy(max_reported_tokens=10),
        ).execute(PlanRequest(user_input="下午出去玩"))

    snapshot = raised.value.snapshot
    assert after_call_executed is False
    assert snapshot.termination_reason == "reported_token_limit"
    assert snapshot.usage.reported_total_tokens == 11


def test_wall_clock_limit_is_checked_before_next_operation() -> None:
    now = [0.0]
    llm_body_executed = False

    def planner(**kwargs):
        nonlocal llm_body_executed
        now[0] = 0.010
        with trace_span("llm.fixture.complete"):
            llm_body_executed = True
        return _plan()

    with pytest.raises(ExecutionBudgetExceeded) as raised:
        _service(
            planner=planner,
            policy=ExecutionBudgetPolicy(max_wall_clock_ms=5),
            clock=lambda: now[0],
        ).execute(PlanRequest(user_input="下午出去玩"))

    assert llm_body_executed is False
    assert raised.value.snapshot.termination_reason == "wall_clock_limit"
    assert raised.value.snapshot.usage.elapsed_ms == 10.0


def test_request_local_budgets_do_not_share_counts_between_threads() -> None:
    barrier = threading.Barrier(2)
    results = []
    failures = []

    def run(label: str) -> None:
        try:
            def planner(**kwargs):
                barrier.wait(timeout=2)
                with trace_span(f"llm.{label}.complete"):
                    pass
                return _plan()

            result = _service(
                planner=planner,
                policy=ExecutionBudgetPolicy(max_llm_calls=1),
            ).execute(PlanRequest(user_input=f"下午出去玩 {label}"))
            results.append(result.execution.execution_budget)
        except Exception as exc:  # pragma: no cover - assertion reports details
            failures.append(exc)

    threads = [threading.Thread(target=run, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert failures == []
    assert len(results) == 2
    assert all(item is not None and item.usage.llm_call_count == 1 for item in results)


def test_transport_retry_cap_is_owned_by_current_server_policy() -> None:
    policy = ExecutionBudgetPolicy(max_transport_attempts_per_llm_call=2)
    assert max_transport_attempts() == 4
    with enforce_execution_budget(policy):
        assert max_transport_attempts() == 2
    assert max_transport_attempts() == 4


def test_anthropic_fallback_is_traced_and_cannot_bypass_llm_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls = []

    class FakeMessages:
        def create(self, **kwargs):
            provider_calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"answer":"ok"}')],
                usage=SimpleNamespace(input_tokens=5, output_tokens=2),
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.messages = FakeMessages()

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=FakeAnthropic),
    )
    client = AnthropicClient()

    def planner(**kwargs):
        client.complete("system", "first")
        client.complete("system", "second")
        return _plan()

    with pytest.raises(ExecutionBudgetExceeded) as raised:
        _service(
            planner=planner,
            policy=ExecutionBudgetPolicy(max_llm_calls=1),
        ).execute(PlanRequest(user_input="下午出去玩"))

    assert len(provider_calls) == 1
    assert raised.value.snapshot.termination_reason == "llm_call_limit"
    assert raised.value.snapshot.usage.llm_call_count == 2
    assert raised.value.snapshot.usage.reported_total_tokens == 7


def test_invalid_environment_budget_fails_closed() -> None:
    with pytest.raises(ValueError, match="BJ_PAL_MAX_LLM_CALLS"):
        ExecutionBudgetPolicy.from_env({"BJ_PAL_MAX_LLM_CALLS": "zero"})
    with pytest.raises(ValueError, match="BJ_PAL_MAX_EXECUTION_MS"):
        ExecutionBudgetPolicy.from_env({"BJ_PAL_MAX_EXECUTION_MS": "0"})


def test_http_returns_structured_429_budget_evidence() -> None:
    def planner(**kwargs):
        with trace_span("llm.fixture.complete"):
            pass
        with trace_span("llm.fixture.complete"):
            pass
        return _plan()

    service = _service(
        planner=planner,
        policy=ExecutionBudgetPolicy(max_llm_calls=1),
    )
    app = create_app(
        service=service,
        readiness_probe=lambda: ReadinessResponse(
            status="ready",
            data_profile="demo",
            classification="synthetic",
            checks={"dataset_manifest": "ok"},
        ),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/plans",
            headers={"X-Request-ID": "budget-http"},
            json={"user_input": "下午出去玩"},
        )

    payload = response.json()["error"]
    assert response.status_code == 429
    assert payload["code"] == "execution_budget_exceeded"
    assert payload["request_id"] == "budget-http"
    details = payload["details"]
    assert details["termination_reason"] == "llm_call_limit"
    assert details["usage"]["llm_call_count"] == 2
    assert len(details["artifact_sha256"]) == 64


def test_durable_budget_failure_is_terminal_without_retry(tmp_path: Path) -> None:
    def planner(**kwargs):
        with trace_span("llm.fixture.complete"):
            pass
        with trace_span("llm.fixture.complete"):
            pass
        return _plan()

    repository = PlanningJobRepository(tmp_path / "jobs.db")
    jobs = PlanningJobService(
        repository=repository,
        planning_service=_service(
            planner=planner,
            policy=ExecutionBudgetPolicy(max_llm_calls=1),
        ),
        retry_base_seconds=0,
        retry_max_seconds=0,
    )
    submitted = jobs.submit(
        request=PlanRequest(user_input="下午出去玩"),
        request_id="budget-job",
    )

    finished = jobs.run_once(worker_id="budget-worker")

    assert finished is not None
    assert finished.status == "failed"
    assert finished.error_code == "execution_budget_exceeded"
    assert finished.attempt == 1
    events = repository.list_events(submitted.job_id)
    assert "retry_scheduled" not in [event.event_type for event in events]
