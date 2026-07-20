"""Independently verify the orchestration comparison artifact."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _rate(values: list[bool]) -> float:
    if not values:
        raise ValueError("orchestration metric has no cases")
    return round(sum(values) / len(values), 3)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        raise ValueError("orchestration ratio denominator must be positive")
    return round(numerator / denominator, 3)


def _verify_snapshot(snapshot: dict[str, Any]) -> None:
    canonical = deepcopy(snapshot)
    observed = canonical.pop("artifact_sha256", None)
    if snapshot.get("version") != "execution_budget_v1" or observed != _sha(canonical):
        raise ValueError("execution-budget snapshot integrity mismatch")


def _verify_mode(mode: dict[str, Any], *, expected_branches: int) -> None:
    snapshot = mode.get("execution_budget") or {}
    _verify_snapshot(snapshot)
    if snapshot.get("status") != "succeeded" or snapshot.get("termination_reason") != "completed":
        raise ValueError("comparison mode did not complete within its explicit policy")
    usage = snapshot.get("usage") or {}
    attempts = mode.get("branch_attempt_count")
    successes = mode.get("branch_success_count")
    failures = mode.get("branch_failure_count")
    if attempts != expected_branches or successes + failures != attempts:
        raise ValueError("branch accounting mismatch")
    if failures != 0 or successes != expected_branches:
        raise ValueError("normal comparison case contains an unexpected branch failure")
    if usage.get("llm_call_count") != expected_branches:
        raise ValueError("LLM call count does not match executed branches")
    if usage.get("data_provider_batch_count") != expected_branches:
        raise ValueError("data batch count does not match executed branches")
    projection = mode.get("plan_projection")
    if not isinstance(projection, dict) or mode.get("plan_fingerprint") != _sha(projection):
        raise ValueError("plan fingerprint mismatch")
    breakdown = mode.get("quality_breakdown") or {}
    if mode.get("quality_score") != breakdown.get("total"):
        raise ValueError("quality score does not match raw breakdown")
    for gate in ("commonsense", "hard_constraint"):
        if not isinstance((breakdown.get(gate) or {}).get("pass"), bool):
            raise ValueError(f"quality breakdown missing boolean {gate} result")


def _verify_fault_case(case: dict[str, Any]) -> None:
    snapshot = case.get("execution_budget") or {}
    _verify_snapshot(snapshot)
    usage = snapshot.get("usage") or {}
    if snapshot.get("status") != "succeeded" or snapshot.get("termination_reason") != "completed":
        raise ValueError("fault-injection case did not complete")
    if (
        case.get("branch_attempt_count") != 3
        or case.get("branch_success_count") != 2
        or case.get("branch_failure_count") != 1
        or case.get("failed_branch_labels") != ["culture_first"]
        or case.get("returned_plan") is not True
    ):
        raise ValueError("fault-injection branch accounting mismatch")
    if usage.get("llm_call_count") != 3 or usage.get("data_provider_batch_count") != 3:
        raise ValueError("fault-injection work accounting mismatch")
    projection = case.get("selected_plan_projection")
    if case.get("selected_plan_fingerprint") != _sha(projection):
        raise ValueError("fault-injection selected plan fingerprint mismatch")


def _verify_budget_case(case: dict[str, Any]) -> None:
    snapshot = case.get("execution_budget") or {}
    _verify_snapshot(snapshot)
    policy = snapshot.get("policy") or {}
    usage = snapshot.get("usage") or {}
    if case.get("selected_plan_returned") is not False:
        raise ValueError("default budget case unexpectedly returned a plan")
    if (
        case.get("expected_reason") != "data_provider_batch_limit"
        or snapshot.get("status") != "terminated"
        or snapshot.get("termination_reason") != case.get("expected_reason")
        or usage.get("data_provider_batch_count")
        != policy.get("max_data_provider_batches", -1) + 1
    ):
        raise ValueError("default budget rejection semantics mismatch")


def _recompute_metrics(
    cases: list[dict[str, Any]],
    fault_case: dict[str, Any],
    budget_case: dict[str, Any],
) -> dict[str, Any]:
    quality_improvements = []
    constraint_non_regressions = []
    output_changes = []
    for case in cases:
        single = case["single"]
        multi = case["multi"]
        quality_improvements.append(multi["quality_score"] > single["quality_score"])
        single_breakdown = single["quality_breakdown"]
        multi_breakdown = multi["quality_breakdown"]
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
        output_changes.append(single["plan_fingerprint"] != multi["plan_fingerprint"])
        expected_delta = round(multi["quality_score"] - single["quality_score"], 3)
        if case.get("quality_delta") != expected_delta:
            raise ValueError("quality delta does not match raw mode scores")

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


def verify_orchestration_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    canonical = deepcopy(artifact)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _sha(canonical):
        raise ValueError("orchestration artifact SHA-256 mismatch")
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported orchestration artifact schema")
    if artifact.get("classification") != "synthetic_orchestration_comparison":
        raise ValueError("unexpected orchestration artifact classification")

    result = artifact.get("result") or {}
    cases = result.get("raw_cases") or []
    if len(cases) != 3 or len({case.get("case_id") for case in cases}) != 3:
        raise ValueError("orchestration cases must contain three unique fixtures")
    for case in cases:
        _verify_mode(case.get("single") or {}, expected_branches=1)
        _verify_mode(case.get("multi") or {}, expected_branches=3)

    fault_case = result.get("fault_case") or {}
    budget_case = result.get("budget_case") or {}
    _verify_fault_case(fault_case)
    _verify_budget_case(budget_case)
    metrics = _recompute_metrics(cases, fault_case, budget_case)
    if result.get("metrics") != metrics:
        raise ValueError("orchestration metrics do not match raw cases")
    expected_decision = (
        "single_branch_default"
        if metrics["multi_quality_improvement_rate"] == 0.0
        and metrics["llm_call_multiplier"] > 1.0
        and metrics["data_batch_multiplier"] > 1.0
        and metrics["default_budget_rejection_rate"] == 1.0
        else "revisit_with_real_outcomes"
    )
    if result.get("decision") != expected_decision:
        raise ValueError("orchestration decision does not follow raw evidence")
    limitations = result.get("limitations")
    if not isinstance(limitations, list) or len(limitations) < 4:
        raise ValueError("orchestration limitations are incomplete")
    return artifact
