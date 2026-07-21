"""Golden-set loader and transparent metrics for the v5.5 Constraint Ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.types import UserPreferences
from application import ConstraintNormalizer, PlanRequest


@dataclass(frozen=True)
class ConstraintCase:
    case_id: str
    user_input: str
    persona: str
    preferences: dict[str, Any]
    provided_fields: frozenset[str]
    expected_text: dict[str, Any]
    expected_effective: dict[str, Any]
    expected_conflicts: tuple[str, ...]
    expected_rewrite_contains: tuple[str, ...]

    def to_request(self) -> PlanRequest:
        persona = self.persona
        prefs = self.preferences
        return PlanRequest(
            user_input=self.user_input,
            persona=persona,
            preferences=UserPreferences(
                persona=persona,
                party_size=int(prefs.get("party_size", 3)),
                has_child=bool(prefs.get("has_child", False)),
                child_age=prefs.get("child_age"),
                diet_flags=list(prefs.get("diet_flags") or []),
                walk_radius_km=float(prefs.get("walk_radius_km", 1.5)),
                budget_per_person=prefs.get("budget_per_person"),
                target_start=str(prefs.get("target_start") or "14:00"),
                duration_hours=float(prefs.get("duration_hours", 4.5)),
                raw_input=self.user_input,
            ),
            provided_fields=self.provided_fields,
        )


@dataclass(frozen=True)
class ConstraintGoldenSet:
    name: str
    classification: str
    label_basis: str
    cases: tuple[ConstraintCase, ...]
    sha256: str


def load_golden_set(path: Path) -> ConstraintGoldenSet:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported constraint golden-set schema")
    cases = tuple(_load_case(item) for item in payload.get("cases") or [])
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("constraint cases must be non-empty and uniquely identified")
    return ConstraintGoldenSet(
        name=str(payload["name"]),
        classification=str(payload["classification"]),
        label_basis=str(payload["label_basis"]),
        cases=cases,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def evaluate_constraint_ledger(
    golden: ConstraintGoldenSet,
    normalizer: ConstraintNormalizer | None = None,
) -> dict[str, Any]:
    normalizer = normalizer or ConstraintNormalizer()
    raw_cases: list[dict[str, Any]] = []
    for case in golden.cases:
        result = normalizer.normalize(case.to_request())
        observed_text = {
            entry.field: _json_value(entry.text_value)
            for entry in result.ledger.entries
            if entry.text_value is not None
        }
        observed_effective = {
            field: _effective_value(result.request, field)
            for field in case.expected_effective
        }
        observed_conflicts = [item.field for item in result.ledger.conflicts]
        expected_pairs = _pairs(case.expected_text)
        observed_pairs = _pairs(observed_text)
        rewrite_checks = {
            fragment: fragment in result.ledger.rewritten_query
            for fragment in case.expected_rewrite_contains
        }
        idempotent = None
        round_trip_request = None
        round_trip_ledger = None
        if not observed_conflicts:
            restored = PlanRequest.from_dict(result.request.to_dict())
            repeated = normalizer.normalize(restored)
            round_trip_request = repeated.request.to_dict()
            round_trip_ledger = repeated.ledger.to_dict()
            idempotent = (
                round_trip_request == result.request.to_dict()
                and round_trip_ledger == result.ledger.to_dict()
            )
        raw_cases.append(
            {
                "case_id": case.case_id,
                "normalized_request": result.request.to_dict(),
                "ledger": result.ledger.to_dict(),
                "round_trip_request": round_trip_request,
                "round_trip_ledger": round_trip_ledger,
                "expected_text": case.expected_text,
                "observed_text": observed_text,
                "expected_effective": case.expected_effective,
                "observed_effective": observed_effective,
                "expected_conflicts": list(case.expected_conflicts),
                "observed_conflicts": observed_conflicts,
                "pair_true_positive": len(expected_pairs & observed_pairs),
                "pair_false_positive": len(observed_pairs - expected_pairs),
                "pair_false_negative": len(expected_pairs - observed_pairs),
                "preserved_count": sum(
                    _same_value(observed_effective.get(field), value)
                    for field, value in case.expected_effective.items()
                ),
                "preservation_total": len(case.expected_effective),
                "rewrite_checks": rewrite_checks,
                "idempotent": idempotent,
            }
        )
    return {
        "case_count": len(raw_cases),
        "metrics": recompute_metrics(raw_cases),
        "raw_cases": raw_cases,
    }


def recompute_metrics(raw_cases: list[dict[str, Any]]) -> dict[str, float]:
    if not raw_cases:
        raise ValueError("constraint evaluation needs at least one raw case")
    true_positive = sum(int(item["pair_true_positive"]) for item in raw_cases)
    false_positive = sum(int(item["pair_false_positive"]) for item in raw_cases)
    false_negative = sum(int(item["pair_false_negative"]) for item in raw_cases)
    precision = _rate(true_positive, true_positive + false_positive)
    recall = _rate(true_positive, true_positive + false_negative)
    f1 = (
        round(2 * precision * recall / (precision + recall), 6)
        if precision + recall
        else 0.0
    )
    preservation_total = sum(int(item["preservation_total"]) for item in raw_cases)
    preserved = sum(int(item["preserved_count"]) for item in raw_cases)

    expected_conflicts = sum(len(item["expected_conflicts"]) for item in raw_cases)
    observed_conflicts = sum(len(item["observed_conflicts"]) for item in raw_cases)
    conflict_true_positive = sum(
        len(set(item["expected_conflicts"]) & set(item["observed_conflicts"]))
        for item in raw_cases
    )
    conflict_false_positive = sum(
        len(set(item["observed_conflicts"]) - set(item["expected_conflicts"]))
        for item in raw_cases
    )
    rewrite_total = sum(len(item["rewrite_checks"]) for item in raw_cases)
    rewrite_passed = sum(
        sum(bool(value) for value in item["rewrite_checks"].values())
        for item in raw_cases
    )
    idempotency_cases = [item for item in raw_cases if item["idempotent"] is not None]
    return {
        "field_extraction_precision": precision,
        "field_extraction_recall": recall,
        "field_extraction_f1": f1,
        "false_extraction_rate": _rate(
            false_positive,
            true_positive + false_positive,
        ),
        "hard_constraint_preservation_rate": _rate(preserved, preservation_total),
        "explicit_conflict_detection_recall": _rate(
            conflict_true_positive,
            expected_conflicts,
        ),
        "false_conflict_rate": _rate(
            conflict_false_positive,
            observed_conflicts,
        ),
        "rewrite_constraint_coverage_rate": _rate(rewrite_passed, rewrite_total),
        "round_trip_idempotency_rate": _rate(
            sum(bool(item["idempotent"]) for item in idempotency_cases),
            len(idempotency_cases),
        ),
    }


def _load_case(item: dict[str, Any]) -> ConstraintCase:
    expected = item.get("expected") or {}
    expected_text = dict(expected.get("text_constraints") or {})
    expected_effective = dict(expected.get("effective_constraints") or {})
    expected_conflicts = tuple(expected.get("conflicts") or [])
    rewrite_contains = tuple(expected.get("rewrite_contains") or [])
    preferences = dict(item.get("preferences") or {})
    persona = str(item.get("persona") or "family")
    provided_fields = frozenset(item.get("provided_fields") or ["user_input"])
    if "persona" in preferences and preferences["persona"] != persona:
        raise ValueError(f"constraint persona mismatch: {item.get('case_id')}")
    if any(not str(field).strip() for field in expected_text):
        raise ValueError("constraint expected fields must not be empty")
    return ConstraintCase(
        case_id=str(item["case_id"]),
        user_input=str(item["user_input"]),
        persona=persona,
        preferences=preferences,
        provided_fields=provided_fields,
        expected_text=expected_text,
        expected_effective=expected_effective,
        expected_conflicts=expected_conflicts,
        expected_rewrite_contains=rewrite_contains,
    )


def _effective_value(request: PlanRequest, field: str) -> Any:
    if field == "persona":
        return request.persona
    prefix = "preferences."
    if not field.startswith(prefix):
        raise ValueError(f"unsupported effective constraint field: {field}")
    return _json_value(getattr(request.preferences, field[len(prefix) :]))


def _pairs(values: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (field, json.dumps(value, ensure_ascii=False, sort_keys=True))
        for field, value in values.items()
    }


def _json_value(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 1e-9
    return left == right


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
