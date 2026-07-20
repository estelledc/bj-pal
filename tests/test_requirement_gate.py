"""Deterministic acceptance tests for the application-level requirement gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from application import (  # noqa: E402
    PlanRequest,
    PlanningClarificationRequired,
    PlanningService,
    RequirementNormalizer,
)
from agents.types import Plan  # noqa: E402
from data_profile import DataProfile  # noqa: E402


def _profile() -> DataProfile:
    return DataProfile(
        name="demo",
        classification="synthetic",
        public_reproducible=True,
        sources={},
        counts={},
        limitations=("fixture",),
    )


def _service(captured: list[dict]) -> PlanningService:
    def planner(**kwargs):
        captured.append(kwargs)
        return Plan(
            persona=kwargs["persona"],
            area_anchor=kwargs["area_anchor"],
            steps=[],
            plan_id="plan-requirements",
        )

    return PlanningService(
        planner=planner,
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
    )


def test_implicit_supported_area_is_normalized_before_planning() -> None:
    decision = RequirementNormalizer().normalize(
        PlanRequest(user_input="周六下午去三里屯逛逛")
    )

    assert decision.status == "proceed"
    assert decision.resolved_area_anchor == "三里屯片区"
    assert decision.questions == ()
    assert decision.signals[0].code == "area_from_text"

    captured: list[dict] = []
    result = _service(captured).execute(
        PlanRequest(user_input="周六下午去三里屯逛逛")
    )
    assert captured[0]["area_anchor"] == "三里屯片区"
    assert result.request.area_anchor == "三里屯片区"
    assert result.requirements.resolved_area_anchor == "三里屯片区"


def test_unresolved_context_reference_fails_before_planner_execution() -> None:
    captured: list[dict] = []
    service = _service(captured)

    with pytest.raises(PlanningClarificationRequired) as raised:
        service.execute(PlanRequest(user_input="还是上次那个地方，下午安排一下"))

    decision = raised.value.decision
    assert decision.status == "clarification_required"
    assert decision.unresolved[0].code == "unresolved_location_reference"
    assert decision.questions[0].field == "area_anchor"
    assert 2 <= len(decision.questions[0].options) <= 3
    assert captured == []


def test_relative_location_is_resolved_by_an_explicit_area_without_extra_friction() -> None:
    request = PlanRequest(
        user_input="别离家太远，安排四小时",
        area_anchor="望京片区",
        provided_fields=frozenset({"user_input", "area_anchor"}),
    )

    decision = RequirementNormalizer().normalize(request)

    assert decision.status == "proceed"
    assert decision.resolved_area_anchor == "望京片区"
    assert decision.questions == ()


def test_implicit_default_is_exposed_as_an_assumption_not_a_clarification() -> None:
    decision = RequirementNormalizer().normalize(
        PlanRequest(user_input="周六下午出去玩四小时")
    )

    assert decision.status == "proceed_with_assumptions"
    assert decision.assumptions[0].code == "default_area_anchor"
    assert decision.assumptions[0].value == "五道营-雍和宫片区"
    assert decision.questions == ()


def test_conflicting_text_and_explicit_area_requires_one_bounded_question() -> None:
    request = PlanRequest(
        user_input="周六下午去三里屯逛逛",
        area_anchor="五道营-雍和宫片区",
        provided_fields=frozenset({"user_input", "area_anchor"}),
    )

    decision = RequirementNormalizer().normalize(request)

    assert decision.status == "clarification_required"
    assert decision.unresolved[0].code == "conflicting_area_anchor"
    assert decision.questions[0].options[:2] == (
        "使用三里屯片区",
        "使用五道营-雍和宫片区",
    )


def test_non_referential_phrases_do_not_trigger_false_clarification() -> None:
    normalizer = RequirementNormalizer()
    cases = (
        "第一次去故宫，想轻松逛逛",
        "之前没去过三里屯，周末想去看看",
        "第二个周六想出去玩四小时",
    )

    for text in cases:
        assert normalizer.normalize(PlanRequest(user_input=text)).status != (
            "clarification_required"
        )


def test_named_location_without_supported_area_mapping_requires_clarification() -> None:
    decision = RequirementNormalizer().normalize(
        PlanRequest(user_input="去第二外国语学院附近吃饭")
    )

    assert decision.status == "clarification_required"
    assert decision.unresolved[0].code == "missing_location_reference"
    assert decision.unresolved[0].evidence == "去第二外国语学院附近"


def test_plan_request_round_trip_preserves_field_provenance() -> None:
    original = PlanRequest(
        user_input="别离家太远",
        area_anchor="望京片区",
        provided_fields=frozenset({"user_input", "area_anchor"}),
    )

    restored = PlanRequest.from_dict(original.to_dict())

    assert restored.user_input == original.user_input
    assert restored.area_anchor == original.area_anchor
    assert restored.provided_fields == frozenset({"user_input", "area_anchor"})


def test_legacy_request_does_not_invent_explicit_area_provenance() -> None:
    legacy_payload = PlanRequest(user_input="下午出去玩").to_dict()
    legacy_payload.pop("provided_fields")

    restored = PlanRequest.from_dict(legacy_payload)

    assert restored.provided_fields == frozenset({"user_input"})
    assert RequirementNormalizer().normalize(restored).status == (
        "proceed_with_assumptions"
    )
