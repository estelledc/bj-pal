"""Independently recompute durable-job diagnostic classifications and hashes."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


EXPECTED = {
    "active-first-attempt": ("in_progress", "wait_for_worker"),
    "retry-pending": ("retry_pending", "wait_for_scheduled_retry"),
    "lease-recovery": ("lease_recovery_in_progress", "monitor_reclaimed_worker"),
    "completed": ("completed", "none"),
    "cancelled": ("cancelled", "none"),
    "queue-timeout": (
        "queue_deadline_exceeded",
        "review_deadline_and_queue_capacity_before_replay",
    ),
    "execution-timeout": (
        "execution_deadline_exceeded",
        "review_deadline_and_queue_capacity_before_replay",
    ),
    "invalid-persisted-request": (
        "persisted_request_invalid",
        "inspect_persisted_request_migration",
    ),
    "clarification-required": (
        "clarification_required",
        "resubmit_with_clarification",
    ),
    "execution-budget": (
        "execution_budget_exceeded",
        "reduce_work_or_adjust_server_budget",
    ),
    "model-output": (
        "model_output_rejected",
        "inspect_model_output_contract_cases",
    ),
    "worker-lease-exhausted": (
        "worker_lease_exhausted",
        "inspect_worker_health_before_replay",
    ),
    "runtime-unknown": (
        "runtime_or_dependency_unknown",
        "inspect_dependency_health_before_replay",
    ),
    "unclassified-safe-redaction": ("unclassified_failure", "manual_review"),
}
SAFE_ERROR_CODES = {
    "invalid_persisted_request",
    "clarification_required",
    "execution_budget_exceeded",
    "invalid_model_output",
    "planning_execution_failed",
    "job_deadline_exceeded",
    "lease_expired_attempts_exhausted",
}
TERMINAL = {"succeeded", "failed", "dead_lettered", "cancelled", "timed_out"}
JOB_STATUSES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "dead_lettered",
    "cancelled",
    "timed_out",
}
JOB_EVENT_TYPES = {
    "submitted",
    "claimed",
    "heartbeat",
    "retry_scheduled",
    "lease_reclaimed",
    "cancel_requested",
    "cancelled",
    "replay_requested",
    "timed_out",
    "succeeded",
    "failed",
    "dead_lettered",
}
FAILURE_SIGNALS = {
    "retry_scheduled",
    "lease_reclaimed",
    "failed",
    "dead_lettered",
    "timed_out",
}
FORBIDDEN_KEYS = {
    "request_id",
    "request_payload",
    "tenant_id",
    "submitted_by",
    "worker_id",
    "payload",
    "error_message",
}


def _canonical_sha256(payload: Any, *, compact: bool = True) -> str:
    options = {"ensure_ascii": False, "sort_keys": True}
    if compact:
        options["separators"] = (",", ":")
    canonical = json.dumps(payload, **options).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def canonical_artifact_sha256(payload: dict[str, Any]) -> str:
    unsigned = deepcopy(payload)
    unsigned.pop("artifact_sha256", None)
    return _canonical_sha256(unsigned, compact=False)


def verify_job_diagnostic_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported job diagnostic artifact schema")
    if artifact.get("artifact_sha256") != canonical_artifact_sha256(artifact):
        raise ValueError("job diagnostic artifact SHA-256 mismatch")
    result = artifact.get("result") or {}
    raw_cases = result.get("raw_cases") or []
    if result.get("case_count") != len(EXPECTED) or len(raw_cases) != len(EXPECTED):
        raise ValueError("job diagnostic case count mismatch")
    by_id = {item.get("case_id"): item for item in raw_cases}
    if set(by_id) != set(EXPECTED) or len(by_id) != len(raw_cases):
        raise ValueError("job diagnostic case registry mismatch")
    for case_id, expected in EXPECTED.items():
        _verify_case(case_id, by_id[case_id], expected)
    metrics = _metrics(raw_cases)
    if result.get("metrics") != metrics:
        raise ValueError("job diagnostic metrics do not match raw cases")
    return artifact


def _verify_case(
    case_id: str,
    case: dict[str, Any],
    expected: tuple[str, str],
) -> None:
    if case.get("expected") != {
        "classification": expected[0],
        "recommended_action": expected[1],
    }:
        raise ValueError(f"job diagnostic label mismatch: {case_id}")
    raw = case.get("input") or {}
    job = raw.get("job") or {}
    events = raw.get("events") or []
    observed = case.get("observed") or {}
    if not events or events[0].get("event_type") != "submitted":
        raise ValueError(f"job diagnostic submitted evidence missing: {case_id}")
    if job.get("status") not in JOB_STATUSES:
        raise ValueError(f"job diagnostic raw status is invalid: {case_id}")
    if any(item.get("event_type") not in JOB_EVENT_TYPES for item in events):
        raise ValueError(f"job diagnostic raw event type is invalid: {case_id}")
    if observed.get("version") != "job_incident_diagnosis_v1":
        raise ValueError(f"job diagnostic version mismatch: {case_id}")
    if observed.get("job_id") != job.get("job_id"):
        raise ValueError(f"job diagnostic job binding mismatch: {case_id}")
    if observed.get("status") != job.get("status"):
        raise ValueError(f"job diagnostic status mismatch: {case_id}")
    _verify_privacy(observed, case_id=case_id)

    safe_job_error = _safe_error(job.get("error_code"))
    event_types = [str(item.get("event_type")) for item in events]
    classification = _classify(job.get("status"), safe_job_error, event_types)
    action = EXPECTED[case_id][1]
    if observed.get("classification") != classification or classification != expected[0]:
        raise ValueError(f"job diagnostic classification mismatch: {case_id}")
    if observed.get("recommended_action") != action:
        raise ValueError(f"job diagnostic action mismatch: {case_id}")
    if observed.get("observed_error_code") != safe_job_error:
        raise ValueError(f"job diagnostic error-code boundary mismatch: {case_id}")
    if observed.get("classification_basis") != _basis(
        str(job.get("status")), safe_job_error, event_types
    ):
        raise ValueError(f"job diagnostic classification basis mismatch: {case_id}")

    projections = [
        {
            "event_id": item.get("event_id"),
            "event_type": item.get("event_type"),
            "attempt": item.get("attempt"),
            "created_at": item.get("created_at"),
            "error_code": _safe_error(item.get("error_code")),
        }
        for item in events
    ]
    if observed.get("event_sequence_sha256") != _canonical_sha256(projections):
        raise ValueError(f"job diagnostic event chain mismatch: {case_id}")

    baseline = _timestamp(str(events[0]["created_at"]))
    significant = []
    for item in events:
        if item.get("event_type") == "heartbeat":
            continue
        significant.append(
            {
                "event_id": item.get("event_id"),
                "event_type": item.get("event_type"),
                "attempt": item.get("attempt"),
                "offset_ms": round(
                    max(
                        0.0,
                        (_timestamp(str(item["created_at"])) - baseline).total_seconds(),
                    )
                    * 1000,
                    3,
                ),
                "error_code": _safe_error(item.get("error_code")),
            }
        )
    if observed.get("significant_events") != significant:
        raise ValueError(f"job diagnostic significant-event mismatch: {case_id}")
    first_failure = next(
        (item for item in significant if item["event_type"] in FAILURE_SIGNALS),
        None,
    )
    terminal = next(
        (
            item
            for item in reversed(significant)
            if item["event_type"] == job.get("status") and job.get("status") in TERMINAL
        ),
        None,
    )
    first_claim = next(
        (item for item in significant if item["event_type"] == "claimed"),
        None,
    )
    expected_fields = {
        "event_count": len(events),
        "first_failure_event_id": (
            first_failure["event_id"] if first_failure is not None else None
        ),
        "terminal_event_id": terminal["event_id"] if terminal is not None else None,
        "retry_count": event_types.count("retry_scheduled"),
        "lease_reclaim_count": event_types.count("lease_reclaimed"),
        "heartbeat_count": event_types.count("heartbeat"),
        "queue_wait_ms": first_claim["offset_ms"] if first_claim is not None else None,
        "time_to_terminal_ms": terminal["offset_ms"] if terminal is not None else None,
        "replay_allowed": job.get("status") in {"failed", "dead_lettered", "timed_out"},
    }
    for field, value in expected_fields.items():
        if observed.get(field) != value:
            raise ValueError(f"job diagnostic {field} mismatch: {case_id}")
    unsigned = dict(observed)
    recorded_sha = unsigned.pop("artifact_sha256", None)
    if recorded_sha != _canonical_sha256(unsigned):
        raise ValueError(f"job diagnostic inner artifact SHA mismatch: {case_id}")


def _safe_error(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return "unclassified_error"
    return value if value in SAFE_ERROR_CODES else "unclassified_error"


def _classify(status: object, error: str | None, events: Sequence[str]) -> str:
    if status == "queued":
        return "retry_pending" if "retry_scheduled" in events else "in_progress"
    if status == "running":
        return "lease_recovery_in_progress" if "lease_reclaimed" in events else "in_progress"
    if status == "succeeded":
        return "completed"
    if status == "cancelled":
        return "cancelled"
    if status == "timed_out":
        return (
            "execution_deadline_exceeded"
            if "claimed" in events or "lease_reclaimed" in events
            else "queue_deadline_exceeded"
        )
    mapping = {
        "invalid_persisted_request": "persisted_request_invalid",
        "clarification_required": "clarification_required",
        "execution_budget_exceeded": "execution_budget_exceeded",
        "invalid_model_output": "model_output_rejected",
        "lease_expired_attempts_exhausted": "worker_lease_exhausted",
    }
    if error in mapping:
        return mapping[error]
    if status == "dead_lettered" and "lease_reclaimed" in events:
        return "worker_lease_exhausted"
    if error == "planning_execution_failed":
        return "runtime_or_dependency_unknown"
    return "unclassified_failure"


def _basis(status: str, error: str | None, events: Sequence[str]) -> list[str]:
    result = [f"status:{status}"]
    if error is not None:
        result.append(f"error_code:{error}")
    if "retry_scheduled" in events:
        result.append("event:retry_scheduled")
    if "lease_reclaimed" in events:
        result.append("event:lease_reclaimed")
    if status == "timed_out":
        result.append(
            "phase:execution"
            if "claimed" in events or "lease_reclaimed" in events
            else "phase:queue"
        )
    return result


def _timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


def _verify_privacy(value: Any, *, case_id: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False).lower()
    if "private" in serialized or "secret" in serialized:
        raise ValueError(f"job diagnostic private marker leaked: {case_id}")

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            overlap = FORBIDDEN_KEYS.intersection(item)
            if overlap:
                raise ValueError(f"job diagnostic forbidden keys leaked: {case_id}")
            for nested in item.values():
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)


def _metrics(raw_cases: list[dict[str, Any]]) -> dict[str, float | int]:
    count = len(raw_cases)
    classes = {item["observed"]["classification"] for item in raw_cases}
    return {
        "classification_accuracy_rate": sum(
            item["observed"]["classification"]
            == EXPECTED[item["case_id"]][0]
            for item in raw_cases
        )
        / count,
        "recommended_action_accuracy_rate": sum(
            item["observed"]["recommended_action"]
            == EXPECTED[item["case_id"]][1]
            for item in raw_cases
        )
        / count,
        "classification_coverage_count": len(classes),
        "integrity_rate": 1.0,
        "privacy_minimization_rate": 1.0,
        "unknown_error_non_promotion_rate": sum(
            item["observed"]["observed_error_code"] == "unclassified_error"
            for item in raw_cases
            if item["case_id"] == "unclassified-safe-redaction"
        ),
    }
