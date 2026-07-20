"""Submit/worker orchestration around the durable repository."""

from __future__ import annotations

import logging
import threading
import uuid

from application import (
    ConstraintNormalizer,
    ExecutionBudgetExceeded,
    ModelOutputContractError,
    PlanRequest,
    PlanningPreflight,
    PlanningClarificationRequired,
    PlanningCallbacks,
    PlanningCancelled,
    PlanningDeadlineExceeded,
    PlanningService,
    RequirementNormalizer,
)

from .diagnostics import (
    MAX_DIAGNOSTIC_EVENTS,
    JobDiagnosticEventLimitExceeded,
    JobIncidentDiagnosis,
)
from .models import PlanningAdmissionEvent, PlanningJob, PlanningJobEvent, PlanningJobSummary
from .repository import PlanningJobRepository


LOGGER = logging.getLogger(__name__)


class PlanningJobService:
    def __init__(
        self,
        *,
        repository: PlanningJobRepository | None = None,
        planning_service: PlanningService | None = None,
        max_attempts: int = 3,
        default_deadline_seconds: int = 900,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 60.0,
        requirement_normalizer: RequirementNormalizer | None = None,
        constraint_normalizer: ConstraintNormalizer | None = None,
    ) -> None:
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not 1 <= default_deadline_seconds <= 86400:
            raise ValueError("default_deadline_seconds must be between 1 and 86400")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must be non-negative")
        if retry_max_seconds < retry_base_seconds:
            raise ValueError("retry_max_seconds must be at least retry_base_seconds")
        self.repository = repository or PlanningJobRepository()
        self.planning_service = planning_service or PlanningService()
        self.max_attempts = max_attempts
        self.default_deadline_seconds = default_deadline_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        inherited_preflight = getattr(self.planning_service, "preflight_engine", None)
        if (
            inherited_preflight is not None
            and requirement_normalizer is None
            and constraint_normalizer is None
        ):
            self.preflight = inherited_preflight
        else:
            inherited_requirement = getattr(
                self.planning_service, "requirement_normalizer", None
            )
            inherited_constraint = getattr(
                self.planning_service, "constraint_normalizer", None
            )
            self.preflight = PlanningPreflight(
                requirement_normalizer=(
                    requirement_normalizer
                    or inherited_requirement
                    or RequirementNormalizer()
                ),
                constraint_normalizer=(
                    constraint_normalizer
                    or inherited_constraint
                    or ConstraintNormalizer()
                ),
            )
        # Compatibility for adapters/tests that introspect the old attribute.
        self.requirement_normalizer = self.preflight.requirement_normalizer
        self.constraint_normalizer = self.preflight.constraint_normalizer

    def submit(
        self,
        *,
        request: PlanRequest,
        request_id: str,
        tenant_id: str = "default",
        submitted_by: str = "system",
        idempotency_key: str | None = None,
        priority: int = 0,
        deadline_seconds: int | None = None,
        tenant_active_job_limit: int | None = None,
        tenant_submission_limit_per_minute: int | None = None,
    ) -> PlanningJob:
        resolved_deadline = (
            self.default_deadline_seconds
            if deadline_seconds is None
            else deadline_seconds
        )
        if not 1 <= resolved_deadline <= 86400:
            raise ValueError("deadline_seconds must be between 1 and 86400")
        request = self.preflight.normalize(request).request
        return self.repository.submit(
            request_id=request_id,
            request_payload=request.to_dict(),
            tenant_id=tenant_id,
            submitted_by=submitted_by,
            idempotency_key=idempotency_key,
            max_attempts=self.max_attempts,
            priority=priority,
            deadline_seconds=resolved_deadline,
            tenant_active_job_limit=tenant_active_job_limit,
            tenant_submission_limit_per_minute=(
                tenant_submission_limit_per_minute
            ),
        )

    def get(self, job_id: str, *, tenant_id: str | None = None) -> PlanningJob | None:
        return self.repository.get(job_id, tenant_id=tenant_id)

    def list_jobs(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        after_job_id: str | None = None,
        limit: int = 100,
    ) -> tuple[PlanningJobSummary, ...]:
        return self.repository.list_jobs(
            tenant_id=tenant_id,
            status=status,
            after_job_id=after_job_id,
            limit=limit,
        )

    def cancel(
        self,
        *,
        job_id: str,
        reason_code: str,
        tenant_id: str | None = None,
    ) -> PlanningJob:
        return self.repository.request_cancel(
            job_id=job_id,
            reason_code=reason_code,
            tenant_id=tenant_id,
        )

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
    ) -> PlanningJob:
        return self.repository.replay(
            job_id=job_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            submitted_by=submitted_by,
            tenant_active_job_limit=tenant_active_job_limit,
            tenant_submission_limit_per_minute=(
                tenant_submission_limit_per_minute
            ),
        )

    def events(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> tuple[PlanningJobEvent, ...]:
        return self.repository.list_events(
            job_id,
            tenant_id=tenant_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def diagnose(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
    ) -> JobIncidentDiagnosis | None:
        job = self.repository.get(job_id, tenant_id=tenant_id)
        if job is None:
            return None
        events = self.repository.list_events(
            job_id,
            tenant_id=tenant_id,
            limit=MAX_DIAGNOSTIC_EVENTS,
        )
        if len(events) == MAX_DIAGNOSTIC_EVENTS:
            overflow = self.repository.list_events(
                job_id,
                tenant_id=tenant_id,
                after_event_id=events[-1].event_id,
                limit=1,
            )
            if overflow:
                raise JobDiagnosticEventLimitExceeded(
                    "job diagnosis event limit exceeded; durable event chain was not truncated"
                )
        return JobIncidentDiagnosis.create(job=job, events=events)

    def admission_events(
        self,
        *,
        tenant_id: str,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> tuple[PlanningAdmissionEvent, ...]:
        return self.repository.list_admission_events(
            tenant_id=tenant_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def run_once(
        self,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 300,
        heartbeat_interval_seconds: float | None = None,
    ) -> PlanningJob | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        if heartbeat_interval_seconds is not None and heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        interval = heartbeat_interval_seconds or min(30.0, max(0.05, lease_seconds / 3))
        if interval >= lease_seconds:
            raise ValueError("heartbeat_interval_seconds must be shorter than the lease")
        resolved_worker = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        job = self.repository.claim_next(worker_id=resolved_worker, lease_seconds=lease_seconds)
        if job is None:
            return None

        stop_heartbeat = threading.Event()
        heartbeat_errors: list[Exception] = []
        heartbeat_terminal: list[PlanningJob] = []

        def maintain_lease() -> None:
            while not stop_heartbeat.wait(interval):
                try:
                    heartbeat = self.repository.heartbeat(
                        job_id=job.job_id,
                        worker_id=resolved_worker,
                        lease_seconds=lease_seconds,
                    )
                    if heartbeat.status in {"cancelled", "timed_out"}:
                        heartbeat_terminal.append(heartbeat)
                        return
                except Exception as exc:
                    heartbeat_errors.append(exc)
                    LOGGER.exception(
                        "planning job heartbeat failed",
                        extra={"job_id": job.job_id, "worker_id": resolved_worker},
                    )
                    return

        heartbeat_thread = threading.Thread(
            target=maintain_lease,
            name=f"bj-pal-heartbeat-{job.job_id[-8:]}",
            daemon=True,
        )
        heartbeat_thread.start()
        request_error: Exception | None = None
        control_observed = False
        execution_error: Exception | None = None
        budget_error: ExecutionBudgetExceeded | None = None
        model_output_error: ModelOutputContractError | None = None
        clarification_error: PlanningClarificationRequired | None = None

        def stop_reason() -> str | None:
            return self.repository.pending_stop_reason(job.job_id)

        try:
            request = PlanRequest.from_dict(job.request_payload)
        except (TypeError, ValueError) as exc:
            request_error = exc
        if request_error is None:
            try:
                request = self.preflight.normalize(request).request
            except PlanningClarificationRequired as exc:
                clarification_error = exc
        try:
            if request_error is None and clarification_error is None:
                result = self.planning_service.execute(
                    request,
                    callbacks=PlanningCallbacks(
                        should_cancel=lambda: stop_reason() == "cancelled",
                        should_timeout=lambda: stop_reason() == "timed_out",
                        correlation_id=job.job_id,
                    ),
                )
        except (PlanningCancelled, PlanningDeadlineExceeded):
            control_observed = True
        except ExecutionBudgetExceeded as exc:
            budget_error = exc
        except ModelOutputContractError as exc:
            model_output_error = exc
        except PlanningClarificationRequired as exc:
            clarification_error = exc
        except Exception as exc:
            execution_error = exc
            LOGGER.warning(
                "planning job execution failed; durable retry policy will apply",
                extra={"job_id": job.job_id, "error_type": type(exc).__name__},
            )
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join()

        if heartbeat_errors:
            raise RuntimeError("worker lost the durable job lease during execution") from heartbeat_errors[0]

        if heartbeat_terminal:
            return heartbeat_terminal[-1]

        if control_observed:
            return self.repository.finalize_stopped(
                job_id=job.job_id,
                worker_id=resolved_worker,
            )

        if request_error is not None:
            return self.repository.fail(
                job_id=job.job_id,
                worker_id=resolved_worker,
                error_code="invalid_persisted_request",
                error_message="The persisted planning request is invalid.",
            )

        if clarification_error is not None:
            return self.repository.fail(
                job_id=job.job_id,
                worker_id=resolved_worker,
                error_code="clarification_required",
                error_message="The persisted planning request needs clarification.",
            )

        if budget_error is not None:
            return self.repository.fail(
                job_id=job.job_id,
                worker_id=resolved_worker,
                error_code=budget_error.code,
                error_message=(
                    "The server execution budget stopped this planning job "
                    f"({budget_error.snapshot.termination_reason})."
                ),
            )

        if model_output_error is not None:
            return self.repository.fail(
                job_id=job.job_id,
                worker_id=resolved_worker,
                error_code=model_output_error.code,
                error_message="The model could not produce a valid grounded plan.",
            )

        if execution_error is not None:
            backoff_seconds = min(
                self.retry_max_seconds,
                self.retry_base_seconds * (2 ** max(0, job.attempt - 1)),
            )
            return self.repository.retry_or_dead_letter(
                job_id=job.job_id,
                worker_id=resolved_worker,
                error_code="planning_execution_failed",
                error_message="Planning execution failed.",
                backoff_seconds=backoff_seconds,
            )
        return self.repository.succeed(
            job_id=job.job_id,
            worker_id=resolved_worker,
            result_payload=result.to_dict(),
        )
