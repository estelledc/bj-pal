"""Generate adversarial model-output and bounded-repair evidence."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agents.execution_budget import (
    ExecutionBudgetExceeded,
    ExecutionBudgetPolicy,
    enforce_execution_budget,
)
from agents.llm_client import LLMClient, LLMResponse, MockLLMClient
from agents.model_output_contract import (
    ModelOutputContractError,
    ModelOutputValidationError,
    validate_plan_payload,
)
from agents.planner import plan
from agents.tracing import trace_span
from agents.types import UserPreferences


AREA = "五道营-雍和宫片区"
CANDIDATES = {
    "candidate-1": "候选地点一",
    "candidate-2": "候选地点二",
}
CANDIDATE_CATEGORIES = {
    "candidate-1": ("scenic",),
    "candidate-2": ("food",),
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


def _valid_payload() -> dict[str, Any]:
    return {
        "persona": "family",
        "area_anchor": AREA,
        "steps": [
            {
                "step_index": 1,
                "kind": "citywalk",
                "poi_id": "candidate-1",
                "poi_name": "候选地点一",
                "start_time": "14:00",
                "duration_min": 60,
                "mode_to_here": "walking",
                "rationale": "从候选地点一开始，所有地点都来自本次受控候选池。",
            },
            {
                "step_index": 2,
                "kind": "meal",
                "poi_id": "candidate-2",
                "poi_name": "候选地点二",
                "start_time": "15:00",
                "duration_min": 60,
                "mode_to_here": "walking",
                "rationale": "在候选地点二用餐，保持时间连续并满足结构化契约。",
            },
            {
                "step_index": 3,
                "kind": "depart",
                "poi_id": None,
                "poi_name": "返程",
                "start_time": "16:00",
                "duration_min": 0,
                "mode_to_here": "transit",
                "rationale": "完成活动后返程。",
            },
        ],
        "fallback_strategies": {"queue_overflow": "只切换到候选池内地点"},
        "summary": "候选池内的受控活动方案",
    }


def _mutated(
    case_id: str,
    category: str,
    expected_status: str,
    expected_issue_code: str | None,
    mutator: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    payload = _valid_payload()
    if mutator is not None:
        mutator(payload)
    return {
        "case_id": case_id,
        "category": category,
        "expected_status": expected_status,
        "expected_issue_code": expected_issue_code,
        "expected_persona": "family",
        "expected_area_anchor": AREA,
        "candidate_names_by_id": dict(CANDIDATES),
        "candidate_categories_by_id": dict(CANDIDATE_CATEGORIES),
        "payload": payload,
    }


def _contract_cases() -> list[dict[str, Any]]:
    return [
        _mutated("valid_exact_payload", "valid", "accepted", None),
        _mutated(
            "unknown_top_level_field",
            "schema",
            "rejected",
            "schema_extra_field",
            lambda item: item.update({"unexpected": "PRIVATE-STATIC-MARKER"}),
        ),
        _mutated(
            "duration_wrong_type",
            "schema",
            "rejected",
            "schema_type_invalid",
            lambda item: item["steps"][0].update({"duration_min": "60"}),
        ),
        _mutated(
            "candidate_id_hallucination",
            "grounding",
            "rejected",
            "candidate_id_not_allowed",
            lambda item: item["steps"][0].update(
                {"poi_id": "hallucinated-poi", "poi_name": "虚构地点"}
            ),
        ),
        _mutated(
            "candidate_name_mismatch",
            "grounding",
            "rejected",
            "candidate_name_mismatch",
            lambda item: item["steps"][0].update({"poi_name": "错误名称"}),
        ),
        _mutated(
            "duplicate_candidate",
            "grounding",
            "rejected",
            "duplicate_poi_id",
            lambda item: item["steps"][1].update(
                {
                    "kind": "citywalk",
                    "poi_id": "candidate-1",
                    "poi_name": "候选地点一",
                }
            ),
        ),
        _mutated(
            "food_kind_on_non_food_candidate",
            "grounding",
            "rejected",
            "candidate_category_mismatch",
            lambda item: item["steps"][0].update({"kind": "meal"}),
        ),
        _mutated(
            "missing_depart",
            "sequence",
            "rejected",
            "depart_count_invalid",
            lambda item: item["steps"].pop(),
        ),
        _mutated(
            "depart_not_last",
            "sequence",
            "rejected",
            "depart_not_last",
            lambda item: item["steps"].insert(1, item["steps"].pop()),
        ),
        _mutated(
            "overlapping_steps",
            "sequence",
            "rejected",
            "step_time_overlap",
            lambda item: item["steps"][1].update({"start_time": "14:30"}),
        ),
        _mutated(
            "persona_mismatch",
            "binding",
            "rejected",
            "persona_mismatch",
            lambda item: item.update({"persona": "solo"}),
        ),
        _mutated(
            "area_mismatch",
            "binding",
            "rejected",
            "area_anchor_mismatch",
            lambda item: item.update({"area_anchor": "另一个片区"}),
        ),
        _mutated(
            "locally_recovered_partial_marker",
            "schema",
            "rejected",
            "schema_extra_field",
            lambda item: item.update({"_repaired": "steps_only"}),
        ),
    ]


class _LifecycleClient(LLMClient):
    def __init__(self, *, invalid_first: bool, repair_succeeds: bool) -> None:
        self.invalid_first = invalid_first
        self.repair_succeeds = repair_succeeds
        self.body_count = 0
        self.second_body_executed = False
        self._valid: dict[str, Any] | None = None
        self._invalid: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return "model-output-fixture"

    def complete(
        self,
        system: str,
        user: str,
        json_schema=None,
        temperature: float = 0.3,
        on_token=None,
        on_stream_event=None,
    ) -> LLMResponse:
        del system, json_schema, on_token, on_stream_event
        with trace_span("llm.model_output_fixture.complete", attrs={"temperature": temperature}):
            self.body_count += 1
            if self.body_count == 2:
                self.second_body_executed = True
            if self.body_count == 1:
                response = MockLLMClient()._mock_plan(user)
                self._valid = deepcopy(response.parsed)
                self._invalid = deepcopy(self._valid)
                self._invalid["steps"][0]["poi_id"] = "PRIVATE-HALLUCINATED-POI"
                self._invalid["steps"][0]["poi_name"] = "PRIVATE-HALLUCINATED-NAME"
                return _response(self._invalid if self.invalid_first else self._valid)
            assert self._valid is not None and self._invalid is not None
            return _response(self._valid if self.repair_succeeds else self._invalid)


def _response(payload: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        parsed=deepcopy(payload),
    )


def _prefs() -> UserPreferences:
    return UserPreferences(
        persona="family",
        party_size=3,
        has_child=True,
        child_age=5,
        budget_per_person=120,
        target_start="14:00",
        duration_hours=4.5,
    )


def _run_lifecycle_case(
    *,
    case_id: str,
    client: _LifecycleClient,
    policy: ExecutionBudgetPolicy,
) -> dict[str, Any]:
    try:
        with enforce_execution_budget(policy) as tracker:
            try:
                result = plan(
                    user_input="PRIVATE-USER-MARKER 带娃下午出去玩",
                    persona="family",
                    prefs=_prefs(),
                    area_anchor=AREA,
                    client=client,
                )
            except ModelOutputContractError as exc:
                budget = tracker.complete()
                return {
                    "case_id": case_id,
                    "outcome": "model_output_rejected",
                    "client_body_count": client.body_count,
                    "second_body_executed": client.second_body_executed,
                    "model_output_snapshot": exc.snapshot.to_dict(),
                    "execution_budget": budget.to_dict(),
                }
            budget = tracker.complete()
        return {
            "case_id": case_id,
            "outcome": "plan_returned",
            "client_body_count": client.body_count,
            "second_body_executed": client.second_body_executed,
            "model_output_snapshot": result.model_output_context,
            "execution_budget": budget.to_dict(),
        }
    except ExecutionBudgetExceeded as exc:
        return {
            "case_id": case_id,
            "outcome": "execution_budget_terminated",
            "client_body_count": client.body_count,
            "second_body_executed": client.second_body_executed,
            "model_output_snapshot": None,
            "execution_budget": exc.snapshot.to_dict(),
        }


def _evaluate_contract_case(case: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_plan_payload(
            case["payload"],
            expected_persona=case["expected_persona"],
            expected_area_anchor=case["expected_area_anchor"],
            candidate_names_by_id=case["candidate_names_by_id"],
            candidate_categories_by_id=case["candidate_categories_by_id"],
        )
    except ModelOutputValidationError as exc:
        observed_status = "rejected"
        issue_codes = list(exc.issue_codes)
    else:
        observed_status = "accepted"
        issue_codes = []
    return {
        **case,
        "observed_status": observed_status,
        "observed_issue_codes": issue_codes,
    }


def _rate(values: list[bool]) -> float:
    if not values:
        raise ValueError("model-output metric has no applicable cases")
    return round(sum(values) / len(values), 3)


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
            lifecycle["repair_budget_blocked"]["outcome"]
            == "execution_budget_terminated"
            and lifecycle["repair_budget_blocked"]["client_body_count"] == 1
            and lifecycle["repair_budget_blocked"]["second_body_executed"] is False
        ),
        "privacy_marker_exclusion_rate": _rate(
            [
                "PRIVATE-" not in json.dumps(case, ensure_ascii=False)
                for case in [
                    {
                        "model_output_snapshot": item["model_output_snapshot"],
                        "execution_budget": item["execution_budget"],
                    }
                    for item in lifecycle_cases
                ]
            ]
        ),
    }


def evaluate_model_output() -> dict[str, Any]:
    contract_cases = [_evaluate_contract_case(case) for case in _contract_cases()]
    normal_policy = ExecutionBudgetPolicy(max_tool_calls=64)
    lifecycle_cases = [
        _run_lifecycle_case(
            case_id="first_pass_acceptance",
            client=_LifecycleClient(invalid_first=False, repair_succeeds=True),
            policy=normal_policy,
        ),
        _run_lifecycle_case(
            case_id="bounded_repair_success",
            client=_LifecycleClient(invalid_first=True, repair_succeeds=True),
            policy=normal_policy,
        ),
        _run_lifecycle_case(
            case_id="repair_exhaustion",
            client=_LifecycleClient(invalid_first=True, repair_succeeds=False),
            policy=normal_policy,
        ),
        _run_lifecycle_case(
            case_id="repair_budget_blocked",
            client=_LifecycleClient(invalid_first=True, repair_succeeds=True),
            policy=ExecutionBudgetPolicy(
                max_llm_calls=1,
                max_data_provider_batches=1,
                max_tool_calls=64,
            ),
        ),
    ]
    metrics = _metrics(contract_cases, lifecycle_cases)
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_model_output_contract",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result": {
            "contract_cases": contract_cases,
            "lifecycle_cases": lifecycle_cases,
            "metrics": metrics,
            "limitations": [
                "hand-authored adversarial payloads are not a real-model error distribution",
                "bounded repair uses a deterministic scripted client in the public gate",
                "candidate grounding does not prove provider freshness or real inventory",
                "schema acceptance does not prove plan usefulness or user satisfaction",
                "no live provider token or currency cost is estimated",
            ],
        },
    }
    artifact["artifact_sha256"] = _sha(artifact)
    return artifact


def write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
