"""Independently verify model-output contract and repair evidence.

The static payload checks below deliberately do not import or call the
production model-output validator.  This keeps the public gate capable of
detecting a shared implementation error instead of merely replaying it.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


_TOP_FIELDS = {
    "persona",
    "area_anchor",
    "steps",
    "fallback_strategies",
    "summary",
}
_STEP_FIELDS = {
    "step_index",
    "kind",
    "poi_id",
    "poi_name",
    "start_time",
    "duration_min",
    "mode_to_here",
    "rationale",
}
_PERSONAS = {"family", "friends", "solo", "with_parents"}
_KINDS = {"citywalk", "meal", "culture", "rest", "shopping", "depart", "snack"}
_MODES = {"walking", "bicycling", "driving", "transit"}
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_EXPECTED_CONTRACT_CASES = {
    "valid_exact_payload",
    "unknown_top_level_field",
    "duration_wrong_type",
    "candidate_id_hallucination",
    "candidate_name_mismatch",
    "duplicate_candidate",
    "food_kind_on_non_food_candidate",
    "missing_depart",
    "depart_not_last",
    "overlapping_steps",
    "persona_mismatch",
    "area_mismatch",
    "locally_recovered_partial_marker",
}
_EXPECTED_LIFECYCLE_CASES = {
    "first_pass_acceptance",
    "bounded_repair_success",
    "repair_exhaustion",
    "repair_budget_blocked",
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


def _rate(values: list[bool]) -> float:
    if not values:
        raise ValueError("model-output metric has no applicable cases")
    return round(sum(values) / len(values), 3)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_string(value: Any, *, maximum: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= maximum


def _independent_issue_codes(case: dict[str, Any]) -> list[str]:
    """Recompute the production contract decision from raw fixture fields."""
    payload = case.get("payload")
    if not isinstance(payload, dict):
        return ["unparseable_output"]

    schema_codes: set[str] = set()
    top_keys = set(payload)
    if top_keys - _TOP_FIELDS:
        schema_codes.add("schema_extra_field")
    if _TOP_FIELDS - top_keys:
        schema_codes.add("schema_missing_field")

    persona = payload.get("persona")
    area_anchor = payload.get("area_anchor")
    steps = payload.get("steps")
    fallbacks = payload.get("fallback_strategies")
    summary = payload.get("summary")
    if not isinstance(persona, str) or not isinstance(area_anchor, str) or not isinstance(summary, str):
        schema_codes.add("schema_type_invalid")
    else:
        if persona not in _PERSONAS:
            schema_codes.add("schema_literal_invalid")
        if not 1 <= len(area_anchor) <= 200 or not 1 <= len(summary) <= 1000:
            schema_codes.add("schema_value_invalid")

    if not isinstance(steps, list):
        schema_codes.add("schema_type_invalid")
        steps = []
    elif not 2 <= len(steps) <= 8:
        schema_codes.add("schema_value_invalid")

    if not isinstance(fallbacks, dict):
        schema_codes.add("schema_type_invalid")
    else:
        if len(fallbacks) > 12:
            schema_codes.add("schema_value_invalid")
        for key, value in fallbacks.items():
            if not isinstance(key, str) or not isinstance(value, str):
                schema_codes.add("schema_type_invalid")
            elif not key.strip() or not value.strip():
                schema_codes.add("schema_value_invalid")

    for step in steps:
        if not isinstance(step, dict):
            schema_codes.add("schema_type_invalid")
            continue
        step_keys = set(step)
        if step_keys - _STEP_FIELDS:
            schema_codes.add("schema_extra_field")
        if _STEP_FIELDS - step_keys:
            schema_codes.add("schema_missing_field")
        index = step.get("step_index")
        duration = step.get("duration_min")
        if not _is_int(index) or not _is_int(duration):
            schema_codes.add("schema_type_invalid")
        else:
            if not 1 <= index <= 8 or not 0 <= duration <= 480:
                schema_codes.add("schema_value_invalid")
        if step.get("kind") not in _KINDS or step.get("mode_to_here") not in _MODES:
            schema_codes.add("schema_literal_invalid")
        poi_id = step.get("poi_id")
        if poi_id is not None and not isinstance(poi_id, str):
            schema_codes.add("schema_type_invalid")
        if not _is_nonempty_string(step.get("poi_name"), maximum=200):
            schema_codes.add(
                "schema_type_invalid"
                if not isinstance(step.get("poi_name"), str)
                else "schema_value_invalid"
            )
        start_time = step.get("start_time")
        if not isinstance(start_time, str):
            schema_codes.add("schema_type_invalid")
        elif not _TIME_PATTERN.fullmatch(start_time):
            schema_codes.add("schema_value_invalid")
        if not _is_nonempty_string(step.get("rationale"), maximum=1000):
            schema_codes.add(
                "schema_type_invalid"
                if not isinstance(step.get("rationale"), str)
                else "schema_value_invalid"
            )

    if schema_codes:
        return sorted(schema_codes)

    issue_codes: set[str] = set()
    if persona != case.get("expected_persona"):
        issue_codes.add("persona_mismatch")
    if area_anchor != case.get("expected_area_anchor"):
        issue_codes.add("area_anchor_mismatch")
    expected_indices = list(range(1, len(steps) + 1))
    if [step["step_index"] for step in steps] != expected_indices:
        issue_codes.add("step_index_sequence_invalid")

    depart_indices = [index for index, step in enumerate(steps) if step["kind"] == "depart"]
    if len(depart_indices) != 1:
        issue_codes.add("depart_count_invalid")
    elif depart_indices[0] != len(steps) - 1:
        issue_codes.add("depart_not_last")

    candidate_names = case.get("candidate_names_by_id")
    if not isinstance(candidate_names, dict) or not candidate_names:
        raise ValueError("contract case candidate map must be non-empty")
    candidate_categories = case.get("candidate_categories_by_id")
    if (
        not isinstance(candidate_categories, dict)
        or set(candidate_categories) != set(candidate_names)
        or any(
            not isinstance(categories, list)
            or not categories
            or any(not isinstance(category, str) or not category for category in categories)
            for categories in candidate_categories.values()
        )
    ):
        raise ValueError("contract case candidate category map is invalid")
    seen: set[str] = set()
    previous_end: int | None = None
    for step in steps:
        hour, minute = step["start_time"].split(":", 1)
        start = int(hour) * 60 + int(minute)
        if previous_end is not None and start < previous_end:
            issue_codes.add("step_time_overlap")
        previous_end = start + step["duration_min"]
        if step["kind"] == "depart":
            if step["poi_id"] is not None:
                issue_codes.add("depart_has_poi_id")
            if step["duration_min"] != 0:
                issue_codes.add("depart_duration_invalid")
            continue
        poi_id = step["poi_id"]
        if poi_id is None:
            issue_codes.add("non_depart_missing_poi_id")
            continue
        if poi_id in seen:
            issue_codes.add("duplicate_poi_id")
        seen.add(poi_id)
        expected_name = candidate_names.get(poi_id)
        if expected_name is None:
            issue_codes.add("candidate_id_not_allowed")
        elif step["poi_name"] != expected_name:
            issue_codes.add("candidate_name_mismatch")
        if (
            expected_name is not None
            and step["kind"] in {"meal", "snack"}
            and "food" not in candidate_categories[poi_id]
        ):
            issue_codes.add("candidate_category_mismatch")
    return sorted(issue_codes)


def _verify_contract_cases(cases: list[dict[str, Any]]) -> None:
    case_ids = {case.get("case_id") for case in cases}
    if len(cases) != len(_EXPECTED_CONTRACT_CASES) or case_ids != _EXPECTED_CONTRACT_CASES:
        raise ValueError("model-output contract fixture set is incomplete")
    for case in cases:
        issue_codes = _independent_issue_codes(case)
        status = "rejected" if issue_codes else "accepted"
        if case.get("observed_status") != status:
            raise ValueError(f"independent status mismatch for {case.get('case_id')}")
        if case.get("observed_issue_codes") != issue_codes:
            raise ValueError(f"independent issue-code mismatch for {case.get('case_id')}")
        if case.get("expected_status") != status:
            raise ValueError(f"fixture expectation mismatch for {case.get('case_id')}")
        expected_issue = case.get("expected_issue_code")
        if expected_issue is not None and expected_issue not in issue_codes:
            raise ValueError(f"expected issue was not independently detected for {case.get('case_id')}")


def _verify_execution_budget(snapshot: dict[str, Any]) -> None:
    canonical = deepcopy(snapshot)
    observed = canonical.pop("artifact_sha256", None)
    if snapshot.get("version") != "execution_budget_v1" or observed != _sha(canonical):
        raise ValueError("execution-budget snapshot integrity mismatch")
    policy = snapshot.get("policy")
    usage = snapshot.get("usage")
    if not isinstance(policy, dict) or not isinstance(usage, dict):
        raise ValueError("execution-budget snapshot is incomplete")
    for name in (
        "max_llm_calls",
        "max_data_provider_batches",
        "max_tool_calls",
        "max_transport_attempts_per_llm_call",
        "max_reported_tokens",
        "max_wall_clock_ms",
    ):
        if not _is_int(policy.get(name)) or policy[name] < 0:
            raise ValueError("execution-budget policy is invalid")
    for name in (
        "llm_call_count",
        "data_provider_batch_count",
        "tool_call_count",
        "reported_token_call_count",
    ):
        if not _is_int(usage.get(name)) or usage[name] < 0:
            raise ValueError("execution-budget usage is invalid")
    elapsed = usage.get("elapsed_ms")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
        raise ValueError("execution-budget elapsed time is invalid")
    status = snapshot.get("status")
    reason = snapshot.get("termination_reason")
    if (status == "succeeded") != (reason == "completed"):
        raise ValueError("execution-budget status semantics mismatch")
    if status not in {"succeeded", "terminated"}:
        raise ValueError("execution-budget status is invalid")


def _verify_model_snapshot(snapshot: dict[str, Any], *, expected_status: str) -> None:
    canonical = deepcopy(snapshot)
    observed = canonical.pop("artifact_sha256", None)
    if snapshot.get("version") != "model_output_contract_v1" or observed != _sha(canonical):
        raise ValueError("model-output snapshot integrity mismatch")
    if snapshot.get("status") != expected_status:
        raise ValueError("model-output snapshot status mismatch")
    attempts = snapshot.get("attempt_count")
    repaired = snapshot.get("repair_attempted")
    issues = snapshot.get("issue_codes")
    candidates = snapshot.get("candidate_count")
    if not _is_int(candidates) or candidates < 1 or not isinstance(issues, list):
        raise ValueError("model-output snapshot fields are invalid")
    expected_attempts = 1 if expected_status == "accepted" else 2
    if attempts != expected_attempts or repaired is not (expected_attempts == 2):
        raise ValueError("model-output snapshot attempt semantics mismatch")
    if expected_status == "accepted" and issues:
        raise ValueError("initial acceptance cannot contain issue codes")
    if expected_status != "accepted" and not issues:
        raise ValueError("repair/rejection snapshot must retain issue codes")
    if issues != sorted(set(issues)) or not all(isinstance(item, str) and item for item in issues):
        raise ValueError("model-output issue codes are not canonical")


def _verify_lifecycle_cases(cases: list[dict[str, Any]]) -> None:
    by_id = {case.get("case_id"): case for case in cases}
    if len(cases) != len(_EXPECTED_LIFECYCLE_CASES) or set(by_id) != _EXPECTED_LIFECYCLE_CASES:
        raise ValueError("model-output lifecycle fixture set is incomplete")
    for case in cases:
        _verify_execution_budget(case.get("execution_budget") or {})

    first = by_id["first_pass_acceptance"]
    if (
        first.get("outcome") != "plan_returned"
        or first.get("client_body_count") != 1
        or first.get("second_body_executed") is not False
    ):
        raise ValueError("first-pass lifecycle accounting mismatch")
    _verify_model_snapshot(first.get("model_output_snapshot") or {}, expected_status="accepted")
    first_usage = first["execution_budget"]["usage"]
    if first_usage.get("llm_call_count") != 1 or first_usage.get("data_provider_batch_count") != 1:
        raise ValueError("first-pass work accounting mismatch")

    repaired = by_id["bounded_repair_success"]
    if (
        repaired.get("outcome") != "plan_returned"
        or repaired.get("client_body_count") != 2
        or repaired.get("second_body_executed") is not True
    ):
        raise ValueError("bounded-repair lifecycle accounting mismatch")
    _verify_model_snapshot(
        repaired.get("model_output_snapshot") or {},
        expected_status="accepted_after_repair",
    )
    if repaired["model_output_snapshot"].get("issue_codes") != [
        "candidate_id_not_allowed"
    ]:
        raise ValueError("bounded-repair issue evidence mismatch")
    repaired_usage = repaired["execution_budget"]["usage"]
    if repaired_usage.get("llm_call_count") != 2 or repaired_usage.get("data_provider_batch_count") != 1:
        raise ValueError("bounded-repair work accounting mismatch")

    exhausted = by_id["repair_exhaustion"]
    if (
        exhausted.get("outcome") != "model_output_rejected"
        or exhausted.get("client_body_count") != 2
        or exhausted.get("second_body_executed") is not True
    ):
        raise ValueError("repair-exhaustion lifecycle accounting mismatch")
    _verify_model_snapshot(exhausted.get("model_output_snapshot") or {}, expected_status="rejected")
    if exhausted["model_output_snapshot"].get("issue_codes") != [
        "candidate_id_not_allowed"
    ]:
        raise ValueError("repair-exhaustion issue evidence mismatch")
    exhausted_usage = exhausted["execution_budget"]["usage"]
    if exhausted_usage.get("llm_call_count") != 2 or exhausted_usage.get("data_provider_batch_count") != 1:
        raise ValueError("repair-exhaustion work accounting mismatch")

    blocked = by_id["repair_budget_blocked"]
    blocked_snapshot = blocked["execution_budget"]
    blocked_usage = blocked_snapshot["usage"]
    if (
        blocked.get("outcome") != "execution_budget_terminated"
        or blocked.get("client_body_count") != 1
        or blocked.get("second_body_executed") is not False
        or blocked.get("model_output_snapshot") is not None
        or blocked_snapshot.get("status") != "terminated"
        or blocked_snapshot.get("termination_reason") != "llm_call_limit"
        or blocked_snapshot["policy"].get("max_llm_calls") != 1
        or blocked_usage.get("llm_call_count") != 2
        or blocked_usage.get("data_provider_batch_count") != 1
    ):
        raise ValueError("repair-budget lifecycle accounting mismatch")

    safe_snapshots = [
        {
            "model_output_snapshot": case.get("model_output_snapshot"),
            "execution_budget": case.get("execution_budget"),
        }
        for case in cases
    ]
    if "PRIVATE-" in json.dumps(safe_snapshots, ensure_ascii=False):
        raise ValueError("privacy marker leaked into lifecycle snapshots")


def _metrics(
    contract_cases: list[dict[str, Any]],
    lifecycle_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    by_category: dict[str, list[dict[str, Any]]] = {}
    for case in contract_cases:
        by_category.setdefault(case["category"], []).append(case)
    lifecycle = {case["case_id"]: case for case in lifecycle_cases}
    return {
        "contract_case_count": len(contract_cases),
        "decision_accuracy_rate": _rate(
            [case["observed_status"] == case["expected_status"] for case in contract_cases]
        ),
        "expected_issue_detection_rate": _rate(
            [
                case["expected_issue_code"] in case["observed_issue_codes"]
                for case in contract_cases
                if case["expected_issue_code"] is not None
            ]
        ),
        "valid_false_rejection_rate": round(
            1.0
            - _rate(
                [case["observed_status"] == "accepted" for case in by_category["valid"]]
            ),
            3,
        ),
        "schema_rejection_rate": _rate(
            [case["observed_status"] == "rejected" for case in by_category["schema"]]
        ),
        "grounding_rejection_rate": _rate(
            [case["observed_status"] == "rejected" for case in by_category["grounding"]]
        ),
        "sequence_rejection_rate": _rate(
            [case["observed_status"] == "rejected" for case in by_category["sequence"]]
        ),
        "first_pass_single_call_rate": float(
            lifecycle["first_pass_acceptance"]["outcome"] == "plan_returned"
            and lifecycle["first_pass_acceptance"]["client_body_count"] == 1
        ),
        "bounded_repair_success_rate": float(
            lifecycle["bounded_repair_success"]["outcome"] == "plan_returned"
            and lifecycle["bounded_repair_success"]["client_body_count"] == 2
        ),
        "repair_exhaustion_fail_closed_rate": float(
            lifecycle["repair_exhaustion"]["outcome"] == "model_output_rejected"
            and lifecycle["repair_exhaustion"]["client_body_count"] == 2
        ),
        "repair_budget_enforcement_rate": float(
            lifecycle["repair_budget_blocked"]["outcome"] == "execution_budget_terminated"
            and lifecycle["repair_budget_blocked"]["client_body_count"] == 1
            and lifecycle["repair_budget_blocked"]["second_body_executed"] is False
        ),
        "privacy_marker_exclusion_rate": _rate(
            [
                "PRIVATE-"
                not in json.dumps(
                    {
                        "model_output_snapshot": case["model_output_snapshot"],
                        "execution_budget": case["execution_budget"],
                    },
                    ensure_ascii=False,
                )
                for case in lifecycle_cases
            ]
        ),
    }


def verify_model_output_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    canonical = deepcopy(artifact)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _sha(canonical):
        raise ValueError("model-output artifact SHA-256 mismatch")
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported model-output artifact schema")
    if artifact.get("classification") != "synthetic_model_output_contract":
        raise ValueError("unexpected model-output artifact classification")

    result = artifact.get("result") or {}
    contract_cases = result.get("contract_cases") or []
    lifecycle_cases = result.get("lifecycle_cases") or []
    _verify_contract_cases(contract_cases)
    _verify_lifecycle_cases(lifecycle_cases)
    recomputed = _metrics(contract_cases, lifecycle_cases)
    if result.get("metrics") != recomputed:
        raise ValueError("model-output metrics do not match raw evidence")
    limitations = result.get("limitations")
    if not isinstance(limitations, list) or len(limitations) < 4:
        raise ValueError("model-output limitations are incomplete")
    return artifact
