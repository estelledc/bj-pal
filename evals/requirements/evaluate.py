"""Load requirement-gate labels and compute transparent classification metrics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from application import PlanRequest, RequirementNormalizer


VALID_STATUSES = {
    "proceed",
    "proceed_with_assumptions",
    "clarification_required",
}


@dataclass(frozen=True)
class RequirementCase:
    case_id: str
    user_input: str
    area_anchor: str
    provided_fields: frozenset[str]
    expected_status: str
    follow_up: dict | None

    def to_request(self) -> PlanRequest:
        return PlanRequest(
            user_input=self.user_input,
            area_anchor=self.area_anchor,
            provided_fields=self.provided_fields,
        )


@dataclass(frozen=True)
class RequirementGoldenSet:
    name: str
    classification: str
    label_basis: str
    cases: tuple[RequirementCase, ...]
    sha256: str


def load_golden_set(path: Path) -> RequirementGoldenSet:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported requirement golden-set schema")
    cases = tuple(_load_case(item) for item in payload.get("cases") or [])
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("requirement cases must be non-empty and uniquely identified")
    return RequirementGoldenSet(
        name=str(payload["name"]),
        classification=str(payload["classification"]),
        label_basis=str(payload["label_basis"]),
        cases=cases,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def evaluate_requirement_gate(
    golden: RequirementGoldenSet,
    normalizer: RequirementNormalizer | None = None,
) -> dict:
    normalizer = normalizer or RequirementNormalizer()
    raw_cases = []
    for case in golden.cases:
        decision = normalizer.normalize(case.to_request())
        follow_up_status = None
        post_clarification_executable = None
        if case.follow_up is not None:
            follow_up = _request_from_payload(case.follow_up)
            follow_up_status = normalizer.normalize(follow_up).status
            post_clarification_executable = follow_up_status != "clarification_required"
        raw_cases.append(
            {
                "case_id": case.case_id,
                "expected_status": case.expected_status,
                "observed_status": decision.status,
                "correct": decision.status == case.expected_status,
                "clarification_triggered": decision.requires_clarification,
                "expected_clarification": case.expected_status == "clarification_required",
                "unresolved_codes": [item.code for item in decision.unresolved],
                "follow_up_status": follow_up_status,
                "post_clarification_executable": post_clarification_executable,
            }
        )
    return {
        "case_count": len(raw_cases),
        "metrics": recompute_metrics(raw_cases),
        "raw_cases": raw_cases,
    }


def recompute_metrics(raw_cases: list[dict]) -> dict:
    if not raw_cases:
        raise ValueError("requirement evaluation needs at least one raw case")
    positives = [item for item in raw_cases if item["expected_clarification"]]
    negatives = [item for item in raw_cases if not item["expected_clarification"]]
    follow_ups = [
        item for item in raw_cases if item["post_clarification_executable"] is not None
    ]
    triggered = sum(bool(item["clarification_triggered"]) for item in raw_cases)
    true_positive = sum(bool(item["clarification_triggered"]) for item in positives)
    false_positive = sum(bool(item["clarification_triggered"]) for item in negatives)
    executable = sum(bool(item["post_clarification_executable"]) for item in follow_ups)
    return {
        "clarification_trigger_rate": _rate(triggered, len(raw_cases)),
        "false_clarification_rate": _rate(false_positive, len(negatives)),
        "required_clarification_recall": _rate(true_positive, len(positives)),
        "decision_accuracy": _rate(
            sum(bool(item["correct"]) for item in raw_cases), len(raw_cases)
        ),
        "post_clarification_gate_executability_rate": _rate(
            executable, len(follow_ups)
        ),
    }


def _load_case(item: dict) -> RequirementCase:
    expected_status = str(item["expected_status"])
    if expected_status not in VALID_STATUSES:
        raise ValueError(f"invalid requirement status: {expected_status}")
    provided_fields = frozenset(item.get("provided_fields") or ["user_input"])
    follow_up = item.get("follow_up")
    if expected_status == "clarification_required" and not isinstance(follow_up, dict):
        raise ValueError("clarification cases need a labeled follow_up")
    return RequirementCase(
        case_id=str(item["case_id"]),
        user_input=str(item["user_input"]),
        area_anchor=str(item.get("area_anchor") or "五道营-雍和宫片区"),
        provided_fields=provided_fields,
        expected_status=expected_status,
        follow_up=follow_up,
    )


def _request_from_payload(payload: dict) -> PlanRequest:
    return PlanRequest(
        user_input=str(payload["user_input"]),
        area_anchor=str(payload.get("area_anchor") or "五道营-雍和宫片区"),
        provided_fields=frozenset(payload.get("provided_fields") or ["user_input"]),
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
