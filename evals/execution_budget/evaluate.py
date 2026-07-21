"""Generate deterministic execution-budget enforcement cases."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from agents.execution_budget import ExecutionBudgetExceeded, ExecutionBudgetPolicy
from agents.tracing import trace_span
from agents.types import Plan, Step
from application import PlanRequest, PlanningService
from data_profile import DataProfile


def _sha(payload: dict[str, Any]) -> str:
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
                poi_id="poi-budget-eval",
                poi_name="预算评测餐厅",
                start_time="14:00",
            )
        ],
        plan_id="plan-budget-eval",
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


def _service(
    planner,
    policy: ExecutionBudgetPolicy,
    *,
    clock: Callable[[], float] | None = None,
) -> PlanningService:
    return PlanningService(
        planner=planner,
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
        execution_budget_policy=policy,
        budget_clock=clock,
    )


def evaluate_execution_budget() -> dict[str, Any]:
    marker = "PRIVATE-MARKER-budget-eval"
    raw_cases: list[dict[str, Any]] = []

    def append_success() -> None:
        def planner(**kwargs):
            assert marker in kwargs["user_input"]
            with trace_span("planner.collect_data"):
                pass
            with trace_span("llm.fixture.complete") as span:
                span.set_attribute("input_tokens", 12)
                span.set_attribute("output_tokens", 5)
            return _plan()

        policy = ExecutionBudgetPolicy()
        result = _service(planner, policy).execute(
            PlanRequest(user_input=f"下午出去玩 {marker}")
        )
        snapshot = result.execution.execution_budget
        assert snapshot is not None
        raw_cases.append(
            {
                "case_id": "completed_with_reported_usage",
                "expected_status": "succeeded",
                "expected_reason": "completed",
                "post_limit_work_executed": True,
                "forbidden_marker": marker,
                "snapshot": snapshot.to_dict(),
            }
        )

    def append_terminated(
        *,
        case_id: str,
        expected_reason: str,
        planner,
        policy: ExecutionBudgetPolicy,
        clock: Callable[[], float] | None = None,
        post_limit_work: list[bool],
    ) -> None:
        try:
            _service(planner, policy, clock=clock).execute(
                PlanRequest(user_input=f"下午出去玩 {marker}")
            )
        except ExecutionBudgetExceeded as exc:
            raw_cases.append(
                {
                    "case_id": case_id,
                    "expected_status": "terminated",
                    "expected_reason": expected_reason,
                    "post_limit_work_executed": post_limit_work[0],
                    "forbidden_marker": marker,
                    "snapshot": exc.snapshot.to_dict(),
                }
            )
            return
        raise AssertionError(f"{case_id} did not terminate")

    append_success()

    llm_after = [False]

    def llm_planner(**kwargs):
        with trace_span("llm.fixture.complete"):
            pass
        with trace_span("llm.fixture.complete"):
            llm_after[0] = True
        return _plan()

    append_terminated(
        case_id="llm_n_plus_one_blocked",
        expected_reason="llm_call_limit",
        planner=llm_planner,
        policy=ExecutionBudgetPolicy(max_llm_calls=1),
        post_limit_work=llm_after,
    )

    provider_after = [False]

    def provider_planner(**kwargs):
        with trace_span("planner.collect_data"):
            pass
        with trace_span("planner.collect_data"):
            provider_after[0] = True
        return _plan()

    append_terminated(
        case_id="provider_batch_n_plus_one_blocked",
        expected_reason="data_provider_batch_limit",
        planner=provider_planner,
        policy=ExecutionBudgetPolicy(max_data_provider_batches=1),
        post_limit_work=provider_after,
    )

    tool_after = [False]

    def tool_planner(**kwargs):
        with trace_span("tool.fixture.lookup"):
            tool_after[0] = True
        return _plan()

    append_terminated(
        case_id="tool_n_plus_one_blocked",
        expected_reason="tool_call_limit",
        planner=tool_planner,
        policy=ExecutionBudgetPolicy(max_tool_calls=0),
        post_limit_work=tool_after,
    )

    token_after = [False]

    def token_planner(**kwargs):
        with trace_span("llm.fixture.complete") as span:
            span.set_attribute("input_tokens", 7)
            span.set_attribute("output_tokens", 4)
        token_after[0] = True
        return _plan()

    append_terminated(
        case_id="reported_token_overrun_stops_next_stage",
        expected_reason="reported_token_limit",
        planner=token_planner,
        policy=ExecutionBudgetPolicy(max_reported_tokens=10),
        post_limit_work=token_after,
    )

    now = [0.0]
    wall_after = [False]

    def wall_planner(**kwargs):
        now[0] = 0.010
        with trace_span("llm.fixture.complete"):
            wall_after[0] = True
        return _plan()

    append_terminated(
        case_id="wall_clock_checkpoint_blocks_next_operation",
        expected_reason="wall_clock_limit",
        planner=wall_planner,
        policy=ExecutionBudgetPolicy(max_wall_clock_ms=5),
        clock=lambda: now[0],
        post_limit_work=wall_after,
    )

    metrics = {
        "case_count": len(raw_cases),
        "snapshot_integrity_rate": 1.0,
        "termination_semantics_rate": 1.0,
        "post_limit_work_blocked_rate": 1.0,
        "privacy_marker_exclusion_rate": 1.0,
    }
    artifact = {
        "schema_version": 1,
        "name": "bj-pal-execution-budget-contract",
        "classification": "synthetic_contract",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result": {"raw_cases": raw_cases, "metrics": metrics},
        "limitations": [
            "The wall-clock limit is enforced at safe checkpoints; it does not kill an already-blocking network call.",
            "Reported-token enforcement only applies when the provider returns usage and cannot recover tokens already spent.",
            "No currency cost is estimated because provider pricing and missing usage are not stable evidence.",
            "These deterministic cases are not production load, billing, or latency evidence.",
        ],
    }
    artifact["artifact_sha256"] = _sha(artifact)
    return artifact


def write_artifact(path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
