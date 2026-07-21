"""Synchronous planning and clarification-continuation HTTP routes."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from application import (
    ExecutionBudgetExceeded,
    ModelOutputContractError,
    PlanRequest,
    PlanResult,
    PlanningCallbacks,
    PlanningClarificationRequired,
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
from outcomes import (
    TrialClosed,
    TrialIntegrityError,
    TrialNotActive,
    TrialNotFound,
    TrialParticipantWithdrawn,
    sha256_json,
)

from ..responses import error_response, request_id
from ..schemas import (
    ClarificationContinueRequest,
    ErrorResponse,
    PlanCreateRequest,
    PlanCreateResponse,
)
from .outcomes import FeedbackExecutor, trial_error_response


LOGGER = logging.getLogger(__name__)
ClarificationServiceProvider = Callable[[], ClarificationContinuationService]
FeedbackServiceProvider = Callable[[], FeedbackExecutor]


class PlanningExecutor(Protocol):
    def execute(self, request: PlanRequest, **kwargs) -> PlanResult: ...


class PlanningRouteSupport:
    """Shared planning/clarification behavior used by sync and durable routers."""

    def __init__(
        self,
        *,
        feedback: FeedbackServiceProvider,
        clarifications: ClarificationServiceProvider,
        public_demo: bool,
    ) -> None:
        self._feedback = feedback
        self._clarifications = clarifications
        self.public_demo = public_demo

    def feedback(self) -> FeedbackExecutor:
        return self._feedback()

    def clarifications(self) -> ClarificationContinuationService:
        return self._clarifications()

    def deliver_plan_payload(
        self,
        payload: dict,
        *,
        trial_participant_capability: str | None = None,
    ) -> PlanCreateResponse:
        """Add an ephemeral feedback capability without changing the plan artifact."""
        canonical = PlanCreateResponse.model_validate(payload)
        if self.public_demo:
            return canonical
        execution = canonical.execution
        if execution is None:
            return canonical
        invitation = self.feedback().issue(
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

    def issue_clarification(
        self,
        *,
        application_request: PlanRequest,
        error: PlanningClarificationRequired,
        delivery: str,
        job_policy: dict | None = None,
    ) -> dict:
        session = self.clarifications().issue(
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

    @staticmethod
    def clarification_error_response(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        if isinstance(exc, ClarificationIntegrityError):
            return error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_clarification_artifact",
                message="The clarification continuation artifact is invalid.",
            )
        if isinstance(exc, ClarificationNotFound):
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="clarification_not_found",
                message="The clarification continuation was not found.",
            )
        if isinstance(exc, ClarificationExpired):
            return error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="clarification_expired",
                message="The clarification continuation has expired.",
            )
        if isinstance(exc, ClarificationInProgress):
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="clarification_in_progress",
                message="The clarification continuation is already executing.",
            )
        if isinstance(exc, ClarificationResolutionConflict):
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="clarification_resolution_conflict",
                message="The clarification was already resolved differently.",
            )
        if isinstance(exc, InvalidClarificationTransition):
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="invalid_clarification_transition",
                message="The clarification continuation cannot perform this transition.",
            )
        return error_response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_clarification_resolution",
            message="The clarification resolution is invalid.",
        )


def build_planning_router(
    *,
    planning_service: PlanningExecutor,
    support: PlanningRouteSupport,
    public_demo: bool,
) -> APIRouter:
    """Build synchronous planning routes from explicit runtime dependencies."""
    router = APIRouter()

    @router.post(
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
            return error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="public_demo_user_id_unsupported",
                message="The public demo does not accept user identifiers.",
            )
        if public_demo and trial_participant_capability is not None:
            return error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="public_demo_capability_unsupported",
                message="The public demo does not accept trial or feedback capabilities.",
            )
        if trial_participant_capability is not None:
            try:
                support.feedback().authorize_trial_participant(
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
                return error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="feedback_store_unavailable",
                    message="The feedback evidence store is unavailable.",
                )
        application_request = payload.to_application_request()
        try:
            result = planning_service.execute(
                application_request,
                callbacks=PlanningCallbacks(correlation_id=request_id(request)),
            )
        except PlanningClarificationRequired as exc:
            if public_demo:
                details = {
                    "requirements": exc.decision.to_dict(),
                    "continuation_available": False,
                }
                if exc.constraints is not None:
                    details["constraints"] = exc.constraints.to_dict()
                return error_response(
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
                details = support.issue_clarification(
                    application_request=application_request,
                    error=exc,
                    delivery="sync",
                )
            except (OSError, RuntimeError, sqlite3.Error):
                LOGGER.exception(
                    "clarification continuation persistence failed",
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
                message="The planning request needs one clarification before execution.",
                details=details,
            )
        except ExecutionBudgetExceeded as exc:
            return error_response(
                request,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code=exc.code,
                message="The server execution budget stopped this planning request.",
                details=exc.safe_details(),
            )
        except ModelOutputContractError as exc:
            return error_response(
                request,
                status_code=status.HTTP_502_BAD_GATEWAY,
                code=exc.code,
                message="The model could not produce a valid grounded plan.",
                details=exc.safe_details(),
            )
        except ValueError:
            return error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_planning_request",
                message="The planning request could not be accepted.",
            )
        except (FileNotFoundError, OSError, RuntimeError):
            LOGGER.exception("planning request failed", extra={"request_id": request_id(request)})
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="planning_unavailable",
                message="The planning service is temporarily unavailable.",
            )
        try:
            return support.deliver_plan_payload(
                result.to_dict(),
                trial_participant_capability=trial_participant_capability,
            )
        except (ValidationError, ValueError):
            LOGGER.exception(
                "planning service returned an invalid contract",
                extra={"request_id": request_id(request)},
            )
            return error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_planning_result",
                message="The planning service returned an invalid result.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "plan feedback invitation persistence failed",
                extra={"request_id": request_id(request)},
            )
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="feedback_store_unavailable",
                message="The feedback evidence store is unavailable.",
            )

    @router.post(
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
                support.feedback().authorize_trial_participant(
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
                return error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="feedback_store_unavailable",
                    message="The feedback evidence store is unavailable.",
                )
        owner = f"sync-{uuid.uuid4().hex}"
        try:
            session, resolved_request = support.clarifications().resolve_request(
                continuation_id=continuation_id,
                delivery="sync",
                option_id=payload.option_id,
                answer=payload.answer,
            )
            session = support.clarifications().claim_execution(
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
            return support.clarification_error_response(request, exc)
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "clarification continuation store failed",
                extra={"request_id": request_id(request)},
            )
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
                    message="One additional clarification is required before execution.",
                    details=next_clarification,
                )
            try:
                return support.deliver_plan_payload(
                    session.result_payload or {},
                    trial_participant_capability=trial_participant_capability,
                )
            except (ValidationError, ValueError):
                return error_response(
                    request,
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="invalid_clarification_artifact",
                    message="The clarification continuation artifact is invalid.",
                )
            except (OSError, RuntimeError, sqlite3.Error):
                return error_response(
                    request,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="feedback_store_unavailable",
                    message="The feedback evidence store is unavailable.",
                )

        try:
            result = planning_service.execute(
                resolved_request,
                callbacks=PlanningCallbacks(correlation_id=request_id(request)),
            )
        except PlanningClarificationRequired as exc:
            try:
                details = support.issue_clarification(
                    application_request=resolved_request,
                    error=exc,
                    delivery="sync",
                )
                support.clarifications().complete(
                    continuation_id=continuation_id,
                    owner=owner,
                    result_payload={"next_clarification": details},
                )
            except (OSError, RuntimeError, sqlite3.Error):
                support.clarifications().release_execution(
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
                message="One additional clarification is required before execution.",
                details=details,
            )
        except ExecutionBudgetExceeded as exc:
            support.clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return error_response(
                request,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code=exc.code,
                message="The server execution budget stopped this planning request.",
                details=exc.safe_details(),
            )
        except ModelOutputContractError as exc:
            support.clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return error_response(
                request,
                status_code=status.HTTP_502_BAD_GATEWAY,
                code=exc.code,
                message="The model could not produce a valid grounded plan.",
                details=exc.safe_details(),
            )
        except ValueError:
            support.clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_planning_request",
                message="The clarified planning request could not be accepted.",
            )
        except (FileNotFoundError, OSError, RuntimeError):
            support.clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            LOGGER.exception(
                "clarified planning request failed",
                extra={"request_id": request_id(request)},
            )
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="planning_unavailable",
                message="The planning service is temporarily unavailable.",
            )

        result_payload = result.to_dict()
        try:
            PlanCreateResponse.model_validate(result_payload)
            support.clarifications().complete(
                continuation_id=continuation_id,
                owner=owner,
                result_payload=result_payload,
            )
        except ValidationError:
            support.clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_planning_result",
                message="The planning service returned an invalid result.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            support.clarifications().release_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="clarification_store_unavailable",
                message="The clarification result could not be persisted.",
            )
        try:
            return support.deliver_plan_payload(
                result_payload,
                trial_participant_capability=trial_participant_capability,
            )
        except (OSError, RuntimeError, sqlite3.Error):
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="feedback_store_unavailable",
                message="The feedback evidence store is unavailable.",
            )


    return router
