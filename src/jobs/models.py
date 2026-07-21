"""Typed durable job state, separate from HTTP request and plan artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


JobStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "dead_lettered",
    "cancelled",
    "timed_out",
]
JobEventType = Literal[
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
]
AdmissionDecision = Literal["admitted", "rejected", "idempotent_reuse"]
AdmissionOperation = Literal["submit", "replay"]


@dataclass(frozen=True)
class PlanningJob:
    job_id: str
    request_id: str
    tenant_id: str
    submitted_by: str
    status: JobStatus
    request_payload: dict
    request_sha256: str
    idempotency_key: str | None
    attempt: int
    max_attempts: int
    priority: int
    deadline_seconds: int
    deadline_at: str | None
    available_at: str
    created_at: str
    updated_at: str
    cancel_requested_at: str | None = None
    cancelled_at: str | None = None
    cancel_reason_code: str | None = None
    replayed_from_job_id: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    artifact_id: str | None = None
    artifact_sha256: str | None = None
    result_payload: dict | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PlanningJobEvent:
    event_id: int
    job_id: str
    event_type: JobEventType
    attempt: int
    worker_id: str | None
    payload: dict
    created_at: str


@dataclass(frozen=True)
class PlanningAdmissionEvent:
    event_id: int
    policy_version: str
    tenant_id: str
    submitted_by: str
    request_id: str
    operation: AdmissionOperation
    decision: AdmissionDecision
    reason_code: str | None
    job_id: str | None
    idempotency_key_present: bool
    active_jobs_before: int
    recent_submissions_before: int
    active_job_limit: int | None
    submission_limit_per_minute: int | None
    submission_window_seconds: int
    retry_after_seconds: int | None
    created_at: str


@dataclass(frozen=True)
class PlanningJobSummary:
    """Lightweight control-plane view that never loads request or result JSON blobs."""

    job_id: str
    request_id: str
    tenant_id: str
    submitted_by: str
    status: JobStatus
    attempt: int
    max_attempts: int
    priority: int
    deadline_seconds: int
    deadline_at: str | None
    available_at: str
    created_at: str
    updated_at: str
    cancel_requested_at: str | None
    cancelled_at: str | None
    cancel_reason_code: str | None
    replayed_from_job_id: str | None
    artifact_id: str | None
    error_code: str | None
