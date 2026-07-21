"""Regression tests for v5.5 natural-language constraint preservation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.types import Plan, Step, UserPreferences  # noqa: E402
from application import (  # noqa: E402
    ConstraintNormalizer,
    PlanRequest,
    PlanningClarificationRequired,
    PlanningService,
)
from data_profile import DataProfile  # noqa: E402


def _profile() -> DataProfile:
    return DataProfile(
        name="demo",
        classification="synthetic",
        public_reproducible=True,
        sources={},
        counts={},
        limitations=("not live",),
    )


def test_extracts_the_reproduced_hard_constraints_without_default_drift() -> None:
    text = "周六下午三点，两个人在三里屯玩三小时，人均预算100元，不吃辣"
    result = ConstraintNormalizer().normalize(PlanRequest(user_input=text))

    prefs = result.request.preferences
    assert result.requires_clarification is False
    assert result.request.area_anchor == "五道营-雍和宫片区"
    assert prefs.party_size == 2
    assert prefs.target_start == "15:00"
    assert prefs.duration_hours == 3
    assert prefs.budget_per_person == 100
    assert prefs.diet_flags == ["no_spicy"]
    assert result.ledger.applied_fields == (
        "preferences.party_size",
        "preferences.diet_flags",
        "preferences.budget_per_person",
        "preferences.target_start",
        "preferences.duration_hours",
    )
    assert "人数=2" in result.ledger.rewritten_query
    assert "开始=15:00" in result.ledger.rewritten_query
    assert "时长=3小时" in result.ledger.rewritten_query
    assert "人均预算=100元" in result.ledger.rewritten_query
    assert "忌口=no_spicy" in result.ledger.rewritten_query


def test_explicit_structured_value_matching_text_is_reconciled() -> None:
    request = PlanRequest(
        user_input="下午三点，两个人逛三小时",
        preferences=UserPreferences(
            party_size=2,
            target_start="15:00",
            duration_hours=3,
            raw_input="下午三点，两个人逛三小时",
        ),
        provided_fields=frozenset(
            {
                "user_input",
                "preferences.party_size",
                "preferences.target_start",
                "preferences.duration_hours",
            }
        ),
    )

    result = ConstraintNormalizer().normalize(request)

    assert result.requires_clarification is False
    entries = {entry.field: entry for entry in result.ledger.entries}
    assert entries["preferences.party_size"].source == "explicit_structured"
    assert entries["preferences.party_size"].outcome == "matched"
    assert entries["preferences.target_start"].outcome == "matched"


def test_explicit_structured_value_conflicting_with_text_fails_closed() -> None:
    request = PlanRequest(
        user_input="下午三点，两个人逛三小时",
        preferences=UserPreferences(
            party_size=4,
            target_start="14:00",
            duration_hours=3,
            raw_input="下午三点，两个人逛三小时",
        ),
        provided_fields=frozenset(
            {
                "user_input",
                "preferences.party_size",
                "preferences.target_start",
                "preferences.duration_hours",
            }
        ),
    )

    result = ConstraintNormalizer().normalize(request)

    assert result.requires_clarification is True
    assert [item.field for item in result.ledger.conflicts] == [
        "preferences.party_size",
        "preferences.target_start",
    ]
    assert result.request.preferences.party_size == 4
    assert result.request.preferences.target_start == "14:00"


def test_diet_restrictions_merge_because_dropping_one_is_unsafe() -> None:
    request = PlanRequest(
        user_input="两个人，不吃辣，也不要海鲜",
        preferences=UserPreferences(
            party_size=2,
            diet_flags=["vegetarian"],
            raw_input="两个人，不吃辣，也不要海鲜",
        ),
        provided_fields=frozenset(
            {"user_input", "preferences.party_size", "preferences.diet_flags"}
        ),
    )

    result = ConstraintNormalizer().normalize(request)

    assert result.requires_clarification is False
    assert result.request.preferences.diet_flags == [
        "vegetarian",
        "no_spicy",
        "no_seafood",
    ]
    entry = next(
        item for item in result.ledger.entries if item.field == "preferences.diet_flags"
    )
    assert entry.outcome == "merged"


@pytest.mark.parametrize(
    "text",
    [
        "预算充足，下午随便，想在城里走走",
        "第二个人负责周六的安排",
        "不想只吃清淡的，正常餐馆就行",
        "和4个朋友聊过这个方案，但人数还没定",
    ],
)
def test_negative_phrases_do_not_invent_hard_constraints(text: str) -> None:
    result = ConstraintNormalizer().normalize(PlanRequest(user_input=text))

    assert result.ledger.applied_fields == ()
    assert result.request.preferences == UserPreferences()


def test_text_derived_request_round_trip_remains_idempotent() -> None:
    text = "下午三点，两个人玩三小时，人均100元"
    first = ConstraintNormalizer().normalize(PlanRequest(user_input=text))
    restored = PlanRequest.from_dict(first.request.to_dict())
    second = ConstraintNormalizer().normalize(restored)

    assert restored.provided_fields == frozenset({"user_input"})
    assert second.requires_clarification is False
    assert second.request.to_dict() == first.request.to_dict()
    assert second.ledger.to_dict() == first.ledger.to_dict()


def test_service_forwards_normalized_constraints_and_returns_ledger() -> None:
    captured = {}

    def planner(**kwargs):
        captured.update(kwargs)
        return Plan(
            persona=kwargs["persona"],
            area_anchor=kwargs["area_anchor"],
            steps=[Step(step_index=1, poi_name="测试点", start_time="15:00")],
        )

    service = PlanningService(
        planner=planner,
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
    )

    result = service.execute(
        PlanRequest(
            user_input="周六下午三点，两个人在三里屯玩三小时，人均预算100元，不吃辣"
        )
    )

    assert captured["area_anchor"] == "三里屯片区"
    assert captured["prefs"].party_size == 2
    assert captured["prefs"].target_start == "15:00"
    assert captured["prefs"].duration_hours == 3
    assert result.request.preferences is captured["prefs"]
    assert result.constraints.version == "constraint_ledger_v1"
    assert result.constraints.conflicts == ()


def test_service_turns_constraint_conflict_into_one_clarification() -> None:
    request = PlanRequest(
        user_input="两个人下午三点出发",
        preferences=UserPreferences(
            party_size=4,
            target_start="15:00",
            raw_input="两个人下午三点出发",
        ),
        provided_fields=frozenset(
            {"user_input", "preferences.party_size", "preferences.target_start"}
        ),
    )

    with pytest.raises(PlanningClarificationRequired) as raised:
        PlanningService(
            planner=lambda **kwargs: pytest.fail("planner must not run"),
            prober=lambda plan, **kwargs: pytest.fail("prober must not run"),
        ).execute(request)

    assert raised.value.decision.status == "clarification_required"
    assert raised.value.decision.unresolved[0].code == "constraint_conflict"
    assert raised.value.decision.questions[0].options == (
        "使用文本值：2",
        "使用结构化值：4",
        "重新描述该约束",
    )
    assert raised.value.constraints is not None
    assert raised.value.constraints.conflicts[0].field == "preferences.party_size"
