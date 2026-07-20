"""Independent integrity and metric verification for constraint artifacts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .evaluate import load_golden_set, recompute_metrics


def canonical_artifact_sha256(payload: dict) -> str:
    canonical_payload = deepcopy(payload)
    canonical_payload.pop("artifact_sha256", None)
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_constraint_artifact(artifact_path: Path, golden_path: Path) -> dict:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported constraint artifact schema")
    if artifact.get("artifact_sha256") != canonical_artifact_sha256(artifact):
        raise ValueError("constraint artifact SHA-256 mismatch")

    golden = load_golden_set(golden_path)
    recorded_golden = artifact.get("golden_set") or {}
    if recorded_golden.get("sha256") != golden.sha256:
        raise ValueError("constraint golden-set SHA-256 mismatch")
    if recorded_golden.get("case_count") != len(golden.cases):
        raise ValueError("constraint golden-set case count mismatch")

    result = artifact.get("result") or {}
    raw_cases = result.get("raw_cases") or []
    if result.get("case_count") != len(golden.cases) or len(raw_cases) != len(golden.cases):
        raise ValueError("constraint result case count mismatch")
    by_id = {item.get("case_id"): item for item in raw_cases}
    if len(by_id) != len(raw_cases):
        raise ValueError("constraint raw case IDs must be unique")

    for case in golden.cases:
        observed = by_id.get(case.case_id)
        if observed is None:
            raise ValueError(f"missing constraint raw case: {case.case_id}")
        if observed.get("expected_text") != case.expected_text:
            raise ValueError(f"constraint text label mismatch: {case.case_id}")
        if observed.get("expected_effective") != case.expected_effective:
            raise ValueError(f"constraint effective label mismatch: {case.case_id}")
        if observed.get("expected_conflicts") != list(case.expected_conflicts):
            raise ValueError(f"constraint conflict label mismatch: {case.case_id}")
        ledger = observed.get("ledger") or {}
        normalized_request = observed.get("normalized_request") or {}
        independently_observed_text = {
            entry["field"]: entry["text_value"]
            for entry in ledger.get("entries") or []
            if entry.get("text_value") is not None
        }
        if observed.get("observed_text") != independently_observed_text:
            raise ValueError(f"constraint observed text mismatch: {case.case_id}")
        independently_observed_conflicts = [
            item["field"] for item in ledger.get("conflicts") or []
        ]
        if observed.get("observed_conflicts") != independently_observed_conflicts:
            raise ValueError(f"constraint observed conflict mismatch: {case.case_id}")
        independently_observed_effective = {
            field: _effective_value(normalized_request, field)
            for field in case.expected_effective
        }
        if observed.get("observed_effective") != independently_observed_effective:
            raise ValueError(f"constraint observed effective mismatch: {case.case_id}")
        expected_pairs = _pairs(case.expected_text)
        observed_pairs = _pairs(observed.get("observed_text") or {})
        if observed.get("pair_true_positive") != len(expected_pairs & observed_pairs):
            raise ValueError(f"constraint true-positive mismatch: {case.case_id}")
        if observed.get("pair_false_positive") != len(observed_pairs - expected_pairs):
            raise ValueError(f"constraint false-positive mismatch: {case.case_id}")
        if observed.get("pair_false_negative") != len(expected_pairs - observed_pairs):
            raise ValueError(f"constraint false-negative mismatch: {case.case_id}")
        preserved = sum(
            _same_value((observed.get("observed_effective") or {}).get(field), value)
            for field, value in case.expected_effective.items()
        )
        if observed.get("preservation_total") != len(case.expected_effective):
            raise ValueError(f"constraint preservation total mismatch: {case.case_id}")
        if observed.get("preserved_count") != preserved:
            raise ValueError(f"constraint preservation count mismatch: {case.case_id}")
        expected_rewrite_keys = set(case.expected_rewrite_contains)
        rewrite_checks = observed.get("rewrite_checks") or {}
        if set(rewrite_checks) != expected_rewrite_keys:
            raise ValueError(f"constraint rewrite label mismatch: {case.case_id}")
        rewritten_query = str(ledger.get("rewritten_query") or "")
        independent_rewrite_checks = {
            fragment: fragment in rewritten_query
            for fragment in case.expected_rewrite_contains
        }
        if rewrite_checks != independent_rewrite_checks:
            raise ValueError(f"constraint rewrite evidence mismatch: {case.case_id}")
        round_trip_request = observed.get("round_trip_request")
        round_trip_ledger = observed.get("round_trip_ledger")
        independently_idempotent = None
        if not independently_observed_conflicts:
            independently_idempotent = (
                round_trip_request == normalized_request
                and round_trip_ledger == ledger
            )
        elif round_trip_request is not None or round_trip_ledger is not None:
            raise ValueError(f"unexpected constraint conflict round trip: {case.case_id}")
        if observed.get("idempotent") is not independently_idempotent:
            raise ValueError(f"constraint idempotency evidence mismatch: {case.case_id}")

    metrics = recompute_metrics(raw_cases)
    if result.get("metrics") != metrics:
        raise ValueError("constraint metrics do not match raw cases")
    return artifact


def _pairs(values: dict) -> set[tuple[str, str]]:
    return {
        (field, json.dumps(value, ensure_ascii=False, sort_keys=True))
        for field, value in values.items()
    }


def _same_value(left, right) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 1e-9
    return left == right


def _effective_value(request: dict, field: str):
    if field == "persona":
        return request.get("persona")
    prefix = "preferences."
    if not field.startswith(prefix):
        raise ValueError(f"unsupported effective constraint field: {field}")
    return (request.get("preferences") or {}).get(field[len(prefix) :])
