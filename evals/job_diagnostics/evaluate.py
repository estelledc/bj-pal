"""Exercise every v1 durable-job diagnostic class with fixed synthetic evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from jobs import JobIncidentDiagnosis, PlanningJob, PlanningJobEvent


@dataclass(frozen=True)
class DiagnosticCase:
    case_id: str
    status: str
    error_code: str | None
    event_types: tuple[str, ...]
    expected_classification: str
    expected_action: str
    terminal_attempt: int = 1


CASES = (
    DiagnosticCase(
        "active-first-attempt",
        "queued",
        None,
        ("submitted",),
        "in_progress",
        "wait_for_worker",
        0,
    ),
    DiagnosticCase(
        "retry-pending",
        "queued",
        "planning_execution_failed",
        ("submitted", "claimed", "retry_scheduled"),
        "retry_pending",
        "wait_for_scheduled_retry",
    ),
    DiagnosticCase(
        "lease-recovery",
        "running",
        None,
        ("submitted", "claimed", "lease_reclaimed"),
        "lease_recovery_in_progress",
        "monitor_reclaimed_worker",
        2,
    ),
    DiagnosticCase(
        "completed",
        "succeeded",
        None,
        ("submitted", "claimed", "heartbeat", "succeeded"),
        "completed",
        "none",
    ),
    DiagnosticCase(
        "cancelled",
        "cancelled",
        None,
        ("submitted", "cancelled"),
        "cancelled",
        "none",
        0,
    ),
    DiagnosticCase(
        "queue-timeout",
        "timed_out",
        "job_deadline_exceeded",
        ("submitted", "timed_out"),
        "queue_deadline_exceeded",
        "review_deadline_and_queue_capacity_before_replay",
        0,
    ),
    DiagnosticCase(
        "execution-timeout",
        "timed_out",
        "job_deadline_exceeded",
        ("submitted", "claimed", "heartbeat", "timed_out"),
        "execution_deadline_exceeded",
        "review_deadline_and_queue_capacity_before_replay",
    ),
    DiagnosticCase(
        "invalid-persisted-request",
        "failed",
        "invalid_persisted_request",
        ("submitted", "claimed", "failed"),
        "persisted_request_invalid",
        "inspect_persisted_request_migration",
    ),
    DiagnosticCase(
        "clarification-required",
        "failed",
        "clarification_required",
        ("submitted", "claimed", "failed"),
        "clarification_required",
        "resubmit_with_clarification",
    ),
    DiagnosticCase(
        "execution-budget",
        "failed",
        "execution_budget_exceeded",
        ("submitted", "claimed", "failed"),
        "execution_budget_exceeded",
        "reduce_work_or_adjust_server_budget",
    ),
    DiagnosticCase(
        "model-output",
        "failed",
        "invalid_model_output",
        ("submitted", "claimed", "failed"),
        "model_output_rejected",
        "inspect_model_output_contract_cases",
    ),
    DiagnosticCase(
        "worker-lease-exhausted",
        "dead_lettered",
        "lease_expired_attempts_exhausted",
        ("submitted", "claimed", "lease_reclaimed", "dead_lettered"),
        "worker_lease_exhausted",
        "inspect_worker_health_before_replay",
        2,
    ),
    DiagnosticCase(
        "runtime-unknown",
        "dead_lettered",
        "planning_execution_failed",
        ("submitted", "claimed", "retry_scheduled", "claimed", "dead_lettered"),
        "runtime_or_dependency_unknown",
        "inspect_dependency_health_before_replay",
        2,
    ),
    DiagnosticCase(
        "unclassified-safe-redaction",
        "failed",
        "private-provider-hostname",
        ("submitted", "claimed", "failed"),
        "unclassified_failure",
        "manual_review",
    ),
)


def _job(case: DiagnosticCase) -> PlanningJob:
    return PlanningJob(
        job_id="job-" + hashlib.sha256(case.case_id.encode()).hexdigest()[:32],
        request_id="private-request-marker",
        tenant_id="private-tenant-marker",
        submitted_by="private-principal-marker",
        status=case.status,
        request_payload={"user_input": "private-user-input-marker"},
        request_sha256="a" * 64,
        idempotency_key=None,
        attempt=case.terminal_attempt,
        max_attempts=3,
        priority=0,
        deadline_seconds=900,
        deadline_at="2026-07-20T00:15:00.000Z",
        available_at="2026-07-20T00:00:00.000Z",
        created_at="2026-07-20T00:00:00.000Z",
        updated_at="2026-07-20T00:00:10.000Z",
        error_code=case.error_code,
        error_message="private-provider-error-marker",
    )


def _events(case: DiagnosticCase, job_id: str) -> tuple[PlanningJobEvent, ...]:
    events = []
    attempt = 0
    for event_id, event_type in enumerate(case.event_types, start=1):
        if event_type in {"claimed", "lease_reclaimed"}:
            attempt += 1
        payload: dict[str, Any] = {"private": "private-event-payload-marker"}
        if event_type in {"retry_scheduled", "failed", "dead_lettered", "timed_out"}:
            payload["error_code"] = case.error_code
        events.append(
            PlanningJobEvent(
                event_id=event_id,
                job_id=job_id,
                event_type=event_type,
                attempt=attempt,
                worker_id="private-worker-marker",
                payload=payload,
                created_at=f"2026-07-20T00:00:{event_id:02d}.000Z",
            )
        )
    return tuple(events)


def evaluate_job_diagnostics() -> dict[str, Any]:
    raw_cases = []
    for case in CASES:
        job = _job(case)
        events = _events(case, job.job_id)
        diagnosis = JobIncidentDiagnosis.create(job=job, events=events)
        raw_cases.append(
            {
                "case_id": case.case_id,
                "input": {
                    "job": {
                        "job_id": job.job_id,
                        "status": job.status,
                        "attempt": job.attempt,
                        "max_attempts": job.max_attempts,
                        "error_code": job.error_code,
                    },
                    "events": [
                        {
                            "event_id": item.event_id,
                            "event_type": item.event_type,
                            "attempt": item.attempt,
                            "created_at": item.created_at,
                            "error_code": item.payload.get("error_code"),
                        }
                        for item in events
                    ],
                },
                "expected": {
                    "classification": case.expected_classification,
                    "recommended_action": case.expected_action,
                },
                "observed": diagnosis.to_dict(),
            }
        )
    return {
        "case_count": len(raw_cases),
        "raw_cases": raw_cases,
        "metrics": recompute_metrics(raw_cases),
    }


def recompute_metrics(raw_cases: list[dict[str, Any]]) -> dict[str, float | int]:
    count = len(raw_cases)
    if count == 0:
        raise ValueError("job diagnostic metrics require cases")
    observed_classes = {item["observed"]["classification"] for item in raw_cases}
    privacy_passes = sum(
        "private" not in json.dumps(item["observed"], ensure_ascii=False)
        for item in raw_cases
    )
    return {
        "classification_accuracy_rate": sum(
            item["observed"]["classification"]
            == item["expected"]["classification"]
            for item in raw_cases
        )
        / count,
        "recommended_action_accuracy_rate": sum(
            item["observed"]["recommended_action"]
            == item["expected"]["recommended_action"]
            for item in raw_cases
        )
        / count,
        "classification_coverage_count": len(observed_classes),
        "integrity_rate": sum(
            len(item["observed"]["artifact_sha256"]) == 64 for item in raw_cases
        )
        / count,
        "privacy_minimization_rate": privacy_passes / count,
        "unknown_error_non_promotion_rate": sum(
            item["observed"]["observed_error_code"] == "unclassified_error"
            for item in raw_cases
            if item["case_id"] == "unclassified-safe-redaction"
        ),
    }
