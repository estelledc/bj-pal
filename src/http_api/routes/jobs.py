"""Durable planning-job control-plane HTTP routes."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from typing import Iterator, Protocol

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from application import PlanRequest, PlanningClarificationRequired
from clarifications import (
    ClarificationContinuationService,
    ClarificationExpired,
    ClarificationInProgress,
    ClarificationIntegrityError,
    ClarificationNotFound,
    ClarificationResolutionConflict,
    InvalidClarificationTransition,
)
from jobs import (
    DurableWorkloadHealth,
    IdempotencyConflict,
    InvalidJobTransition,
    JobDiagnosticEventLimitExceeded,
    JobIncidentDiagnosis,
    JobNotFound,
    JobWorkloadEvidenceLimitExceeded,
    PlanningAdmissionEvent,
    PlanningJob,
    PlanningJobEvent,
    PlanningJobSummary,
    SUBMISSION_RATE_WINDOW_SECONDS,
    TenantAdmissionRejected,
)
from jobs.workload_health import validate_closed_window

from ..auth import ControlPrincipal
from ..responses import REQUEST_ID_PATTERN, error_response, request_id
from ..schemas import (
    ClarificationContinueRequest,
    DurableWorkloadHealthResponse,
    ErrorResponse,
    JobIncidentDiagnosisResponse,
    PlanningAdmissionEventResponse,
    PlanningAdmissionEventsResponse,
    PlanningJobCancelRequest,
    PlanningJobEventResponse,
    PlanningJobEventsResponse,
    PlanningJobListItemResponse,
    PlanningJobListResponse,
    PlanningJobResponse,
    PlanningJobStatus,
    PlanningJobSubmitRequest,
)
from ..sse import (
    TERMINAL_JOB_STATUSES,
    encode_job_event,
    encode_stream_error,
    encode_stream_timeout,
)


LOGGER = logging.getLogger(__name__)
AuthorizationDependency = Callable[..., ControlPrincipal]
ClarificationErrorResponder = Callable[[Request, Exception], JSONResponse]
ClarificationIssuer = Callable[..., dict]
ClarificationServiceProvider = Callable[[], ClarificationContinuationService]
JobServiceProvider = Callable[[], "PlanningJobExecutor"]


class PlanningJobExecutor(Protocol):
    def probe(self) -> bool: ...

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

    def cancel(
        self,
        *,
        job_id: str,
        reason_code: str,
        tenant_id: str | None = None,
    ) -> PlanningJob: ...

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

    def events(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> tuple[PlanningJobEvent, ...]: ...

    def diagnose(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
    ) -> JobIncidentDiagnosis | None: ...

    def workload_health(
        self,
        *,
        tenant_id: str,
        window_start: str,
        window_end: str,
    ) -> DurableWorkloadHealth: ...

    def admission_events(
        self,
        *,
        tenant_id: str,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> tuple[PlanningAdmissionEvent, ...]: ...


def build_jobs_router(
    *,
    jobs: JobServiceProvider,
    clarifications: ClarificationServiceProvider,
    issue_clarification: ClarificationIssuer,
    clarification_error_response: ClarificationErrorResponder,
    require_submit_auth: AuthorizationDependency,
    require_read_auth: AuthorizationDependency,
    require_control_auth: AuthorizationDependency,
    require_replay_auth: AuthorizationDependency,
) -> APIRouter:
    """Build durable-job routes from explicit runtime dependencies."""
    router = APIRouter()

    def job_response(job: PlanningJob) -> PlanningJobResponse:
        return PlanningJobResponse.model_validate(
            {
                "job_id": job.job_id,
                "request_id": job.request_id,
                "tenant_id": job.tenant_id,
                "submitted_by": job.submitted_by,
                "status": job.status,
                "attempt": job.attempt,
                "max_attempts": job.max_attempts,
                "priority": job.priority,
                "deadline_seconds": job.deadline_seconds,
                "deadline_at": job.deadline_at,
                "available_at": job.available_at,
                "lease_expires_at": job.lease_expires_at,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "cancel_requested_at": job.cancel_requested_at,
                "cancelled_at": job.cancelled_at,
                "cancel_reason_code": job.cancel_reason_code,
                "replayed_from_job_id": job.replayed_from_job_id,
                "artifact_id": job.artifact_id,
                "artifact_sha256": job.artifact_sha256,
                "result": job.result_payload,
                "error_code": job.error_code,
                "error_message": job.error_message,
                "links": {
                    "self": f"/v1/planning-jobs/{job.job_id}",
                    "diagnosis": f"/v1/planning-jobs/{job.job_id}/diagnosis",
                    "events": f"/v1/planning-jobs/{job.job_id}/events",
                    "event_stream": f"/v1/planning-jobs/{job.job_id}/events/stream",
                    "cancel": f"/v1/planning-jobs/{job.job_id}/cancel",
                    "replay": f"/v1/planning-jobs/{job.job_id}/replay",
                },
            }
        )

    def job_events_response(
        job_id: str,
        events: tuple[PlanningJobEvent, ...],
        after_event_id: int,
    ) -> PlanningJobEventsResponse:
        next_cursor = events[-1].event_id if events else after_event_id
        return PlanningJobEventsResponse(
            job_id=job_id,
            events=[PlanningJobEventResponse.model_validate(event.__dict__) for event in events],
            next_after_event_id=next_cursor,
            links={
                "job": f"/v1/planning-jobs/{job_id}",
                "next": f"/v1/planning-jobs/{job_id}/events?after_event_id={next_cursor}",
                "stream": f"/v1/planning-jobs/{job_id}/events/stream?after_event_id={next_cursor}",
            },
        )

    def admission_events_response(
        events: tuple[PlanningAdmissionEvent, ...],
        *,
        after_event_id: int,
        limit: int,
    ) -> PlanningAdmissionEventsResponse:
        next_cursor = events[-1].event_id if events else after_event_id
        self_link = (
            f"/v1/planning-admission-events?after_event_id={after_event_id}&limit={limit}"
        )
        return PlanningAdmissionEventsResponse(
            events=[
                PlanningAdmissionEventResponse.model_validate(event.__dict__)
                for event in events
            ],
            next_after_event_id=next_cursor,
            links={
                "self": self_link,
                "next": (
                    "/v1/planning-admission-events"
                    f"?after_event_id={next_cursor}&limit={limit}"
                ),
            },
        )

    def admission_rejected_response(
        request: Request,
        exc: TenantAdmissionRejected,
    ) -> JSONResponse:
        response = error_response(
            request,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code=exc.code,
            message=exc.message,
            details={
                "active_jobs": exc.active_jobs,
                "recent_submissions": exc.recent_submissions,
                "active_job_limit": exc.active_job_limit,
                "submission_limit_per_minute": (
                    exc.submission_limit_per_minute
                ),
                "submission_window_seconds": SUBMISSION_RATE_WINDOW_SECONDS,
                "retry_after_seconds": exc.retry_after_seconds,
            },
        )
        if exc.retry_after_seconds is not None:
            response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response

    def job_list_item(job: PlanningJobSummary) -> PlanningJobListItemResponse:
        return PlanningJobListItemResponse.model_validate(
            {
                "job_id": job.job_id,
                "request_id": job.request_id,
                "tenant_id": job.tenant_id,
                "submitted_by": job.submitted_by,
                "status": job.status,
                "attempt": job.attempt,
                "max_attempts": job.max_attempts,
                "priority": job.priority,
                "deadline_seconds": job.deadline_seconds,
                "deadline_at": job.deadline_at,
                "available_at": job.available_at,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "cancel_requested_at": job.cancel_requested_at,
                "cancelled_at": job.cancelled_at,
                "cancel_reason_code": job.cancel_reason_code,
                "replayed_from_job_id": job.replayed_from_job_id,
                "artifact_id": job.artifact_id,
                "error_code": job.error_code,
                "links": {"self": f"/v1/planning-jobs/{job.job_id}"},
            }
        )

    def job_list_response(
        items: tuple[PlanningJobSummary, ...],
        *,
        job_status: str | None,
        limit: int,
    ) -> PlanningJobListResponse:
        next_cursor = items[-1].job_id if items else None
        self_link = f"/v1/planning-jobs?limit={limit}"
        if job_status is not None:
            self_link += f"&status={job_status}"
        links = {"self": self_link}
        if next_cursor is not None:
            links["next"] = f"{self_link}&after_job_id={next_cursor}"
        return PlanningJobListResponse(
            jobs=[job_list_item(item) for item in items],
            next_after_job_id=next_cursor,
            links=links,
        )
    @router.post(
        "/v1/planning-jobs",
        response_model=PlanningJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            429: {"model": ErrorResponse, "description": "Tenant admission rejected"},
            409: {
                "model": ErrorResponse,
                "description": "Clarification required or idempotency conflict",
            },
            422: {"model": ErrorResponse, "description": "Invalid planning request"},
            503: {"model": ErrorResponse, "description": "Durable job store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Persist a planning job for an independent worker",
    )
    def submit_planning_job(
        payload: PlanningJobSubmitRequest,
        request: Request,
        principal: ControlPrincipal = Depends(require_submit_auth),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        principal.require_priority(payload.priority)
        if idempotency_key is not None and not REQUEST_ID_PATTERN.fullmatch(idempotency_key):
            return error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_idempotency_key",
                message="Idempotency-Key must contain 1-128 safe characters.",
            )
        application_request = payload.to_application_request()
        try:
            job = jobs().submit(
                request=application_request,
                request_id=request_id(request),
                tenant_id=principal.tenant_id,
                submitted_by=principal.principal_id,
                idempotency_key=idempotency_key,
                priority=payload.priority,
                deadline_seconds=payload.deadline_seconds,
                tenant_active_job_limit=principal.tenant_active_job_limit,
                tenant_submission_limit_per_minute=(
                    principal.tenant_submission_limit_per_minute
                ),
            )
        except PlanningClarificationRequired as exc:
            try:
                details = issue_clarification(
                    application_request=application_request,
                    error=exc,
                    delivery="job",
                    job_policy={
                        "deadline_seconds": payload.deadline_seconds,
                        "priority": payload.priority,
                        "tenant_id": principal.tenant_id,
                        "requested_by": principal.principal_id,
                    },
                )
            except (OSError, RuntimeError, sqlite3.Error):
                LOGGER.exception(
                    "job clarification continuation persistence failed",
                    extra={"request_id": request_id(request)},
                )
                return error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="clarification_store_unavailable",
                    message="The clarification continuation store is unavailable.",
                )
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="clarification_required",
                message="The planning request needs one clarification before it can be queued.",
                details=details,
            )
        except TenantAdmissionRejected as exc:
            return admission_rejected_response(request, exc)
        except IdempotencyConflict:
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="idempotency_conflict",
                message="The idempotency key is already associated with another request.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job submission failed", extra={"request_id": request_id(request)})
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        return job_response(job)

    @router.post(
        "/v1/clarifications/{continuation_id}/planning-job",
        response_model=PlanningJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse, "description": "Continuation not found"},
            429: {"model": ErrorResponse, "description": "Tenant admission rejected"},
            409: {"model": ErrorResponse, "description": "Resolution conflict or in progress"},
            410: {"model": ErrorResponse, "description": "Continuation expired"},
            422: {"model": ErrorResponse, "description": "Invalid resolution"},
            500: {"model": ErrorResponse, "description": "Invalid continuation artifact"},
            503: {"model": ErrorResponse, "description": "Job or continuation store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Resolve one clarification and enqueue the original durable request",
    )
    def continue_planning_job(
        continuation_id: str,
        payload: ClarificationContinueRequest,
        request: Request,
        principal: ControlPrincipal = Depends(require_submit_auth),
    ):
        try:
            existing_session = clarifications().get(continuation_id)
        except (OSError, RuntimeError, sqlite3.Error):
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="clarification_store_unavailable",
                message="The clarification continuation store is unavailable.",
            )
        if existing_session is not None:
            session_tenant = str(existing_session.job_policy.get("tenant_id", "default"))
            if session_tenant != principal.tenant_id:
                return error_response(
                    request,
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="clarification_not_found",
                    message="The clarification continuation was not found.",
                )
            principal.require_priority(int(existing_session.job_policy.get("priority", 0)))
        owner = f"job-{uuid.uuid4().hex}"
        try:
            session, resolved_request = clarifications().resolve_request(
                continuation_id=continuation_id,
                delivery="job",
                option_id=payload.option_id,
                answer=payload.answer,
            )
            session = clarifications().claim_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
        except (
            ClarificationNotFound,
            ClarificationExpired,
            ClarificationIntegrityError,
            ClarificationInProgress,
            ClarificationResolutionConflict,
            InvalidClarificationTransition,
            ValueError,
        ) as exc:
            return clarification_error_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="clarification_store_unavailable",
                message="The clarification continuation store is unavailable.",
            )

        if session.status == "completed":
            next_clarification = (session.result_payload or {}).get("next_clarification")
            if isinstance(next_clarification, dict):
                return error_response(
                    request,
                    status_code=status.HTTP_409_CONFLICT,
                    code="clarification_required",
                    message="One additional clarification is required before the job can be queued.",
                    details=next_clarification,
                )
            job_id = str((session.result_payload or {}).get("job_id") or "")
            job = jobs().get(job_id, tenant_id=principal.tenant_id)
            if job is None:
                return error_response(
                    request,
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="invalid_clarification_artifact",
                    message="The clarification continuation job artifact is invalid.",
                )
            return job_response(job)

        try:
            job = jobs().submit(
                request=resolved_request,
                request_id=request_id(request),
                tenant_id=principal.tenant_id,
                submitted_by=principal.principal_id,
                idempotency_key=f"clarification:{continuation_id}",
                priority=int(session.job_policy.get("priority", 0)),
                deadline_seconds=int(session.job_policy.get("deadline_seconds", 900)),
                tenant_active_job_limit=principal.tenant_active_job_limit,
                tenant_submission_limit_per_minute=(
                    principal.tenant_submission_limit_per_minute
                ),
            )
        except PlanningClarificationRequired as exc:
            try:
                details = issue_clarification(
                    application_request=resolved_request,
                    error=exc,
                    delivery="job",
                    job_policy=session.job_policy,
                )
                clarifications().complete(
                    continuation_id=continuation_id,
                    owner=owner,
                    result_payload={"next_clarification": details},
                )
            except (OSError, RuntimeError, sqlite3.Error):
                clarifications().release_execution(
                    continuation_id=continuation_id,
                    owner=owner,
                )
                return error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="clarification_store_unavailable",
                    message="The next clarification could not be persisted.",
                )
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="clarification_required",
                message="One additional clarification is required before the job can be queued.",
                details=details,
            )
        except IdempotencyConflict:
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="idempotency_conflict",
                message="The clarification continuation conflicts with an existing job.",
            )
        except TenantAdmissionRejected as exc:
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return admission_rejected_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )

        try:
            clarifications().complete(
                continuation_id=continuation_id,
                owner=owner,
                result_payload={"job_id": job.job_id},
            )
        except (OSError, RuntimeError, sqlite3.Error):
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="clarification_store_unavailable",
                message="The clarification job reference could not be persisted.",
            )
        return job_response(job)

    @router.get(
        "/v1/planning-jobs",
        response_model=PlanningJobListResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Planning job cursor not found"},
            503: {"model": ErrorResponse, "description": "Durable job store unavailable"},
        },
        tags=["planning-jobs"],
        summary="List durable jobs, including dead letters, with a stable cursor",
    )
    def list_planning_jobs(
        request: Request,
        principal: ControlPrincipal = Depends(require_read_auth),
        job_status: PlanningJobStatus | None = Query(default=None, alias="status"),
        after_job_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        if after_job_id is not None and not re.fullmatch(r"job-[a-f0-9]{32}", after_job_id):
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_cursor_not_found",
                message="The planning job cursor was not found.",
            )
        try:
            items = jobs().list_jobs(
                tenant_id=principal.tenant_id,
                status=job_status,
                after_job_id=after_job_id,
                limit=limit,
            )
        except JobNotFound:
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_cursor_not_found",
                message="The planning job cursor was not found.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job listing failed", extra={"request_id": request_id(request)})
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        return job_list_response(items, job_status=job_status, limit=limit)

    @router.get(
        "/v1/planning-job-health",
        response_model=DurableWorkloadHealthResponse,
        responses={
            400: {"model": ErrorResponse, "description": "Invalid metrics window"},
            409: {
                "model": ErrorResponse,
                "description": "Bounded evidence limit exceeded",
            },
            500: {"model": ErrorResponse, "description": "Invalid workload evidence"},
            503: {"model": ErrorResponse, "description": "Durable job store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Aggregate a tenant-scoped durable workload window",
    )
    def get_planning_job_health(
        request: Request,
        principal: ControlPrincipal = Depends(require_read_auth),
        window_start: str = Query(min_length=20, max_length=64),
        window_end: str = Query(min_length=20, max_length=64),
    ):
        try:
            validate_closed_window(window_start, window_end)
        except ValueError as exc:
            return error_response(
                request,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid_workload_window",
                message=str(exc),
            )
        try:
            snapshot = jobs().workload_health(
                tenant_id=principal.tenant_id,
                window_start=window_start,
                window_end=window_end,
            )
        except JobWorkloadEvidenceLimitExceeded:
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="workload_evidence_limit_exceeded",
                message="The workload window exceeds the bounded evidence limit.",
            )
        except ValueError:
            LOGGER.exception(
                "durable workload evidence is invalid",
                extra={"request_id": request_id(request)},
            )
            return error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_workload_evidence",
                message="The persisted workload evidence is invalid.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "durable workload aggregation failed",
                extra={"request_id": request_id(request)},
            )
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        payload = snapshot.to_dict()
        payload["links"] = {
            "jobs": "/v1/planning-jobs",
            "admission_events": "/v1/planning-admission-events",
        }
        return DurableWorkloadHealthResponse.model_validate(payload)

    @router.get(
        "/v1/planning-admission-events",
        response_model=PlanningAdmissionEventsResponse,
        responses={
            503: {"model": ErrorResponse, "description": "Admission audit store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Read tenant-scoped append-only admission decisions",
    )
    def list_planning_admission_events(
        request: Request,
        principal: ControlPrincipal = Depends(require_read_auth),
        after_event_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        try:
            events = jobs().admission_events(
                tenant_id=principal.tenant_id,
                after_event_id=after_event_id,
                limit=limit,
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "planning admission event listing failed",
                extra={"request_id": request_id(request)},
            )
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="admission_store_unavailable",
                message="The admission audit store is temporarily unavailable.",
            )
        return admission_events_response(
            events,
            after_event_id=after_event_id,
            limit=limit,
        )

    @router.post(
        "/v1/planning-jobs/{job_id}/cancel",
        response_model=PlanningJobResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Planning job not found"},
            409: {"model": ErrorResponse, "description": "Invalid job transition"},
            503: {"model": ErrorResponse, "description": "Durable job store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Request durable cancellation without killing the HTTP connection",
    )
    def cancel_planning_job(
        job_id: str,
        payload: PlanningJobCancelRequest,
        request: Request,
        principal: ControlPrincipal = Depends(require_control_auth),
    ):
        if not re.fullmatch(r"job-[a-f0-9]{32}", job_id):
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        try:
            job = jobs().cancel(
                job_id=job_id,
                reason_code=payload.reason_code,
                tenant_id=principal.tenant_id,
            )
        except JobNotFound:
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        except InvalidJobTransition:
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="invalid_job_transition",
                message="The planning job cannot be cancelled from its current status.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job cancellation failed", extra={"job_id": job_id})
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        return job_response(job)

    @router.post(
        "/v1/planning-jobs/{job_id}/replay",
        response_model=PlanningJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse, "description": "Planning job not found"},
            429: {"model": ErrorResponse, "description": "Tenant admission rejected"},
            409: {"model": ErrorResponse, "description": "Conflict or invalid transition"},
            422: {"model": ErrorResponse, "description": "Invalid idempotency key"},
            503: {"model": ErrorResponse, "description": "Durable job store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Create an idempotent replay job from a failed terminal job",
    )
    def replay_planning_job(
        job_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_replay_auth),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        if not re.fullmatch(r"job-[a-f0-9]{32}", job_id):
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        if not REQUEST_ID_PATTERN.fullmatch(idempotency_key):
            return error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_idempotency_key",
                message="Idempotency-Key must contain 1-128 safe characters.",
            )
        try:
            job = jobs().replay(
                job_id=job_id,
                request_id=request_id(request),
                idempotency_key=idempotency_key,
                tenant_id=principal.tenant_id,
                submitted_by=principal.principal_id,
                tenant_active_job_limit=principal.tenant_active_job_limit,
                tenant_submission_limit_per_minute=(
                    principal.tenant_submission_limit_per_minute
                ),
            )
        except JobNotFound:
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        except TenantAdmissionRejected as exc:
            return admission_rejected_response(request, exc)
        except IdempotencyConflict:
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="idempotency_conflict",
                message="The idempotency key is already associated with another operation.",
            )
        except InvalidJobTransition:
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="invalid_job_transition",
                message="Only failed, dead-lettered, or timed-out jobs can be replayed.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job replay failed", extra={"job_id": job_id})
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        return job_response(job)

    @router.get(
        "/v1/planning-jobs/{job_id}",
        response_model=PlanningJobResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Planning job not found"},
            500: {"model": ErrorResponse, "description": "Invalid persisted artifact"},
            503: {"model": ErrorResponse, "description": "Durable job store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Read durable job state and its completed artifact",
    )
    def get_planning_job(
        job_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_read_auth),
    ):
        if not re.fullmatch(r"job-[a-f0-9]{32}", job_id):
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        try:
            job = jobs().get(job_id, tenant_id=principal.tenant_id)
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job lookup failed", extra={"request_id": request_id(request)})
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        if job is None:
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        try:
            return job_response(job)
        except ValidationError:
            LOGGER.exception("persisted planning artifact is invalid", extra={"job_id": job_id})
            return error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_job_artifact",
                message="The persisted planning artifact is invalid.",
            )

    @router.get(
        "/v1/planning-jobs/{job_id}/diagnosis",
        response_model=JobIncidentDiagnosisResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Planning job not found"},
            409: {
                "model": ErrorResponse,
                "description": "Diagnostic event boundary exceeded",
            },
            500: {"model": ErrorResponse, "description": "Invalid diagnostic evidence"},
            503: {"model": ErrorResponse, "description": "Durable job store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Classify a durable job from privacy-minimized persisted evidence",
    )
    def diagnose_planning_job(
        job_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_read_auth),
    ):
        if not re.fullmatch(r"job-[a-f0-9]{32}", job_id):
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        try:
            diagnosis = jobs().diagnose(job_id, tenant_id=principal.tenant_id)
        except JobDiagnosticEventLimitExceeded:
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="job_diagnosis_event_limit_exceeded",
                message="The job event chain exceeds the bounded diagnostic limit.",
            )
        except ValueError:
            LOGGER.exception("planning job diagnostic evidence is invalid", extra={"job_id": job_id})
            return error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_job_diagnosis",
                message="The persisted job diagnostic evidence is invalid.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job diagnosis failed", extra={"job_id": job_id})
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        if diagnosis is None:
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        payload = diagnosis.to_dict()
        payload["links"] = {
            "job": f"/v1/planning-jobs/{job_id}",
            "events": f"/v1/planning-jobs/{job_id}/events",
            "replay": f"/v1/planning-jobs/{job_id}/replay",
        }
        return JobIncidentDiagnosisResponse.model_validate(payload)

    @router.get(
        "/v1/planning-jobs/{job_id}/events",
        response_model=PlanningJobEventsResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Planning job not found"},
            503: {"model": ErrorResponse, "description": "Durable event store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Replay persisted job state events after a cursor",
    )
    def get_planning_job_events(
        job_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_read_auth),
        after_event_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        if not re.fullmatch(r"job-[a-f0-9]{32}", job_id):
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        try:
            job = jobs().get(job_id, tenant_id=principal.tenant_id)
            if job is None:
                return error_response(
                    request,
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="job_not_found",
                    message="The planning job was not found.",
                )
            events = jobs().events(
                job_id,
                tenant_id=principal.tenant_id,
                after_event_id=after_event_id,
                limit=limit,
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job event replay failed", extra={"job_id": job_id})
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_event_store_unavailable",
                message="The durable event store is temporarily unavailable.",
            )
        return job_events_response(job_id, events, after_event_id)

    @router.get(
        "/v1/planning-jobs/{job_id}/events/stream",
        response_class=StreamingResponse,
        responses={
            200: {"description": "Durable job events as text/event-stream"},
            404: {"model": ErrorResponse, "description": "Planning job not found"},
            503: {"model": ErrorResponse, "description": "Durable event store unavailable"},
        },
        tags=["planning-jobs"],
        summary="Stream persisted job events with Last-Event-ID recovery",
    )
    def stream_planning_job_events(
        job_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_read_auth),
        after_event_id: int | None = Query(default=None, ge=0),
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
        stream_seconds: float = Query(default=15.0, gt=0, le=30),
        poll_interval_ms: int = Query(default=200, ge=10, le=5000),
    ):
        if not re.fullmatch(r"job-[a-f0-9]{32}", job_id):
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        cursor = after_event_id if after_event_id is not None else (last_event_id or 0)
        try:
            job = jobs().get(job_id, tenant_id=principal.tenant_id)
            if job is None:
                return error_response(
                    request,
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="job_not_found",
                    message="The planning job was not found.",
                )
            initial_events = jobs().events(
                job_id,
                tenant_id=principal.tenant_id,
                after_event_id=cursor,
                limit=limit,
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job event stream failed", extra={"job_id": job_id})
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_event_store_unavailable",
                message="The durable event store is temporarily unavailable.",
            )

        def stream() -> Iterator[str]:
            current_cursor = cursor
            pending = initial_events
            deadline = time.monotonic() + stream_seconds
            while True:
                for event in pending:
                    yield encode_job_event(event)
                    current_cursor = event.event_id

                try:
                    current_job = jobs().get(job_id, tenant_id=principal.tenant_id)
                except (OSError, RuntimeError, sqlite3.Error) as exc:
                    LOGGER.warning(
                        "planning job event stream interrupted",
                        extra={"job_id": job_id, "error_type": type(exc).__name__},
                    )
                    yield encode_stream_error(current_cursor)
                    return
                if current_job is None or current_job.status in TERMINAL_JOB_STATUSES:
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield encode_stream_timeout(current_cursor)
                    return
                time.sleep(min(poll_interval_ms / 1000, remaining))
                try:
                    pending = jobs().events(
                        job_id,
                        tenant_id=principal.tenant_id,
                        after_event_id=current_cursor,
                        limit=limit,
                    )
                except (OSError, RuntimeError, sqlite3.Error) as exc:
                    LOGGER.warning(
                        "planning job event stream interrupted",
                        extra={"job_id": job_id, "error_type": type(exc).__name__},
                    )
                    yield encode_stream_error(current_cursor)
                    return

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


    return router
