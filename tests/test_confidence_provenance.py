from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.confidence import (
    CONTROL_STEP_SOURCE,
    EVIDENCE_SUPPORT_SOURCE,
    EvidenceSnapshot,
    estimate_plan_confidence,
    estimate_step_confidence,
)
from agents.types import Plan, Step
from data_profile import DataProfile


def _profile(classification: str = "synthetic") -> DataProfile:
    return DataProfile(
        name=f"test-{classification}",
        classification=classification,
        public_reproducible=True,
        sources={},
        counts={},
        limitations=(),
    )


def _snapshot(plan: Plan) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        rating_by_poi_id={"poi-1": 4.8},
        ugc_by_poi_name={"测试地点": (20, 0.9)},
    )


def test_estimate_plan_adds_deterministic_provenance() -> None:
    plan = Plan(
        persona="family",
        area_anchor="测试片区",
        steps=[
            Step(
                step_index=1,
                poi_id="poi-1",
                poi_name="测试地点",
                rationale="有明确地点、路线和用户可核对的推荐理由。" * 4,
                travel_time_min=12,
                booking={"status": "confirmed"},
            ),
            Step(step_index=2, kind="depart", poi_name="返程"),
        ],
    )

    estimate_plan_confidence(plan, profile=_profile(), evidence_lookup=_snapshot)
    first = plan.steps[0]
    assert first.confidence == 0.79  # synthetic profile 的诚实上限
    assert first.confidence_source == EVIDENCE_SUPPORT_SOURCE
    assert first.confidence_factors["ugc_count"] == 20
    assert "not a calibrated probability" in first.confidence_factors["semantics"]
    assert plan.steps[1].confidence_source == CONTROL_STEP_SOURCE

    original = first.confidence_factors.copy()
    estimate_plan_confidence(plan, profile=_profile(), evidence_lookup=_snapshot)
    assert first.confidence == 0.79
    assert first.confidence_factors == original


def test_risks_lower_and_booking_raises_support() -> None:
    empty = EvidenceSnapshot(rating_by_poi_id={}, ugc_by_poi_name={})
    plain = Step(step_index=1, poi_name="未知地点")
    risky = Step(step_index=1, poi_name="未知地点", risk_tags=["queue", "closed"])
    booked = Step(step_index=1, poi_name="未知地点", booking={"status": "confirmed"})

    plain_score, _, _ = estimate_step_confidence(plain, snapshot=empty, profile=_profile("real"))
    risky_score, _, _ = estimate_step_confidence(risky, snapshot=empty, profile=_profile("real"))
    booked_score, _, _ = estimate_step_confidence(booked, snapshot=empty, profile=_profile("real"))

    assert risky_score < plain_score < booked_score


def test_tracer_receives_source_factors_and_semantics(monkeypatch) -> None:
    from agents import planner

    captured = []
    plan = Plan(
        persona="family",
        area_anchor="测试片区",
        steps=[Step(step_index=1, poi_name="测试地点", rationale="理由")],
    )

    def fake_estimate(target: Plan) -> Plan:
        target.steps[0].confidence = 0.61
        target.steps[0].confidence_source = EVIDENCE_SUPPORT_SOURCE
        target.steps[0].confidence_factors = {
            "ugc_count": 3,
            "semantics": "evidence support score; not a calibrated probability",
        }
        return target

    monkeypatch.setattr(planner, "estimate_plan_confidence", fake_estimate)
    monkeypatch.setattr(
        planner,
        "_tracer_replace_steps",
        lambda plan_id, steps: captured.extend(steps),
    )

    planner._record_plan_to_tracer(plan)

    assert captured[0].confidence == 0.61
    evidence = captured[0].evidence
    assert evidence["confidence_source"] == EVIDENCE_SUPPORT_SOURCE
    assert evidence["confidence_factors"]["ugc_count"] == 3
    assert "not a calibrated probability" in evidence["confidence_semantics"]
