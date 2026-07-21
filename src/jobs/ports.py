"""Backend-neutral contract for the durable planning-job store."""

from __future__ import annotations

from typing import Protocol

from .models import (
    PlanningAdmissionEvent,
    PlanningJob,
    PlanningJobEvent,
    PlanningJobSummary,
    PlanningJobWindowEvidence,
)


class PlanningJobStore(Protocol):
    """Persistence boundary consumed by :class:`PlanningJobService`.

    Implementations must make every state transition and its corresponding
    append-only event atomic.  A worker lease is a fencing proof: only the
    current owner may heartbeat or finish a running job before expiry.
    """

    def probe(self) -> bool: ...

    def close(self) -> None: ...

    def submit(
        self,
        *,
        request_id: str,
        request_payload: dict,
        tenant_id: str = "default",
        submitted_by: str = "system",
        idempotency_key: str | None = None,
        max_attempts: int = 3,
        priority: int = 0,
        deadline_seconds: int = 900,
        tenant_active_job_limit: int | None = None,
        tenant_submission_limit_per_minute: int | None = None,
    ) -> PlanningJob: ...

    def get(self, job_id: str, *, tenant_id: str | None = None) -> PlanningJob | None: ...

    def list_jobs(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        after_job_id: str | None = None,
        limit: int = 100,
    ) -> tuple[PlanningJobSummary, ...]: ...

    def request_cancel(
        self,
        *,
        job_id: str,
        reason_code: str,
        tenant_id: str | None = None,
    ) -> PlanningJob: ...

    def finalize_stopped(self, *, job_id: str, worker_id: str) -> PlanningJob: ...

    def finalize_cancelled(self, *, job_id: str, worker_id: str) -> PlanningJob: ...

    def pending_stop_reason(self, job_id: str) -> str | None: ...

    def replay(
        self,
        *,
        job_id: str,
        request_id: str,
        idempotency_key: str,
        tenant_id: str | None = None,
        submitted_by: str = "system",
        tenant_active_job_limit: int | None = None,
        tenant_submission_limit_per_minute: int | None = None,
    ) -> PlanningJob: ...

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> PlanningJob | None: ...

    def heartbeat(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> PlanningJob: ...

    def retry_or_dead_letter(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        backoff_seconds: float,
    ) -> PlanningJob: ...

    def succeed(
        self,
        *,
        job_id: str,
        worker_id: str,
        result_payload: dict,
    ) -> PlanningJob: ...

    def fail(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
    ) -> PlanningJob: ...

    def list_events(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> tuple[PlanningJobEvent, ...]: ...

    def workload_evidence(
        self,
        *,
        tenant_id: str,
        window_start: str,
        window_end: str,
    ) -> tuple[PlanningJobWindowEvidence, ...]: ...

    def list_admission_events(
        self,
        *,
        tenant_id: str,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> tuple[PlanningAdmissionEvent, ...]: ...
