"""Generate a recomputable orchestration trade-off artifact.

The comparison deliberately uses the deterministic mock model and local demo
data. It measures implementation-level quality proxies and execution cost; it
does not estimate production latency, currency cost, or real-user preference.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.execution_budget import (
    ExecutionBudgetExceeded,
    ExecutionBudgetPolicy,
    enforce_execution_budget,
)
from agents.llm_client import MockLLMClient
from agents.planner import plan as make_plan
from agents.planner_tot import DEFAULT_BRANCHES, plan_tot, score_plan
from agents.types import Plan, UserPreferences


COMPARISON_POLICY = ExecutionBudgetPolicy(
    max_llm_calls=3,
    max_data_provider_batches=3,
    max_tool_calls=128,
)


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _plan_projection(plan: Plan) -> dict[str, Any]:
    """Keep only deterministic, non-user-authored fields needed for comparison."""
    return {
        "persona": plan.persona,
        "area_anchor": plan.area_anchor,
        "steps": [
            {
                "step_index": step.step_index,
                "kind": step.kind,
                "poi_id": step.poi_id,
                "start_time": step.start_time,
                "duration_min": step.duration_min,
                "mode_to_here": step.mode_to_here,
            }
            for step in plan.steps
        ],
    }


def _mode_result(
    plan: Plan,
    prefs: UserPreferences,
    snapshot,
    *,
    branch_attempt_count: int,
    branch_success_count: int,
    branch_failure_count: int,
) -> dict[str, Any]:
    score, breakdown = score_plan(plan, prefs)
    projection = _plan_projection(plan)
    return {
        "quality_score": round(score, 3),
        "quality_breakdown": breakdown,
        "plan_projection": projection,
        "plan_fingerprint": _sha(projection),
        "branch_attempt_count": branch_attempt_count,
        "branch_success_count": branch_success_count,
        "branch_failure_count": branch_failure_count,
        "execution_budget": snapshot.to_dict(),
    }


def _run_single(case: dict[str, Any]) -> dict[str, Any]:
    with enforce_execution_budget(COMPARISON_POLICY) as tracker:
        plan = make_plan(
            user_input=case["user_input"],
            persona=case["persona"],
            prefs=case["prefs"],
            area_anchor=case["area_anchor"],
            client=MockLLMClient(),
        )
        snapshot = tracker.complete()
    return _mode_result(
        plan,
        case["prefs"],
        snapshot,
        branch_attempt_count=1,
        branch_success_count=1,
        branch_failure_count=0,
    )


def _run_multi(case: dict[str, Any]) -> dict[str, Any]:
    with enforce_execution_budget(COMPARISON_POLICY) as tracker:
        plan, branches = plan_tot(
            user_input=case["user_input"],
            persona=case["persona"],
            prefs=case["prefs"],
            area_anchor=case["area_anchor"],
            client=MockLLMClient(),
            max_workers=3,
        )
        snapshot = tracker.complete()
    successes = sum(branch.plan is not None for branch in branches)
    return _mode_result(
        plan,
        case["prefs"],
        snapshot,
        branch_attempt_count=len(branches),
        branch_success_count=successes,
        branch_failure_count=len(branches) - successes,
    )


def _comparison_cases() -> list[dict[str, Any]]:
    anchor = "五道营-雍和宫片区"
    return [
        {
            "case_id": "family_afternoon",
            "user_input": "带 5 岁孩子下午玩四小时，少走路，人均 120 元",
            "persona": "family",
            "area_anchor": anchor,
            "prefs": UserPreferences(
                persona="family",
                party_size=3,
                has_child=True,
                child_age=5,
                walk_radius_km=1.5,
                budget_per_person=120,
                target_start="14:00",
                duration_hours=4.0,
            ),
        },
        {
            "case_id": "friends_citywalk",
            "user_input": "三个朋友下午 citywalk，想吃饭和拍照，人均 200 元",
            "persona": "friends",
            "area_anchor": anchor,
            "prefs": UserPreferences(
                persona="friends",
                party_size=3,
                walk_radius_km=2.0,
                budget_per_person=200,
                target_start="14:00",
                duration_hours=4.5,
            ),
        },
        {
            "case_id": "solo_short_trip",
            "user_input": "一个人下午逛三小时，想安静一点",
            "persona": "solo",
            "area_anchor": anchor,
            "prefs": UserPreferences(
                persona="solo",
                party_size=1,
                walk_radius_km=2.0,
                budget_per_person=150,
                target_start="14:00",
                duration_hours=3.0,
            ),
        },
    ]


def _run_fault_case(case: dict[str, Any]) -> dict[str, Any]:
    def failing_branch_planner(**kwargs):
        plan = make_plan(**kwargs)
        if "culture / landmark" in kwargs.get("branch_hint", ""):
            raise RuntimeError("injected branch failure after generation")
        return plan

    with enforce_execution_budget(COMPARISON_POLICY) as tracker:
        selected, branches = plan_tot(
            user_input=case["user_input"],
            persona=case["persona"],
            prefs=case["prefs"],
            area_anchor=case["area_anchor"],
            client=MockLLMClient(),
            max_workers=1,
            branch_planner=failing_branch_planner,
        )
        snapshot = tracker.complete()
    failed = [branch.label for branch in branches if branch.plan is None]
    projection = _plan_projection(selected)
    return {
        "case_id": "one_of_three_branch_failure",
        "fault_model": "synthetic post-generation RuntimeError in culture_first",
        "returned_plan": selected is not None,
        "branch_attempt_count": len(branches),
        "branch_success_count": len(branches) - len(failed),
        "branch_failure_count": len(failed),
        "failed_branch_labels": failed,
        "selected_plan_fingerprint": _sha(projection),
        "selected_plan_projection": projection,
        "execution_budget": snapshot.to_dict(),
    }


def _run_default_budget_case(case: dict[str, Any]) -> dict[str, Any]:
    try:
        with enforce_execution_budget(ExecutionBudgetPolicy()):
            plan_tot(
                user_input=case["user_input"],
                persona=case["persona"],
                prefs=case["prefs"],
                area_anchor=case["area_anchor"],
                client=MockLLMClient(),
                max_workers=1,
            )
    except ExecutionBudgetExceeded as exc:
        return {
            "case_id": "default_request_budget_rejects_three_branches",
            "selected_plan_returned": False,
            "expected_reason": "data_provider_batch_limit",
            "execution_budget": exc.snapshot.to_dict(),
        }
    raise AssertionError("default request budget unexpectedly admitted three branches")


def _rate(values: list[bool]) -> float:
    if not values:
        raise ValueError("orchestration metric has no cases")
    return round(sum(values) / len(values), 3)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        raise ValueError("orchestration ratio denominator must be positive")
    return round(numerator / denominator, 3)


def _metrics(
    cases: list[dict[str, Any]],
    fault_case: dict[str, Any],
    budget_case: dict[str, Any],
) -> dict[str, Any]:
    quality_improvements = [
        case["multi"]["quality_score"] > case["single"]["quality_score"]
        for case in cases
    ]
    constraint_non_regressions = []
    output_changes = []
    for case in cases:
        single_breakdown = case["single"]["quality_breakdown"]
        multi_breakdown = case["multi"]["quality_breakdown"]
        constraint_non_regressions.append(
            (
                not single_breakdown["commonsense"]["pass"]
                or multi_breakdown["commonsense"]["pass"]
            )
            and (
                not single_breakdown["hard_constraint"]["pass"]
                or multi_breakdown["hard_constraint"]["pass"]
            )
        )
        output_changes.append(
            case["single"]["plan_fingerprint"]
            != case["multi"]["plan_fingerprint"]
        )

    single_usage = [case["single"]["execution_budget"]["usage"] for case in cases]
    multi_usage = [case["multi"]["execution_budget"]["usage"] for case in cases]
    return {
        "case_count": len(cases),
        "multi_quality_improvement_rate": _rate(quality_improvements),
        "constraint_non_regression_rate": _rate(constraint_non_regressions),
        "semantic_output_change_rate": _rate(output_changes),
        "llm_call_multiplier": _ratio(
            sum(item["llm_call_count"] for item in multi_usage),
            sum(item["llm_call_count"] for item in single_usage),
        ),
        "data_batch_multiplier": _ratio(
            sum(item["data_provider_batch_count"] for item in multi_usage),
            sum(item["data_provider_batch_count"] for item in single_usage),
        ),
        "observed_elapsed_multiplier": _ratio(
            sum(item["elapsed_ms"] for item in multi_usage),
            sum(item["elapsed_ms"] for item in single_usage),
        ),
        "injected_branch_failure_containment_rate": float(
            fault_case["returned_plan"]
            and fault_case["branch_failure_count"] == 1
        ),
        "default_budget_rejection_rate": float(
            not budget_case["selected_plan_returned"]
            and budget_case["execution_budget"]["termination_reason"]
            == budget_case["expected_reason"]
        ),
    }


def evaluate_orchestration() -> dict[str, Any]:
    raw_cases = []
    source_cases = _comparison_cases()
    for source in source_cases:
        single = _run_single(source)
        multi = _run_multi(source)
        raw_cases.append(
            {
                "case_id": source["case_id"],
                "single": single,
                "multi": multi,
                "quality_delta": round(
                    multi["quality_score"] - single["quality_score"], 3
                ),
            }
        )

    fault_case = _run_fault_case(source_cases[0])
    budget_case = _run_default_budget_case(source_cases[0])
    metrics = _metrics(raw_cases, fault_case, budget_case)
    decision = (
        "single_branch_default"
        if metrics["multi_quality_improvement_rate"] == 0.0
        and metrics["llm_call_multiplier"] > 1.0
        and metrics["data_batch_multiplier"] > 1.0
        and metrics["default_budget_rejection_rate"] == 1.0
        else "revisit_with_real_outcomes"
    )
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_orchestration_comparison",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result": {
            "raw_cases": raw_cases,
            "fault_case": fault_case,
            "budget_case": budget_case,
            "metrics": metrics,
            "decision": decision,
            "limitations": [
                "deterministic mock LLM ignores branch hints",
                "local demo SQLite data is synthetic and not live inventory",
                "quality score is a deterministic proxy, not user preference or success probability",
                "elapsed time is an observed local diagnostic, not a production latency claim",
                "no provider price table is available, so currency cost is not estimated",
            ],
        },
    }
    canonical = deepcopy(artifact)
    artifact["artifact_sha256"] = _sha(canonical)
    return artifact


def write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
