"""FastAPI adapter for BJ-Pal's canonical application service."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
import uuid
from typing import Callable, Iterator, Protocol

from fastapi import Depends, FastAPI, Header, Query, Request, Response, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from agents.tracing import trace_export_status

from application import (
    ExecutionBudgetExceeded,
    ModelOutputContractError,
    PlanRequest,
    PlanResult,
    PlanningCallbacks,
    PlanningClarificationRequired,
    PlanningService,
)
from clarifications import (
    ClarificationContinuationService,
    ClarificationExpired,
    ClarificationIntegrityError,
    ClarificationInProgress,
    ClarificationNotFound,
    ClarificationResolutionConflict,
    InvalidClarificationTransition,
)
from data_profile import inspect_runtime_data
from jobs import (
    DurableWorkloadHealth,
    IdempotencyConflict,
    InvalidJobTransition,
    JobDiagnosticEventLimitExceeded,
    JobIncidentDiagnosis,
    JobWorkloadEvidenceLimitExceeded,
    JobNotFound,
    PlanningAdmissionEvent,
    PlanningJob,
    PlanningJobEvent,
    PlanningJobService,
    PlanningJobSummary,
    SUBMISSION_RATE_WINDOW_SECONDS,
    TenantAdmissionRejected,
)
from jobs.workload_health import validate_closed_window
from monitoring import OperationalAlertSnapshot
from operations import (
    InvalidOperationTransition,
    OperationApprovalConflict,
    OperationEvent,
    OperationExpired,
    OperationIdempotencyConflict,
    OperationNotFound,
    OperationQuote,
    OperationReconciliation,
    OperationReconciliationUnavailable,
    OperationSelfApprovalForbidden,
    SandboxProviderFailure,
    SideEffectOperation,
    SideEffectOperationService,
)
from outcomes import (
    FeedbackExpired,
    FeedbackIdempotencyConflict,
    FeedbackIntegrityError,
    FeedbackNotFound,
    FeedbackPhaseConflict,
    PlanFeedbackReport,
    PlanFeedbackService,
    TrialClosed,
    TrialConsentMismatch,
    TrialEnrollmentConflict,
    TrialIntegrityError,
    TrialNotActive,
    TrialNotFound,
    TrialParticipantWithdrawn,
    sha256_json,
)
from storage.legacy_retirement import (
    DEDICATED_REQUIRED_POLICY,
    inspect_legacy_retirement,
    state_layout_policy,
)

from .schemas import (
    ErrorResponse,
    ClarificationContinueRequest,
    DurableWorkloadHealthResponse,
    FeedbackCollectionResponse,
    FeedbackReportResponse,
    FeedbackSubmitRequest,
    FeedbackSummaryResponse,
    HealthResponse,
    PlanCreateRequest,
    PlanCreateResponse,
    PlanningJobCancelRequest,
    PlanningAdmissionEventResponse,
    PlanningAdmissionEventsResponse,
    PlanningJobEventResponse,
    PlanningJobEventsResponse,
    JobIncidentDiagnosisResponse,
    PlanningJobListItemResponse,
    PlanningJobListResponse,
    PlanningJobResponse,
    PlanningJobStatus,
    PlanningJobSubmitRequest,
    ReadinessResponse,
    OperationApprovalRequest,
    OperationDenialRequest,
    OperationEventResponse,
    OperationEventsResponse,
    OperationReconciliationResponse,
    OperationReconciliationsResponse,
    OperationalAlertSnapshotResponse,
    SideEffectOperationRequest,
    SideEffectOperationResponse,
    TrialCreateRequest,
    TrialEnrollRequest,
    TrialEnrollmentInvitationRequest,
    TrialEnrollmentInvitationResponse,
    TrialEvidenceSnapshotResponse,
    TrialEvidenceSummaryResponse,
    TrialNoticeResponse,
    TrialParticipantEventResponse,
    TrialParticipantResponse,
    TraceExportStatusResponse,
)
from .auth import (
    JOBS_CONTROL,
    JOBS_READ,
    JOBS_REPLAY,
    JOBS_SUBMIT,
    OPERATIONS_APPROVE,
    OPERATIONS_READ,
    OPERATIONS_RECONCILE,
    OPERATIONS_REQUEST,
    TRIALS_MANAGE,
    TRIALS_READ,
    ControlPlaneAuthenticator,
    ControlPlaneCredential,
    ControlPlaneForbidden,
    ControlPlaneNotConfigured,
    ControlPrincipal,
    ControlPlaneUnauthorized,
)
from .sse import (
    TERMINAL_JOB_STATUSES,
    encode_job_event,
    encode_stream_error,
    encode_stream_timeout,
)
from version import SERVICE_VERSION


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
LOGGER = logging.getLogger(__name__)


class PlanningExecutor(Protocol):
    def execute(self, request: PlanRequest, **kwargs) -> PlanResult: ...


class PlanningJobExecutor(Protocol):
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


class SideEffectOperationExecutor(Protocol):
    def request(self, **kwargs) -> SideEffectOperation: ...

    def approve(self, **kwargs) -> SideEffectOperation: ...

    def deny(self, **kwargs) -> SideEffectOperation: ...

    def get(
        self,
        operation_id: str,
        *,
        tenant_id: str | None = None,
    ) -> SideEffectOperation | None: ...

    def events(
        self,
        operation_id: str,
        **kwargs,
    ) -> tuple[OperationEvent, ...]: ...

    def reconciliations(
        self,
        operation_id: str,
        **kwargs,
    ) -> tuple[OperationReconciliation, ...]: ...

    def reconcile_uncertain(self, **kwargs): ...


class FeedbackExecutor(Protocol):
    def issue(self, **kwargs): ...

    def submit(self, **kwargs) -> PlanFeedbackReport: ...

    def list_reports(self, **kwargs) -> tuple[PlanFeedbackReport, ...]: ...

    def public_summary(self, **kwargs) -> dict: ...

    def create_trial(self, **kwargs): ...

    def get_trial(self, trial_id: str): ...

    def issue_trial_enrollment(self, **kwargs): ...

    def enroll_trial(self, **kwargs): ...

    def authorize_trial_participant(self, **kwargs): ...

    def withdraw_trial(self, **kwargs): ...

    def trial_summary(self, **kwargs) -> dict: ...

    def close_trial(self, **kwargs): ...


ReadinessProbe = Callable[[], ReadinessResponse]


def default_readiness_probe() -> ReadinessResponse:
    audit = inspect_runtime_data()
    checks = dict(audit.checks)
    state_ready = True
    try:
        policy = state_layout_policy()
    except ValueError:
        policy = "invalid"
        state_ready = False
        checks["state_layout_policy"] = "invalid"
    if policy == DEDICATED_REQUIRED_POLICY:
        state_audit = inspect_legacy_retirement(policy=policy)
        state_ready = state_audit.ready
        checks["state_layout_policy"] = "ok" if state_ready else "failed"
        checks.update(
            {
                f"state_layout_{name}": value
                for name, value in state_audit.checks.items()
            }
        )
    return ReadinessResponse(
        status="ready" if audit.ready and state_ready else "not_ready",
        data_profile=audit.profile.name,
        classification=audit.profile.classification,
        checks=checks,
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> JSONResponse:
    payload = ErrorResponse.model_validate({
        "error": {
            "code": code,
            "message": message,
            "request_id": _request_id(request),
            "details": details,
        }
    })
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
    )


def create_app(
    *,
    service: PlanningExecutor | None = None,
    readiness_probe: ReadinessProbe | None = None,
    job_service: PlanningJobExecutor | None = None,
    operation_service: SideEffectOperationExecutor | None = None,
    feedback_service: FeedbackExecutor | None = None,
    clarification_service: ClarificationContinuationService | None = None,
    control_token: str | None = None,
    control_credentials: tuple[ControlPlaneCredential, ...] | None = None,
    public_demo: bool = False,
) -> FastAPI:
    planning_service = service or PlanningService()
    probe = readiness_probe or default_readiness_probe
    resolved_job_service = job_service
    resolved_operation_service = operation_service
    resolved_feedback_service = feedback_service
    resolved_clarification_service = clarification_service
    authenticator = ControlPlaneAuthenticator.from_configuration(
        legacy_token=(
            control_token if control_token is not None else os.environ.get("BJ_PAL_CONTROL_TOKEN")
        ),
        registry_json=(
            None
            if control_token is not None or control_credentials is not None
            else os.environ.get("BJ_PAL_CONTROL_PRINCIPALS_JSON")
        ),
        credentials=control_credentials,
    )
    bearer = HTTPBearer(
        auto_error=False,
        scheme_name="BJPalControlBearer",
        description="Bearer token for durable planning-job submission and control.",
    )

    def require_scope(required_scope: str):
        def authorize(
            credentials: HTTPAuthorizationCredentials | None = Security(bearer),
        ) -> ControlPrincipal:
            return authenticator.authorize(credentials, required_scope=required_scope)

        return authorize

    require_submit_auth = require_scope(JOBS_SUBMIT)
    require_read_auth = require_scope(JOBS_READ)
    require_control_auth = require_scope(JOBS_CONTROL)
    require_replay_auth = require_scope(JOBS_REPLAY)
    require_operation_request_auth = require_scope(OPERATIONS_REQUEST)
    require_operation_read_auth = require_scope(OPERATIONS_READ)
    require_operation_approve_auth = require_scope(OPERATIONS_APPROVE)
    require_operation_reconcile_auth = require_scope(OPERATIONS_RECONCILE)
    require_trial_manage_auth = require_scope(TRIALS_MANAGE)
    require_trial_read_auth = require_scope(TRIALS_READ)

    def jobs() -> PlanningJobExecutor:
        nonlocal resolved_job_service
        if resolved_job_service is None:
            resolved_job_service = PlanningJobService()
        return resolved_job_service

    def operations() -> SideEffectOperationExecutor:
        nonlocal resolved_operation_service
        if resolved_operation_service is None:
            resolved_operation_service = SideEffectOperationService()
        return resolved_operation_service

    def feedback() -> FeedbackExecutor:
        nonlocal resolved_feedback_service
        if resolved_feedback_service is None:
            resolved_feedback_service = PlanFeedbackService()
        return resolved_feedback_service

    def trial_error_response(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, TrialNotFound):
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="trial_not_found",
                message="The trial or trial capability was not found.",
            )
        if isinstance(exc, TrialNotActive):
            return _error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="trial_not_active",
                message="The trial collection window is not active.",
            )
        if isinstance(exc, TrialClosed):
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="trial_closed",
                message="The trial evidence has already been frozen.",
            )
        if isinstance(exc, TrialParticipantWithdrawn):
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="trial_participant_withdrawn",
                message="The trial participant has withdrawn.",
            )
        if isinstance(exc, TrialEnrollmentConflict):
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="trial_enrollment_conflict",
                message="The single-use trial enrollment capability was already consumed.",
            )
        if isinstance(exc, TrialConsentMismatch):
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="trial_consent_mismatch",
                message="Consent must attest to the exact published trial notice.",
            )
        if isinstance(exc, TrialIntegrityError):
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_trial_artifact",
                message="The persisted trial evidence is invalid.",
            )
        return _error_response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_trial_request",
            message="The trial request did not match the evidence contract.",
        )

    def deliver_plan_payload(
        payload: dict,
        *,
        trial_participant_capability: str | None = None,
    ) -> PlanCreateResponse:
        """Add an ephemeral feedback capability without changing the plan artifact."""
        canonical = PlanCreateResponse.model_validate(payload)
        if public_demo:
            return canonical
        execution = canonical.execution
        if execution is None:
            return canonical
        invitation = feedback().issue(
            plan_id=canonical.final_plan.plan_id,
            plan_artifact_sha256=sha256_json(
                canonical.final_plan.model_dump(mode="json")
            ),
            data_profile_name=canonical.data_profile.name,
            data_profile_classification=canonical.data_profile.classification,
            trial_participant_capability=trial_participant_capability,
        )
        delivered = dict(payload)
        delivered["feedback"] = invitation.to_public_dict(
            feedback_url=f"/v1/plans/{canonical.final_plan.plan_id}/feedback"
        )
        return PlanCreateResponse.model_validate(delivered)

    def clarifications() -> ClarificationContinuationService:
        nonlocal resolved_clarification_service
        if resolved_clarification_service is None:
            resolved_clarification_service = ClarificationContinuationService()
        return resolved_clarification_service

    def issue_clarification(
        *,
        application_request: PlanRequest,
        error: PlanningClarificationRequired,
        delivery: str,
        job_policy: dict | None = None,
    ) -> dict:
        session = clarifications().issue(
            request=application_request,
            error=error,
            delivery=delivery,
            job_policy=job_policy,
        )
        details = {
            "requirements": error.decision.to_dict(),
            "continuation": session.to_public_dict(),
        }
        if error.constraints is not None:
            details["constraints"] = error.constraints.to_dict()
        return details

    def clarification_error_response(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        if isinstance(exc, ClarificationIntegrityError):
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_clarification_artifact",
                message="The clarification continuation artifact is invalid.",
            )
        if isinstance(exc, ClarificationNotFound):
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="clarification_not_found",
                message="The clarification continuation was not found.",
            )
        if isinstance(exc, ClarificationExpired):
            return _error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="clarification_expired",
                message="The clarification continuation has expired.",
            )
        if isinstance(exc, ClarificationInProgress):
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="clarification_in_progress",
                message="The clarification continuation is already executing.",
            )
        if isinstance(exc, ClarificationResolutionConflict):
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="clarification_resolution_conflict",
                message="The clarification was already resolved differently.",
            )
        if isinstance(exc, InvalidClarificationTransition):
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="invalid_clarification_transition",
                message="The clarification continuation cannot perform this transition.",
            )
        return _error_response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_clarification_resolution",
            message="The clarification resolution is invalid.",
        )

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
        response = _error_response(
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

    def operation_response(
        operation: SideEffectOperation,
    ) -> SideEffectOperationResponse:
        base = f"/v1/operations/{operation.operation_id}"
        return SideEffectOperationResponse.model_validate(
            {
                "operation_id": operation.operation_id,
                "request_id": operation.request_id,
                "tenant_id": operation.tenant_id,
                "requested_by": operation.requested_by,
                "operation_kind": operation.operation_kind,
                "status": operation.status,
                "action": operation.action_payload,
                "request_sha256": operation.request_sha256,
                "quote": operation.quote.to_dict(),
                "approval_sha256": operation.approval_sha256,
                "approval_expires_at": operation.approval_expires_at,
                "approved_by": operation.approved_by,
                "approved_at": operation.approved_at,
                "denied_by": operation.denied_by,
                "denied_at": operation.denied_at,
                "denial_reason_code": operation.denial_reason_code,
                "attempt": operation.attempt,
                "provider_operation_id": operation.provider_operation_id,
                "receipt": operation.receipt_payload,
                "receipt_sha256": operation.receipt_sha256,
                "error_code": operation.error_code,
                "error_message": operation.error_message,
                "created_at": operation.created_at,
                "updated_at": operation.updated_at,
                "links": {
                    "self": base,
                    "approve": f"{base}/approve",
                    "deny": f"{base}/deny",
                    "events": f"{base}/events",
                    "reconcile": f"{base}/reconcile",
                    "reconciliations": f"{base}/reconciliations",
                },
            }
        )

    def operation_events_response(
        operation_id: str,
        events: tuple[OperationEvent, ...],
        *,
        after_event_id: int,
        limit: int,
    ) -> OperationEventsResponse:
        next_cursor = events[-1].event_id if events else after_event_id
        base = f"/v1/operations/{operation_id}/events"
        return OperationEventsResponse(
            operation_id=operation_id,
            events=[
                OperationEventResponse.model_validate(event.__dict__)
                for event in events
            ],
            next_after_event_id=next_cursor,
            links={
                "self": f"{base}?after_event_id={after_event_id}&limit={limit}",
                "next": f"{base}?after_event_id={next_cursor}&limit={limit}",
            },
        )

    def operation_reconciliations_response(
        operation_id: str,
        reconciliations: tuple[OperationReconciliation, ...],
        *,
        after_reconciliation_id: int,
        limit: int,
    ) -> OperationReconciliationsResponse:
        next_cursor = (
            reconciliations[-1].reconciliation_id
            if reconciliations
            else after_reconciliation_id
        )
        base = f"/v1/operations/{operation_id}/reconciliations"
        return OperationReconciliationsResponse(
            operation_id=operation_id,
            reconciliations=[
                OperationReconciliationResponse.model_validate(
                    {
                        "reconciliation_id": item.reconciliation_id,
                        "operation_id": item.operation_id,
                        "tenant_id": item.tenant_id,
                        "actor_id": item.actor_id,
                        "outcome": item.outcome,
                        "provider_operation_id": item.provider_operation_id,
                        "evidence": item.evidence_payload,
                        "evidence_sha256": item.evidence_sha256,
                        "receipt_sha256": item.receipt_sha256,
                        "created_at": item.created_at,
                    }
                )
                for item in reconciliations
            ],
            next_after_reconciliation_id=next_cursor,
            links={
                "self": (
                    f"{base}?after_reconciliation_id="
                    f"{after_reconciliation_id}&limit={limit}"
                ),
                "next": (
                    f"{base}?after_reconciliation_id={next_cursor}&limit={limit}"
                ),
            },
        )

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
    application = FastAPI(
        title="BJ-Pal Planning API",
        version=SERVICE_VERSION,
        description=(
            "Short-activity planning API. Public demo responses use a provenance-labeled "
            "synthetic dataset and do not prove live availability or booking success."
        ),
    )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, exc: RequestValidationError):
        LOGGER.info(
            "planning request rejected by HTTP schema",
            extra={"request_id": _request_id(request), "error_count": len(exc.errors())},
        )
        return _error_response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_request",
            message="The request did not match the API contract.",
        )

    @application.exception_handler(ControlPlaneNotConfigured)
    async def control_plane_not_configured(request: Request, exc: ControlPlaneNotConfigured):
        del exc
        return _error_response(
            request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="control_plane_not_configured",
            message="The durable job control plane is not configured.",
        )

    @application.exception_handler(ControlPlaneUnauthorized)
    async def control_plane_unauthorized(request: Request, exc: ControlPlaneUnauthorized):
        del exc
        response = _error_response(
            request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="control_plane_unauthorized",
            message="A valid control-plane bearer token is required.",
        )
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @application.exception_handler(ControlPlaneForbidden)
    async def control_plane_forbidden(request: Request, exc: ControlPlaneForbidden):
        return _error_response(
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            code=exc.code,
            message=exc.message,
        )

    @application.middleware("http")
    async def attach_request_id(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else f"req-{uuid.uuid4().hex}"
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @application.get(
        "/healthz",
        response_model=HealthResponse,
        tags=["operations"],
        summary="Process liveness",
    )
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok", service="bj-pal", version=SERVICE_VERSION)

    @application.get(
        "/readyz",
        response_model=ReadinessResponse,
        tags=["operations"],
        summary="Dataset and database readiness",
    )
    def readyz(response: Response) -> ReadinessResponse:
        result = probe()
        if result.status != "ready":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return result

    @application.get(
        "/v1/trace-export-status",
        response_model=TraceExportStatusResponse,
        tags=["operations"],
        summary="Read privacy-minimized trace export health",
    )
    def get_trace_export_status(
        principal: ControlPrincipal = Depends(require_read_auth),
    ) -> TraceExportStatusResponse:
        del principal
        return TraceExportStatusResponse.model_validate(
            trace_export_status().to_dict()
        )

    @application.get(
        "/v1/operational-alerts",
        response_model=OperationalAlertSnapshotResponse,
        responses={
            400: {"model": ErrorResponse, "description": "Invalid metrics window"},
            409: {
                "model": ErrorResponse,
                "description": "Bounded evidence limit exceeded",
            },
            500: {
                "model": ErrorResponse,
                "description": "Invalid operational evidence",
            },
            503: {"model": ErrorResponse, "description": "Job store unavailable"},
        },
        tags=["operations"],
        summary="Evaluate deterministic operational alert rules",
    )
    def get_operational_alerts(
        request: Request,
        principal: ControlPrincipal = Depends(require_read_auth),
        window_start: str = Query(min_length=20, max_length=64),
        window_end: str = Query(min_length=20, max_length=64),
    ):
        try:
            validate_closed_window(window_start, window_end)
        except ValueError as exc:
            return _error_response(
                request,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid_operational_alert_window",
                message=str(exc),
            )
        try:
            workload = jobs().workload_health(
                tenant_id=principal.tenant_id,
                window_start=window_start,
                window_end=window_end,
            )
            snapshot = OperationalAlertSnapshot.create(
                workload=workload,
                trace_status=trace_export_status().to_dict(),
            )
        except JobWorkloadEvidenceLimitExceeded:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operational_alert_evidence_limit_exceeded",
                message="The alert window exceeds the bounded evidence limit.",
            )
        except ValueError:
            LOGGER.exception(
                "operational alert evidence is invalid",
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_operational_alert_evidence",
                message="The operational alert evidence is invalid.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "operational alert evaluation failed",
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        payload = snapshot.to_dict()
        payload["links"] = {
            "workload": (
                "/v1/planning-job-health"
                f"?window_start={snapshot.window_start}&window_end={snapshot.window_end}"
            ),
            "trace_export": "/v1/trace-export-status",
        }
        return OperationalAlertSnapshotResponse.model_validate(payload)

    @application.post(
        "/v1/plans",
        response_model=PlanCreateResponse,
        responses={
            409: {"model": ErrorResponse, "description": "Clarification required"},
            413: {"model": ErrorResponse, "description": "Public demo body limit exceeded"},
            429: {
                "model": ErrorResponse,
                "description": "Execution or public demo attempt budget exhausted",
            },
            422: {"model": ErrorResponse, "description": "Invalid planning request"},
            500: {"model": ErrorResponse, "description": "Invalid internal planning result"},
            503: {"model": ErrorResponse, "description": "Planning backend unavailable"},
        },
        tags=["planning"],
        summary="Create and risk-adjust an activity plan",
    )
    def create_plan(
        payload: PlanCreateRequest,
        request: Request,
        trial_participant_capability: str | None = Header(
            default=None,
            alias="X-Trial-Participant-Capability",
        ),
    ):
        if public_demo and payload.user_id is not None:
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="public_demo_user_id_unsupported",
                message="The public demo does not accept user identifiers.",
            )
        if public_demo and trial_participant_capability is not None:
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="public_demo_capability_unsupported",
                message="The public demo does not accept trial or feedback capabilities.",
            )
        if trial_participant_capability is not None:
            try:
                feedback().authorize_trial_participant(
                    capability=trial_participant_capability
                )
            except (
                TrialNotFound,
                TrialNotActive,
                TrialClosed,
                TrialParticipantWithdrawn,
                TrialIntegrityError,
                ValueError,
            ) as exc:
                return trial_error_response(request, exc)
            except (OSError, RuntimeError, sqlite3.Error):
                return _error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="feedback_store_unavailable",
                    message="The feedback evidence store is unavailable.",
                )
        application_request = payload.to_application_request()
        try:
            result = planning_service.execute(
                application_request,
                callbacks=PlanningCallbacks(correlation_id=_request_id(request)),
            )
        except PlanningClarificationRequired as exc:
            if public_demo:
                details = {
                    "requirements": exc.decision.to_dict(),
                    "continuation_available": False,
                }
                if exc.constraints is not None:
                    details["constraints"] = exc.constraints.to_dict()
                return _error_response(
                    request,
                    status_code=status.HTTP_409_CONFLICT,
                    code="clarification_required",
                    message=(
                        "The public demo needs a complete request and does not persist "
                        "clarification continuations."
                    ),
                    details=details,
                )
            try:
                details = issue_clarification(
                    application_request=application_request,
                    error=exc,
                    delivery="sync",
                )
            except (OSError, RuntimeError, sqlite3.Error):
                LOGGER.exception(
                    "clarification continuation persistence failed",
                    extra={"request_id": _request_id(request)},
                )
                return _error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="clarification_store_unavailable",
                    message="The clarification continuation store is unavailable.",
                )
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="clarification_required",
                message="The planning request needs one clarification before execution.",
                details=details,
            )
        except ExecutionBudgetExceeded as exc:
            return _error_response(
                request,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code=exc.code,
                message="The server execution budget stopped this planning request.",
                details=exc.safe_details(),
            )
        except ModelOutputContractError as exc:
            return _error_response(
                request,
                status_code=status.HTTP_502_BAD_GATEWAY,
                code=exc.code,
                message="The model could not produce a valid grounded plan.",
                details=exc.safe_details(),
            )
        except ValueError:
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_planning_request",
                message="The planning request could not be accepted.",
            )
        except (FileNotFoundError, OSError, RuntimeError):
            LOGGER.exception("planning request failed", extra={"request_id": _request_id(request)})
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="planning_unavailable",
                message="The planning service is temporarily unavailable.",
            )
        try:
            return deliver_plan_payload(
                result.to_dict(),
                trial_participant_capability=trial_participant_capability,
            )
        except (ValidationError, ValueError):
            LOGGER.exception(
                "planning service returned an invalid contract",
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_planning_result",
                message="The planning service returned an invalid result.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "plan feedback invitation persistence failed",
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="feedback_store_unavailable",
                message="The feedback evidence store is unavailable.",
            )

    @application.post(
        "/v1/clarifications/{continuation_id}/plan",
        response_model=PlanCreateResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Continuation not found"},
            409: {"model": ErrorResponse, "description": "Resolution conflict or in progress"},
            410: {"model": ErrorResponse, "description": "Continuation expired"},
            422: {"model": ErrorResponse, "description": "Invalid resolution"},
            429: {"model": ErrorResponse, "description": "Execution budget exhausted"},
            500: {"model": ErrorResponse, "description": "Invalid continuation artifact"},
            503: {"model": ErrorResponse, "description": "Planning or continuation store unavailable"},
        },
        tags=["planning"],
        summary="Resolve one clarification and continue a synchronous plan",
    )
    def continue_plan(
        continuation_id: str,
        payload: ClarificationContinueRequest,
        request: Request,
        trial_participant_capability: str | None = Header(
            default=None,
            alias="X-Trial-Participant-Capability",
        ),
    ):
        if trial_participant_capability is not None:
            try:
                feedback().authorize_trial_participant(
                    capability=trial_participant_capability
                )
            except (
                TrialNotFound,
                TrialNotActive,
                TrialClosed,
                TrialParticipantWithdrawn,
                TrialIntegrityError,
                ValueError,
            ) as exc:
                return trial_error_response(request, exc)
            except (OSError, RuntimeError, sqlite3.Error):
                return _error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="feedback_store_unavailable",
                    message="The feedback evidence store is unavailable.",
                )
        owner = f"sync-{uuid.uuid4().hex}"
        try:
            session, resolved_request = clarifications().resolve_request(
                continuation_id=continuation_id,
                delivery="sync",
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
            LOGGER.exception(
                "clarification continuation store failed",
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="clarification_store_unavailable",
                message="The clarification continuation store is unavailable.",
            )

        if session.status == "completed":
            next_clarification = (session.result_payload or {}).get("next_clarification")
            if isinstance(next_clarification, dict):
                return _error_response(
                    request,
                    status_code=status.HTTP_409_CONFLICT,
                    code="clarification_required",
                    message="One additional clarification is required before execution.",
                    details=next_clarification,
                )
            try:
                return deliver_plan_payload(
                    session.result_payload or {},
                    trial_participant_capability=trial_participant_capability,
                )
            except (ValidationError, ValueError):
                return _error_response(
                    request,
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="invalid_clarification_artifact",
                    message="The clarification continuation artifact is invalid.",
                )
            except (OSError, RuntimeError, sqlite3.Error):
                return _error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="feedback_store_unavailable",
                    message="The feedback evidence store is unavailable.",
                )

        try:
            result = planning_service.execute(
                resolved_request,
                callbacks=PlanningCallbacks(correlation_id=_request_id(request)),
            )
        except PlanningClarificationRequired as exc:
            try:
                details = issue_clarification(
                    application_request=resolved_request,
                    error=exc,
                    delivery="sync",
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
                return _error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="clarification_store_unavailable",
                    message="The next clarification could not be persisted.",
                )
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="clarification_required",
                message="One additional clarification is required before execution.",
                details=details,
            )
        except ExecutionBudgetExceeded as exc:
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return _error_response(
                request,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code=exc.code,
                message="The server execution budget stopped this planning request.",
                details=exc.safe_details(),
            )
        except ModelOutputContractError as exc:
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return _error_response(
                request,
                status_code=status.HTTP_502_BAD_GATEWAY,
                code=exc.code,
                message="The model could not produce a valid grounded plan.",
                details=exc.safe_details(),
            )
        except ValueError:
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_planning_request",
                message="The clarified planning request could not be accepted.",
            )
        except (FileNotFoundError, OSError, RuntimeError):
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            LOGGER.exception(
                "clarified planning request failed",
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="planning_unavailable",
                message="The planning service is temporarily unavailable.",
            )

        result_payload = result.to_dict()
        try:
            PlanCreateResponse.model_validate(result_payload)
            clarifications().complete(
                continuation_id=continuation_id,
                owner=owner,
                result_payload=result_payload,
            )
        except ValidationError:
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_planning_result",
                message="The planning service returned an invalid result.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="clarification_store_unavailable",
                message="The clarification result could not be persisted.",
            )
        try:
            return deliver_plan_payload(
                result_payload,
                trial_participant_capability=trial_participant_capability,
            )
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="feedback_store_unavailable",
                message="The feedback evidence store is unavailable.",
            )

    @application.post(
        "/v1/trials",
        response_model=TrialNoticeResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["human-outcome-evidence"],
        summary="Create a tenant-scoped, consent-bound evaluation cohort",
    )
    def create_trial(
        payload: TrialCreateRequest,
        request: Request,
        principal: ControlPrincipal = Depends(require_trial_manage_auth),
    ):
        try:
            trial = feedback().create_trial(
                created_by=principal.principal_id,
                tenant_id=principal.tenant_id,
                duration_days=payload.duration_days,
                retention_days=payload.retention_days,
                minimum_participants=payload.minimum_participants,
            )
            return TrialNoticeResponse.model_validate(trial.to_notice_dict())
        except (TrialIntegrityError, ValueError) as exc:
            return trial_error_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @application.get(
        "/v1/trials/{trial_id}/notice",
        response_model=TrialNoticeResponse,
        tags=["human-outcome-evidence"],
        summary="Read the exact consent notice before enrollment",
    )
    def get_trial_notice(trial_id: str, request: Request):
        try:
            return TrialNoticeResponse.model_validate(
                feedback().get_trial(trial_id).to_notice_dict()
            )
        except (TrialNotFound, TrialIntegrityError, ValueError) as exc:
            return trial_error_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @application.post(
        "/v1/trials/{trial_id}/enrollment-invitations",
        response_model=TrialEnrollmentInvitationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["human-outcome-evidence"],
        summary="Issue one operator-controlled, single-use enrollment capability",
    )
    def issue_trial_enrollment(
        trial_id: str,
        payload: TrialEnrollmentInvitationRequest,
        request: Request,
        principal: ControlPrincipal = Depends(require_trial_manage_auth),
    ):
        try:
            invitation = feedback().issue_trial_enrollment(
                trial_id=trial_id,
                issued_by=principal.principal_id,
                tenant_id=principal.tenant_id,
                ttl_seconds=payload.ttl_seconds,
            )
            return TrialEnrollmentInvitationResponse.model_validate(
                invitation.to_public_dict(
                    enroll_url=f"/v1/trials/{trial_id}/participants"
                )
            )
        except (
            TrialNotFound,
            TrialNotActive,
            TrialClosed,
            TrialIntegrityError,
            ValueError,
        ) as exc:
            return trial_error_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @application.post(
        "/v1/trials/{trial_id}/participants",
        response_model=TrialParticipantResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["human-outcome-evidence"],
        summary="Consume an enrollment capability and attest to the exact notice",
    )
    def enroll_trial_participant(
        trial_id: str,
        payload: TrialEnrollRequest,
        request: Request,
        enrollment_capability: str = Header(
            alias="X-Trial-Enrollment-Capability"
        ),
    ):
        try:
            participant = feedback().enroll_trial(
                trial_id=trial_id,
                enrollment_capability=enrollment_capability,
                consent_notice_sha256=payload.consent_notice_sha256,
                consent_attested=payload.consent_attested,
            )
            return TrialParticipantResponse.model_validate(
                participant.to_public_dict()
            )
        except (
            TrialNotFound,
            TrialNotActive,
            TrialClosed,
            TrialConsentMismatch,
            TrialEnrollmentConflict,
            TrialIntegrityError,
            ValueError,
        ) as exc:
            return trial_error_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @application.post(
        "/v1/trials/{trial_id}/withdraw",
        response_model=TrialParticipantEventResponse,
        tags=["human-outcome-evidence"],
        summary="Withdraw a participant and exclude future cohort aggregation",
    )
    def withdraw_trial_participant(
        trial_id: str,
        request: Request,
        participant_capability: str = Header(
            alias="X-Trial-Participant-Capability"
        ),
    ):
        try:
            event = feedback().withdraw_trial(
                trial_id=trial_id,
                participant_capability=participant_capability,
            )
            return TrialParticipantEventResponse.model_validate(event.to_dict())
        except (
            TrialNotFound,
            TrialNotActive,
            TrialClosed,
            TrialParticipantWithdrawn,
            TrialIntegrityError,
            ValueError,
        ) as exc:
            return trial_error_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @application.get(
        "/v1/trials/{trial_id}/summary",
        response_model=(TrialEvidenceSummaryResponse | TrialEvidenceSnapshotResponse),
        tags=["human-outcome-evidence"],
        summary="Read tenant-scoped cohort evidence or its frozen snapshot",
    )
    def get_trial_summary(
        trial_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_trial_read_auth),
    ):
        try:
            return feedback().trial_summary(
                trial_id=trial_id,
                tenant_id=principal.tenant_id,
            )
        except (TrialNotFound, TrialIntegrityError, ValueError) as exc:
            return trial_error_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @application.post(
        "/v1/trials/{trial_id}/close",
        response_model=TrialEvidenceSnapshotResponse,
        tags=["human-outcome-evidence"],
        summary="Freeze an immutable, cutoff-bound cohort evidence snapshot",
    )
    def close_trial(
        trial_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_trial_manage_auth),
    ):
        try:
            snapshot = feedback().close_trial(
                trial_id=trial_id,
                closed_by=principal.principal_id,
                tenant_id=principal.tenant_id,
            )
            return TrialEvidenceSnapshotResponse.model_validate(snapshot.to_dict())
        except (TrialNotFound, TrialIntegrityError, ValueError) as exc:
            return trial_error_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @application.post(
        "/v1/plans/{plan_id}/feedback",
        response_model=FeedbackReportResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            404: {"model": ErrorResponse, "description": "Capability or plan not found"},
            409: {"model": ErrorResponse, "description": "Immutable feedback conflict"},
            410: {"model": ErrorResponse, "description": "Feedback capability expired"},
            422: {"model": ErrorResponse, "description": "Invalid feedback contract"},
            503: {"model": ErrorResponse, "description": "Feedback store unavailable"},
        },
        tags=["human-outcome-evidence"],
        summary="Append one capability-bound, self-reported plan outcome",
    )
    def submit_plan_feedback(
        plan_id: str,
        payload: FeedbackSubmitRequest,
        request: Request,
        capability: str = Header(alias="X-Feedback-Capability"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        if not REQUEST_ID_PATTERN.fullmatch(idempotency_key):
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_idempotency_key",
                message="Idempotency-Key must contain 1-128 safe characters.",
            )
        try:
            report = feedback().submit(
                plan_id=plan_id,
                capability=capability,
                idempotency_key=idempotency_key,
                phase=payload.phase,
                value=payload.value,
                reason_codes=tuple(payload.reason_codes),
            )
        except FeedbackNotFound:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="feedback_not_found",
                message="The feedback invitation was not found.",
            )
        except FeedbackExpired:
            return _error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="feedback_expired",
                message="The feedback invitation has expired.",
            )
        except FeedbackIdempotencyConflict:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="feedback_idempotency_conflict",
                message="The idempotency key belongs to another feedback report.",
            )
        except FeedbackPhaseConflict:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="feedback_phase_conflict",
                message="This plan already has an immutable report for that phase.",
            )
        except (
            TrialNotFound,
            TrialNotActive,
            TrialClosed,
            TrialParticipantWithdrawn,
            TrialIntegrityError,
        ) as exc:
            return trial_error_response(request, exc)
        except FeedbackIntegrityError:
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_feedback_artifact",
                message="The persisted feedback evidence is invalid.",
            )
        except ValueError:
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_feedback",
                message="The feedback did not match the evidence contract.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="feedback_store_unavailable",
                message="The feedback evidence store is unavailable.",
            )
        return FeedbackReportResponse.model_validate(report.to_dict())

    @application.get(
        "/v1/plans/{plan_id}/feedback",
        response_model=FeedbackCollectionResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Capability or plan not found"},
            410: {"model": ErrorResponse, "description": "Feedback capability expired"},
            503: {"model": ErrorResponse, "description": "Feedback store unavailable"},
        },
        tags=["human-outcome-evidence"],
        summary="Read the immutable reports visible to a plan capability",
    )
    def get_plan_feedback(
        plan_id: str,
        request: Request,
        capability: str = Header(alias="X-Feedback-Capability"),
    ):
        try:
            reports = feedback().list_reports(
                plan_id=plan_id,
                capability=capability,
            )
        except FeedbackNotFound:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="feedback_not_found",
                message="The feedback invitation was not found.",
            )
        except FeedbackExpired:
            return _error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="feedback_expired",
                message="The feedback invitation has expired.",
            )
        except FeedbackIntegrityError:
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_feedback_artifact",
                message="The persisted feedback evidence is invalid.",
            )
        except ValueError:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="feedback_not_found",
                message="The feedback invitation was not found.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="feedback_store_unavailable",
                message="The feedback evidence store is unavailable.",
            )
        return FeedbackCollectionResponse(
            plan_id=plan_id,
            reports=[
                FeedbackReportResponse.model_validate(report.to_dict())
                for report in reports
            ],
        )

    @application.get(
        "/v1/feedback-summary",
        response_model=FeedbackSummaryResponse,
        responses={
            503: {"model": ErrorResponse, "description": "Feedback store unavailable"},
        },
        tags=["human-outcome-evidence"],
        summary="Read privacy-minimized aggregate evidence with a sample-size gate",
    )
    def get_feedback_summary(request: Request):
        try:
            return FeedbackSummaryResponse.model_validate(feedback().public_summary())
        except (ValueError, OSError, RuntimeError, sqlite3.Error):
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="feedback_store_unavailable",
                message="The feedback evidence store is unavailable.",
            )

    @application.post(
        "/v1/operations",
        response_model=SideEffectOperationResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            409: {"model": ErrorResponse, "description": "Idempotency conflict"},
            422: {"model": ErrorResponse, "description": "Invalid sandbox operation"},
            503: {"model": ErrorResponse, "description": "Operation store unavailable"},
        },
        tags=["side-effect-operations"],
        summary="Persist a quote-bound sandbox operation pending human approval",
    )
    def request_side_effect_operation(
        payload: SideEffectOperationRequest,
        request: Request,
        principal: ControlPrincipal = Depends(require_operation_request_auth),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        if not REQUEST_ID_PATTERN.fullmatch(idempotency_key):
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_idempotency_key",
                message="Idempotency-Key must contain 1-128 safe characters.",
            )
        try:
            operation = operations().request(
                request_id=_request_id(request),
                tenant_id=principal.tenant_id,
                requested_by=principal.principal_id,
                operation_kind=payload.operation_kind,
                action_payload=payload.action.model_dump(mode="json"),
                quote=OperationQuote(**payload.quote.model_dump(mode="json")),
                idempotency_key=idempotency_key,
                approval_ttl_seconds=payload.approval_ttl_seconds,
            )
        except OperationIdempotencyConflict:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operation_idempotency_conflict",
                message="The idempotency key belongs to another quote-bound operation.",
            )
        except ValueError:
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_operation_request",
                message="The sandbox operation request could not be accepted.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "side-effect operation request failed",
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return operation_response(operation)

    @application.get(
        "/v1/operations/{operation_id}",
        response_model=SideEffectOperationResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Operation not found"},
            503: {"model": ErrorResponse, "description": "Operation store unavailable"},
        },
        tags=["side-effect-operations"],
        summary="Read one tenant-scoped operation and its receipt state",
    )
    def get_side_effect_operation(
        operation_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_operation_read_auth),
    ):
        if not re.fullmatch(r"op-[a-f0-9]{32}", operation_id):
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        try:
            operation = operations().get(
                operation_id,
                tenant_id=principal.tenant_id,
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("side-effect operation read failed")
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        if operation is None:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        return operation_response(operation)

    @application.post(
        "/v1/operations/{operation_id}/approve",
        response_model=SideEffectOperationResponse,
        responses={
            403: {"model": ErrorResponse, "description": "Self approval forbidden"},
            404: {"model": ErrorResponse, "description": "Operation not found"},
            409: {"model": ErrorResponse, "description": "Approval conflict"},
            410: {"model": ErrorResponse, "description": "Approval or quote expired"},
            503: {"model": ErrorResponse, "description": "Operation store unavailable"},
        },
        tags=["side-effect-operations"],
        summary="Approve the exact quote-bound operation as a separate principal",
    )
    def approve_side_effect_operation(
        operation_id: str,
        payload: OperationApprovalRequest,
        request: Request,
        principal: ControlPrincipal = Depends(require_operation_approve_auth),
    ):
        try:
            operation = operations().approve(
                operation_id=operation_id,
                tenant_id=principal.tenant_id,
                approved_by=principal.principal_id,
                expected_approval_sha256=payload.expected_approval_sha256,
            )
        except OperationSelfApprovalForbidden:
            return _error_response(
                request,
                status_code=status.HTTP_403_FORBIDDEN,
                code="operation_self_approval_forbidden",
                message="Requester and approver must be different principals.",
            )
        except OperationNotFound:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except OperationExpired:
            return _error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="operation_approval_expired",
                message="The operation approval or quote has expired.",
            )
        except (OperationApprovalConflict, InvalidOperationTransition):
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operation_approval_conflict",
                message="The operation cannot be approved with this fingerprint or state.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("side-effect operation approval failed")
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return operation_response(operation)

    @application.post(
        "/v1/operations/{operation_id}/deny",
        response_model=SideEffectOperationResponse,
        responses={
            403: {"model": ErrorResponse, "description": "Self denial forbidden"},
            404: {"model": ErrorResponse, "description": "Operation not found"},
            409: {"model": ErrorResponse, "description": "Denial conflict"},
            410: {"model": ErrorResponse, "description": "Approval or quote expired"},
            503: {"model": ErrorResponse, "description": "Operation store unavailable"},
        },
        tags=["side-effect-operations"],
        summary="Deny the exact quote-bound operation as a separate principal",
    )
    def deny_side_effect_operation(
        operation_id: str,
        payload: OperationDenialRequest,
        request: Request,
        principal: ControlPrincipal = Depends(require_operation_approve_auth),
    ):
        try:
            operation = operations().deny(
                operation_id=operation_id,
                tenant_id=principal.tenant_id,
                denied_by=principal.principal_id,
                expected_approval_sha256=payload.expected_approval_sha256,
                reason_code=payload.reason_code,
            )
        except OperationSelfApprovalForbidden:
            return _error_response(
                request,
                status_code=status.HTTP_403_FORBIDDEN,
                code="operation_self_approval_forbidden",
                message="Requester and approver must be different principals.",
            )
        except OperationNotFound:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except OperationExpired:
            return _error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="operation_approval_expired",
                message="The operation approval or quote has expired.",
            )
        except (OperationApprovalConflict, InvalidOperationTransition):
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operation_denial_conflict",
                message="The operation cannot be denied with this fingerprint or state.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("side-effect operation denial failed")
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return operation_response(operation)

    @application.get(
        "/v1/operations/{operation_id}/events",
        response_model=OperationEventsResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Operation not found"},
            503: {"model": ErrorResponse, "description": "Operation store unavailable"},
        },
        tags=["side-effect-operations"],
        summary="Replay append-only operation decisions after a cursor",
    )
    def get_side_effect_operation_events(
        operation_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_operation_read_auth),
        after_event_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        try:
            events = operations().events(
                operation_id,
                tenant_id=principal.tenant_id,
                after_event_id=after_event_id,
                limit=limit,
            )
        except OperationNotFound:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("side-effect operation event replay failed")
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return operation_events_response(
            operation_id,
            events,
            after_event_id=after_event_id,
            limit=limit,
        )

    @application.post(
        "/v1/operations/{operation_id}/reconcile",
        response_model=SideEffectOperationResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Operation not found"},
            409: {"model": ErrorResponse, "description": "Reconciliation unavailable"},
            503: {"model": ErrorResponse, "description": "Status provider unavailable"},
        },
        tags=["side-effect-operations"],
        summary="Resolve an uncertain sandbox operation through provider-bound status lookup",
    )
    def reconcile_side_effect_operation(
        operation_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_operation_reconcile_auth),
    ):
        if not re.fullmatch(r"op-[a-f0-9]{32}", operation_id):
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        try:
            operation, _ = operations().reconcile_uncertain(
                operation_id=operation_id,
                tenant_id=principal.tenant_id,
                actor_id=principal.principal_id,
            )
        except OperationNotFound:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except (OperationReconciliationUnavailable, InvalidOperationTransition) as exc:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operation_reconciliation_unavailable",
                message=str(exc),
            )
        except SandboxProviderFailure:
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_status_provider_unavailable",
                message="The sandbox status provider is unavailable.",
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            LOGGER.exception("side-effect operation reconciliation failed")
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_reconciliation_store_unavailable",
                message="The operation could not be reconciled safely.",
            )
        return operation_response(operation)

    @application.get(
        "/v1/operations/{operation_id}/reconciliations",
        response_model=OperationReconciliationsResponse,
        responses={
            404: {"model": ErrorResponse, "description": "Operation not found"},
            503: {"model": ErrorResponse, "description": "Operation store unavailable"},
        },
        tags=["side-effect-operations"],
        summary="Replay append-only provider status reconciliation evidence",
    )
    def get_side_effect_operation_reconciliations(
        operation_id: str,
        request: Request,
        principal: ControlPrincipal = Depends(require_operation_read_auth),
        after_reconciliation_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        try:
            reconciliations = operations().reconciliations(
                operation_id,
                tenant_id=principal.tenant_id,
                after_reconciliation_id=after_reconciliation_id,
                limit=limit,
            )
        except OperationNotFound:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            LOGGER.exception("side-effect operation reconciliation replay failed")
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return operation_reconciliations_response(
            operation_id,
            reconciliations,
            after_reconciliation_id=after_reconciliation_id,
            limit=limit,
        )

    @application.post(
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
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_idempotency_key",
                message="Idempotency-Key must contain 1-128 safe characters.",
            )
        application_request = payload.to_application_request()
        try:
            job = jobs().submit(
                request=application_request,
                request_id=_request_id(request),
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
                    extra={"request_id": _request_id(request)},
                )
                return _error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="clarification_store_unavailable",
                    message="The clarification continuation store is unavailable.",
                )
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="clarification_required",
                message="The planning request needs one clarification before it can be queued.",
                details=details,
            )
        except TenantAdmissionRejected as exc:
            return admission_rejected_response(request, exc)
        except IdempotencyConflict:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="idempotency_conflict",
                message="The idempotency key is already associated with another request.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job submission failed", extra={"request_id": _request_id(request)})
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        return job_response(job)

    @application.post(
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
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="clarification_store_unavailable",
                message="The clarification continuation store is unavailable.",
            )
        if existing_session is not None:
            session_tenant = str(existing_session.job_policy.get("tenant_id", "default"))
            if session_tenant != principal.tenant_id:
                return _error_response(
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
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="clarification_store_unavailable",
                message="The clarification continuation store is unavailable.",
            )

        if session.status == "completed":
            next_clarification = (session.result_payload or {}).get("next_clarification")
            if isinstance(next_clarification, dict):
                return _error_response(
                    request,
                    status_code=status.HTTP_409_CONFLICT,
                    code="clarification_required",
                    message="One additional clarification is required before the job can be queued.",
                    details=next_clarification,
                )
            job_id = str((session.result_payload or {}).get("job_id") or "")
            job = jobs().get(job_id, tenant_id=principal.tenant_id)
            if job is None:
                return _error_response(
                    request,
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="invalid_clarification_artifact",
                    message="The clarification continuation job artifact is invalid.",
                )
            return job_response(job)

        try:
            job = jobs().submit(
                request=resolved_request,
                request_id=_request_id(request),
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
                return _error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="clarification_store_unavailable",
                    message="The next clarification could not be persisted.",
                )
            return _error_response(
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
            return _error_response(
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
            return _error_response(
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
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="clarification_store_unavailable",
                message="The clarification job reference could not be persisted.",
            )
        return job_response(job)

    @application.get(
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
            return _error_response(
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
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_cursor_not_found",
                message="The planning job cursor was not found.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job listing failed", extra={"request_id": _request_id(request)})
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        return job_list_response(items, job_status=job_status, limit=limit)

    @application.get(
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
            return _error_response(
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
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="workload_evidence_limit_exceeded",
                message="The workload window exceeds the bounded evidence limit.",
            )
        except ValueError:
            LOGGER.exception(
                "durable workload evidence is invalid",
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_workload_evidence",
                message="The persisted workload evidence is invalid.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "durable workload aggregation failed",
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
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

    @application.get(
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
                extra={"request_id": _request_id(request)},
            )
            return _error_response(
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

    @application.post(
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
            return _error_response(
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
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        except InvalidJobTransition:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="invalid_job_transition",
                message="The planning job cannot be cancelled from its current status.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job cancellation failed", extra={"job_id": job_id})
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        return job_response(job)

    @application.post(
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
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        if not REQUEST_ID_PATTERN.fullmatch(idempotency_key):
            return _error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_idempotency_key",
                message="Idempotency-Key must contain 1-128 safe characters.",
            )
        try:
            job = jobs().replay(
                job_id=job_id,
                request_id=_request_id(request),
                idempotency_key=idempotency_key,
                tenant_id=principal.tenant_id,
                submitted_by=principal.principal_id,
                tenant_active_job_limit=principal.tenant_active_job_limit,
                tenant_submission_limit_per_minute=(
                    principal.tenant_submission_limit_per_minute
                ),
            )
        except JobNotFound:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        except TenantAdmissionRejected as exc:
            return admission_rejected_response(request, exc)
        except IdempotencyConflict:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="idempotency_conflict",
                message="The idempotency key is already associated with another operation.",
            )
        except InvalidJobTransition:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="invalid_job_transition",
                message="Only failed, dead-lettered, or timed-out jobs can be replayed.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job replay failed", extra={"job_id": job_id})
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        return job_response(job)

    @application.get(
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
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        try:
            job = jobs().get(job_id, tenant_id=principal.tenant_id)
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job lookup failed", extra={"request_id": _request_id(request)})
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        if job is None:
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        try:
            return job_response(job)
        except ValidationError:
            LOGGER.exception("persisted planning artifact is invalid", extra={"job_id": job_id})
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_job_artifact",
                message="The persisted planning artifact is invalid.",
            )

    @application.get(
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
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        try:
            diagnosis = jobs().diagnose(job_id, tenant_id=principal.tenant_id)
        except JobDiagnosticEventLimitExceeded:
            return _error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="job_diagnosis_event_limit_exceeded",
                message="The job event chain exceeds the bounded diagnostic limit.",
            )
        except ValueError:
            LOGGER.exception("planning job diagnostic evidence is invalid", extra={"job_id": job_id})
            return _error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_job_diagnosis",
                message="The persisted job diagnostic evidence is invalid.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("planning job diagnosis failed", extra={"job_id": job_id})
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_store_unavailable",
                message="The durable job store is temporarily unavailable.",
            )
        if diagnosis is None:
            return _error_response(
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

    @application.get(
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
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        try:
            job = jobs().get(job_id, tenant_id=principal.tenant_id)
            if job is None:
                return _error_response(
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
            return _error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="job_event_store_unavailable",
                message="The durable event store is temporarily unavailable.",
            )
        return job_events_response(job_id, events, after_event_id)

    @application.get(
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
            return _error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="job_not_found",
                message="The planning job was not found.",
            )
        cursor = after_event_id if after_event_id is not None else (last_event_id or 0)
        try:
            job = jobs().get(job_id, tenant_id=principal.tenant_id)
            if job is None:
                return _error_response(
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
            return _error_response(
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

    if public_demo:
        public_paths = {
            "/docs",
            "/docs/oauth2-redirect",
            "/healthz",
            "/openapi.json",
            "/readyz",
            "/v1/plans",
        }
        application.router.routes = [
            route
            for route in application.router.routes
            if getattr(route, "path", None) in public_paths
        ]
        application.title = "BJ-Pal Synthetic Public Demo API"
        application.description = (
            "A bounded, mock-only portfolio demo backed exclusively by the public "
            "synthetic dataset. It exposes no durable jobs, side effects, trials, "
            "feedback collection, or clarification continuations."
        )
        application.openapi_schema = None

    return application


app = create_app()
