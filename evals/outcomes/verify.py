"""Independently recompute every human-outcome evidence metric."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


EXPECTED_CASES = {
    "capability_and_artifact_binding",
    "idempotency_and_schema",
    "expiry_and_append_only",
    "minimum_sample_gate",
}
EXPECTED_REASONS = [
    "availability_issue",
    "group_disagreement",
    "other",
    "route_issue",
    "schedule_unrealistic",
    "too_expensive",
    "too_far",
    "unsuitable_poi",
    "weather_issue",
]


def _sha(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_outcome_artifact(path: Path) -> dict:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported outcome artifact schema")
    canonical = deepcopy(artifact)
    observed_sha256 = canonical.pop("artifact_sha256", None)
    if observed_sha256 != _sha(canonical):
        raise ValueError("outcome artifact SHA-256 mismatch")
    if artifact.get("classification") != "synthetic_contract":
        raise ValueError("outcome artifact classification mismatch")
    if artifact.get("policy") != {
        "classification": "self_reported_unverified",
        "minimum_phase_samples": 5,
        "reason_codes": EXPECTED_REASONS,
        "raw_capability_persisted": False,
        "free_text_accepted": False,
        "report_uniqueness": "plan_artifact_phase",
    }:
        raise ValueError("outcome evidence policy mismatch")
    cases = (artifact.get("result") or {}).get("raw_cases") or []
    indexed = {case.get("case_id"): case for case in cases}
    if set(indexed) != EXPECTED_CASES or len(indexed) != len(cases):
        raise ValueError("outcome evidence case contract mismatch")

    binding = indexed["capability_and_artifact_binding"]
    report = deepcopy(binding.get("report") or {})
    report_sha256 = report.pop("report_sha256", None)
    invitation = binding.get("invitation") or {}
    idempotency = indexed["idempotency_and_schema"]
    expiry = indexed["expiry_and_append_only"]
    sample = indexed["minimum_sample_gate"]
    before = sample.get("before") or {}
    after = sample.get("after") or {}
    metrics = {
        "case_count": len(cases),
        "capability_binding_rate": float(
            binding.get("mismatch_error") == "feedback_not_found"
            and binding.get("plan_id") == invitation.get("plan_id")
            and binding.get("plan_artifact_sha256")
            == report.get("plan_artifact_sha256")
            == invitation.get("plan_artifact_sha256")
        ),
        "artifact_integrity_rate": float(
            binding.get("invitation_sha256") == _sha(invitation)
            and report_sha256 == _sha(report)
        ),
        "idempotency_rate": float(
            idempotency.get("first_feedback_id")
            == idempotency.get("replay_feedback_id")
            and idempotency.get("idempotency_error")
            == "feedback_idempotency_conflict"
            and idempotency.get("phase_error") == "feedback_phase_conflict"
        ),
        "schema_validation_rate": float(
            idempotency.get("phase_value_error") == "invalid_feedback"
            and idempotency.get("reason_required_error") == "invalid_feedback"
            and idempotency.get("free_text_error") == "invalid_feedback"
        ),
        "expiry_fail_closed_rate": float(
            expiry.get("expiry_error") == "feedback_expired"
            and expiry.get("expired_report_count") == 0
        ),
        "append_only_rate": float(
            expiry.get("invitation_append_only") is True
            and expiry.get("report_append_only") is True
        ),
        "privacy_minimization_rate": float(
            binding.get("raw_capability_persisted") is False
            and binding.get("free_text_columns_present") is False
        ),
        "minimum_sample_gate_rate": float(
            before.get("phase_counts") == {"decision": 5, "outcome": 4}
            and before.get("decision_acceptance_rate") == 0.6
            and before.get("outcome_completion_rate") is None
            and before.get("evidence_level") == "aggregate_self_reported"
            and after.get("phase_counts") == {"decision": 5, "outcome": 5}
            and after.get("decision_acceptance_rate") == 0.6
            and after.get("outcome_completion_rate") == 0.8
            and after.get("classification") == "self_reported_unverified"
        ),
    }
    claimed = (artifact.get("result") or {}).get("metrics")
    if claimed != metrics:
        raise ValueError(f"outcome evidence metrics mismatch: {claimed!r} != {metrics!r}")
    if any(value != 1.0 for key, value in metrics.items() if key != "case_count"):
        raise ValueError("outcome evidence safety gate failed")
    return artifact
