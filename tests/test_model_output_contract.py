from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.execution_budget import (  # noqa: E402
    ExecutionBudgetExceeded,
    ExecutionBudgetPolicy,
    enforce_execution_budget,
)
from agents.llm_client import LLMClient, LLMResponse, MockLLMClient  # noqa: E402
from agents.model_output_contract import (  # noqa: E402
    ModelOutputContractError,
    ModelOutputContractSnapshot,
    ModelOutputValidationError,
    validate_plan_payload,
)
from agents.planner import (  # noqa: E402
    MODEL_OUTPUT_REPAIR_SYSTEM,
    PLANNER_SYSTEM,
    parse_plan_response_text,
    plan,
)
from agents.tracing import trace_span  # noqa: E402
from agents.types import Plan, UserPreferences  # noqa: E402


AREA = "五道营-雍和宫片区"


def test_planner_and_repair_prompts_use_unambiguous_enum_and_depart_rules() -> None:
    for prompt in (PLANNER_SYSTEM, MODEL_OUTPUT_REPAIR_SYSTEM):
        assert '"citywalk|meal|' not in prompt
        assert "kind 只能是 citywalk、meal、culture、rest、shopping、snack、depart 之一" in prompt
        assert 'poi_id=null、poi_name="返程"、duration_min=0、mode_to_here="transit"' in prompt
        assert "不要把竖线连接的枚举说明" in prompt


def _valid_payload() -> dict:
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
                "rationale": "先从候选地点一开始，在受控候选范围内建立下午活动节奏。",
            },
            {
                "step_index": 2,
                "kind": "meal",
                "poi_id": "candidate-2",
                "poi_name": "候选地点二",
                "start_time": "15:00",
                "duration_min": 60,
                "mode_to_here": "walking",
                "rationale": "随后前往候选地点二用餐，保持时间连续且地点来自候选池。",
            },
            {
                "step_index": 3,
                "kind": "depart",
                "poi_id": None,
                "poi_name": "返程",
                "start_time": "16:00",
                "duration_min": 0,
                "mode_to_here": "transit",
                "rationale": "按计划结束并返程。",
            },
        ],
        "fallback_strategies": {"queue_overflow": "切换到候选池内的同类地点"},
        "summary": "候选池内的三步受控方案",
    }


def _validate(payload: dict) -> dict:
    return validate_plan_payload(
        payload,
        expected_persona="family",
        expected_area_anchor=AREA,
        candidate_names_by_id={
            "candidate-1": "候选地点一",
            "candidate-2": "候选地点二",
        },
        candidate_categories_by_id={
            "candidate-1": ("scenic",),
            "candidate-2": ("food",),
        },
    )


class _ScriptedPlannerClient(LLMClient):
    def __init__(self, *, first_mutation: str, repair_succeeds: bool) -> None:
        self.first_mutation = first_mutation
        self.repair_succeeds = repair_succeeds
        self.body_count = 0
        self.second_body_executed = False
        self._valid: dict | None = None
        self._invalid: dict | None = None

    @property
    def name(self) -> str:
        return "scripted"

    def complete(
        self,
        system: str,
        user: str,
        json_schema=None,
        temperature: float = 0.3,
        on_token=None,
        on_stream_event=None,
    ) -> LLMResponse:
        with trace_span("llm.scripted.complete", attrs={"temperature": temperature}):
            self.body_count += 1
            if self.body_count == 2:
                self.second_body_executed = True
            if self.body_count == 1:
                response = MockLLMClient()._mock_plan(user)
                self._valid = deepcopy(response.parsed)
                self._invalid = deepcopy(self._valid)
                if self.first_mutation == "none":
                    return _response(self._valid)
                if self.first_mutation == "hallucinated_id":
                    self._invalid["steps"][0]["poi_id"] = "made-up-secret-poi"
                    self._invalid["steps"][0]["poi_name"] = "不存在的秘密地点"
                    return _response(self._invalid)
                if self.first_mutation == "name_mismatch":
                    self._invalid["steps"][0]["poi_name"] = "错误名称"
                    return _response(self._invalid)
                if self.first_mutation == "truncated":
                    text = json.dumps(self._invalid, ensure_ascii=False)
                    return LLMResponse(text=text[: max(1, len(text) // 2)], parsed=None)
                raise AssertionError(f"unsupported mutation {self.first_mutation}")

            assert self._valid is not None and self._invalid is not None
            return _response(self._valid if self.repair_succeeds else self._invalid)


def _response(payload: dict) -> LLMResponse:
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


def test_strict_contract_accepts_exact_candidate_bound_payload() -> None:
    normalized = _validate(_valid_payload())
    assert normalized == _valid_payload()


def test_strict_contract_rejects_meal_bound_to_non_food_candidate() -> None:
    payload = _valid_payload()
    payload["steps"][0]["kind"] = "meal"

    with pytest.raises(ModelOutputValidationError) as raised:
        _validate(payload)

    assert "candidate_category_mismatch" in raised.value.issue_codes


def test_supplied_preferences_are_canonical_persona_for_model_contract() -> None:
    prefs = _prefs()
    prefs.persona = "friends"
    client = _ScriptedPlannerClient(first_mutation="none", repair_succeeds=True)

    result = plan(
        user_input="周六下午跟朋友出去玩",
        prefs=prefs,
        client=client,
        area_anchor=AREA,
    )

    assert result.persona == "friends"
    assert result.model_output_context is not None
    assert result.model_output_context["status"] == "accepted"
    assert client.body_count == 1


@pytest.mark.parametrize(
    ("mutator", "expected_code"),
    [
        (lambda payload: payload.update({"unknown": True}), "schema_extra_field"),
        (
            lambda payload: payload["steps"][0].update({"duration_min": "60"}),
            "schema_type_invalid",
        ),
        (
            lambda payload: payload["steps"][0].update({"poi_id": "not-candidate"}),
            "candidate_id_not_allowed",
        ),
        (
            lambda payload: payload["steps"][0].update({"poi_name": "错误名称"}),
            "candidate_name_mismatch",
        ),
        (
            lambda payload: payload["steps"][1].update({"poi_id": "candidate-1"}),
            "duplicate_poi_id",
        ),
        (
            lambda payload: payload["steps"][1].update({"start_time": "14:30"}),
            "step_time_overlap",
        ),
    ],
)
def test_strict_contract_rejects_schema_grounding_and_sequence_violations(
    mutator,
    expected_code: str,
) -> None:
    payload = _valid_payload()
    mutator(payload)
    with pytest.raises(ModelOutputValidationError) as raised:
        _validate(payload)
    assert expected_code in raised.value.issue_codes


def test_locally_recovered_truncated_json_is_not_silently_accepted() -> None:
    raw = json.dumps(_valid_payload(), ensure_ascii=False)
    parsed = parse_plan_response_text(raw[: len(raw) // 2])
    assert parsed is not None and parsed.get("_repaired") is not None
    with pytest.raises(ModelOutputValidationError) as raised:
        _validate(parsed)
    assert "schema_extra_field" in raised.value.issue_codes


@pytest.mark.parametrize("mutation", ["hallucinated_id", "name_mismatch", "truncated"])
def test_planner_repairs_one_invalid_output_and_records_evidence(mutation: str) -> None:
    client = _ScriptedPlannerClient(first_mutation=mutation, repair_succeeds=True)
    result = plan(
        user_input="带娃下午出去玩",
        persona="family",
        prefs=_prefs(),
        area_anchor=AREA,
        client=client,
    )

    evidence = result.model_output_context
    assert client.body_count == 2
    assert evidence is not None
    assert evidence["status"] == "accepted_after_repair"
    assert evidence["attempt_count"] == 2
    assert evidence["repair_attempted"] is True
    assert evidence["issue_codes"]
    assert all(step.poi_id != "made-up-secret-poi" for step in result.steps)
    restored = Plan.from_dict(result.to_dict())
    assert restored.model_output_context == evidence


def test_second_invalid_output_fails_closed_with_privacy_minimized_snapshot() -> None:
    client = _ScriptedPlannerClient(
        first_mutation="hallucinated_id",
        repair_succeeds=False,
    )
    with pytest.raises(ModelOutputContractError) as raised:
        plan(
            user_input="包含 PRIVATE-USER-MARKER 的带娃请求",
            persona="family",
            prefs=_prefs(),
            area_anchor=AREA,
            client=client,
        )

    details = raised.value.safe_details()
    encoded = json.dumps(details, ensure_ascii=False)
    assert details["status"] == "rejected"
    assert details["attempt_count"] == 2
    assert details["repair_attempted"] is True
    assert "candidate_id_not_allowed" in details["issue_codes"]
    assert raised.value.snapshot.verify_integrity() is True
    assert "PRIVATE-USER-MARKER" not in encoded
    assert "made-up-secret-poi" not in encoded
    assert "不存在的秘密地点" not in encoded


def test_repair_call_cannot_bypass_request_llm_budget() -> None:
    client = _ScriptedPlannerClient(
        first_mutation="hallucinated_id",
        repair_succeeds=True,
    )
    policy = ExecutionBudgetPolicy(
        max_llm_calls=1,
        max_data_provider_batches=1,
        max_tool_calls=64,
    )
    with pytest.raises(ExecutionBudgetExceeded) as raised:
        with enforce_execution_budget(policy):
            plan(
                user_input="带娃下午出去玩",
                persona="family",
                prefs=_prefs(),
                area_anchor=AREA,
                client=client,
            )

    assert raised.value.snapshot.termination_reason == "llm_call_limit"
    assert raised.value.snapshot.usage.llm_call_count == 2
    assert client.body_count == 1
    assert client.second_body_executed is False


@pytest.mark.parametrize(
    ("status", "attempt_count", "issue_codes"),
    [
        ("accepted", 2, ()),
        ("accepted_after_repair", 1, ("schema_extra_field",)),
        ("rejected", 1, ("candidate_id_not_allowed",)),
    ],
)
def test_model_output_snapshot_rejects_inconsistent_status_semantics(
    status: str,
    attempt_count: int,
    issue_codes: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        ModelOutputContractSnapshot.create(
            status=status,
            attempt_count=attempt_count,
            repair_attempted=attempt_count == 2,
            candidate_count=2,
            issue_codes=issue_codes,
        )


@pytest.mark.parametrize(
    ("attempt_count", "repair_attempted", "candidate_count", "issue_codes"),
    [
        (True, False, 2, ()),
        (1, 0, 2, ()),
        (1, False, True, ()),
        (1, False, 2, ("",)),
    ],
)
def test_model_output_snapshot_rejects_non_strict_evidence_types(
    attempt_count,
    repair_attempted,
    candidate_count,
    issue_codes,
) -> None:
    with pytest.raises(ValueError):
        ModelOutputContractSnapshot.create(
            status="accepted",
            attempt_count=attempt_count,
            repair_attempted=repair_attempted,
            candidate_count=candidate_count,
            issue_codes=issue_codes,
        )
