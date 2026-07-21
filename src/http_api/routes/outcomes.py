"""Consent-bound trial and capability-bound feedback HTTP routes."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse

from outcomes import (
    FeedbackExpired,
    FeedbackIdempotencyConflict,
    FeedbackIntegrityError,
    FeedbackNotFound,
    FeedbackPhaseConflict,
    PlanFeedbackReport,
    TrialClosed,
    TrialConsentMismatch,
    TrialEnrollmentConflict,
    TrialIntegrityError,
    TrialNotActive,
    TrialNotFound,
    TrialParticipantWithdrawn,
)

from ..auth import ControlPrincipal
from ..responses import REQUEST_ID_PATTERN, error_response
from ..schemas import (
    ErrorResponse,
    FeedbackCollectionResponse,
    FeedbackReportResponse,
    FeedbackSubmitRequest,
    FeedbackSummaryResponse,
    TrialCreateRequest,
    TrialEnrollRequest,
    TrialEnrollmentInvitationRequest,
    TrialEnrollmentInvitationResponse,
    TrialEvidenceSnapshotResponse,
    TrialEvidenceSummaryResponse,
    TrialNoticeResponse,
    TrialParticipantEventResponse,
    TrialParticipantResponse,
)


AuthorizationDependency = Callable[..., ControlPrincipal]
FeedbackServiceProvider = Callable[[], "FeedbackExecutor"]


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


def trial_error_response(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, TrialNotFound):
        return error_response(
            request,
            status_code=status.HTTP_404_NOT_FOUND,
            code="trial_not_found",
            message="The trial or trial capability was not found.",
        )
    if isinstance(exc, TrialNotActive):
        return error_response(
            request,
            status_code=status.HTTP_410_GONE,
            code="trial_not_active",
            message="The trial collection window is not active.",
        )
    if isinstance(exc, TrialClosed):
        return error_response(
            request,
            status_code=status.HTTP_409_CONFLICT,
            code="trial_closed",
            message="The trial evidence has already been frozen.",
        )
    if isinstance(exc, TrialParticipantWithdrawn):
        return error_response(
            request,
            status_code=status.HTTP_409_CONFLICT,
            code="trial_participant_withdrawn",
            message="The trial participant has withdrawn.",
        )
    if isinstance(exc, TrialEnrollmentConflict):
        return error_response(
            request,
            status_code=status.HTTP_409_CONFLICT,
            code="trial_enrollment_conflict",
            message="The single-use trial enrollment capability was already consumed.",
        )
    if isinstance(exc, TrialConsentMismatch):
        return error_response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="trial_consent_mismatch",
            message="Consent must attest to the exact published trial notice.",
        )
    if isinstance(exc, TrialIntegrityError):
        return error_response(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="invalid_trial_artifact",
            message="The persisted trial evidence is invalid.",
        )
    return error_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="invalid_trial_request",
        message="The trial request did not match the evidence contract.",
    )



def build_outcomes_router(
    *,
    feedback: FeedbackServiceProvider,
    require_trial_manage_auth: AuthorizationDependency,
    require_trial_read_auth: AuthorizationDependency,
) -> APIRouter:
    """Build outcome-evidence routes from explicit runtime dependencies."""
    router = APIRouter()

    @router.post(
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
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @router.get(
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
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @router.post(
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
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @router.post(
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
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @router.post(
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
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @router.get(
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
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @router.post(
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
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="trial_store_unavailable",
                message="The trial evidence store is unavailable.",
            )

    @router.post(
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
            return error_response(
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
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="feedback_not_found",
                message="The feedback invitation was not found.",
            )
        except FeedbackExpired:
            return error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="feedback_expired",
                message="The feedback invitation has expired.",
            )
        except FeedbackIdempotencyConflict:
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="feedback_idempotency_conflict",
                message="The idempotency key belongs to another feedback report.",
            )
        except FeedbackPhaseConflict:
            return error_response(
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
            return error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_feedback_artifact",
                message="The persisted feedback evidence is invalid.",
            )
        except ValueError:
            return error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_feedback",
                message="The feedback did not match the evidence contract.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="feedback_store_unavailable",
                message="The feedback evidence store is unavailable.",
            )
        return FeedbackReportResponse.model_validate(report.to_dict())

    @router.get(
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
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="feedback_not_found",
                message="The feedback invitation was not found.",
            )
        except FeedbackExpired:
            return error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="feedback_expired",
                message="The feedback invitation has expired.",
            )
        except FeedbackIntegrityError:
            return error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_feedback_artifact",
                message="The persisted feedback evidence is invalid.",
            )
        except ValueError:
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="feedback_not_found",
                message="The feedback invitation was not found.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            return error_response(
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

    @router.get(
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
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="feedback_store_unavailable",
                message="The feedback evidence store is unavailable.",
            )


    return router
