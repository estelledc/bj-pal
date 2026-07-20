"""Application-level resolution semantics for v5.6 clarification continuation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.types import UserPreferences  # noqa: E402
from application import (  # noqa: E402
    ClarificationResolution,
    ConstraintNormalizer,
    PlanRequest,
    PlanningPreflight,
)


DECISION_SHA = "a" * 64


def _resolution(*, code: str, field: str, value, option_id: str) -> ClarificationResolution:
    return ClarificationResolution(
        code=code,
        field=field,
        value=value,
        option_id=option_id,
        answer=str(value),
        decision_sha256=DECISION_SHA,
    )


def test_plan_request_resolution_round_trip_preserves_audit_provenance() -> None:
    resolution = _resolution(
        code="constraint_conflict",
        field="preferences.party_size",
        value=2,
        option_id="use_text_value",
    )
    request = PlanRequest(user_input="两个人", resolutions=(resolution,))

    restored = PlanRequest.from_dict(request.to_dict())

    assert restored.resolutions == (resolution,)
    assert restored.resolution_for(
        code="constraint_conflict",
        field="preferences.party_size",
    ) == resolution


@pytest.mark.parametrize(
    ("option_id", "value", "expected"),
    [("use_text_value", 2, 2), ("use_structured_value", 4, 4)],
)
def test_constraint_conflict_resolution_does_not_loop(
    option_id: str,
    value: int,
    expected: int,
) -> None:
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
        resolutions=(
            _resolution(
                code="constraint_conflict",
                field="preferences.party_size",
                value=value,
                option_id=option_id,
            ),
        ),
    )

    result = PlanningPreflight().normalize(request)

    assert result.request.preferences.party_size == expected
    assert result.constraints.conflicts == ()
    entry = next(
        item
        for item in result.constraints.entries
        if item.field == "preferences.party_size"
    )
    assert entry.source == "user_clarification"
    assert entry.outcome == "resolved"
    assert entry.text_value == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [("三里屯片区", "三里屯片区"), ("五道营-雍和宫片区", "五道营-雍和宫片区")],
)
def test_area_conflict_resolution_is_authoritative(value: str, expected: str) -> None:
    request = PlanRequest(
        user_input="周六下午去三里屯",
        area_anchor="五道营-雍和宫片区",
        provided_fields=frozenset({"user_input", "area_anchor"}),
        resolutions=(
            _resolution(
                code="conflicting_area_anchor",
                field="area_anchor",
                value=value,
                option_id="use_resolved_area",
            ),
        ),
    )

    result = PlanningPreflight().normalize(request)

    assert result.request.area_anchor == expected
    assert result.requirements.status == "proceed"
    assert result.requirements.signals[-1].code == "area_from_clarification"


def test_invalid_persisted_resolution_value_fails_closed() -> None:
    request = PlanRequest(
        user_input="两个人",
        preferences=UserPreferences(party_size=4, raw_input="两个人"),
        provided_fields=frozenset({"user_input", "preferences.party_size"}),
        resolutions=(
            _resolution(
                code="constraint_conflict",
                field="preferences.party_size",
                value=200,
                option_id="use_text_value",
            ),
        ),
    )

    with pytest.raises(ValueError, match="resolution.*party_size"):
        ConstraintNormalizer().normalize(request)
