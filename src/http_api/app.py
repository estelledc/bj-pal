"""FastAPI adapter for BJ-Pal's canonical application service."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import sqlite3
import uuid
from typing import Callable

from fastapi import FastAPI, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from application import PlanningService
from clarifications import ClarificationContinuationService
from data_profile import inspect_runtime_data
from jobs import PlanningJobService
from operations import SideEffectOperationService
from outcomes import PlanFeedbackService
from storage.legacy_retirement import (
    DEDICATED_REQUIRED_POLICY,
    inspect_legacy_retirement,
    state_layout_policy,
)

from .schemas import ErrorResponse, ReadinessResponse
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
from .responses import (
    REQUEST_ID_PATTERN,
    error_response as _error_response,
    request_id as _request_id,
)
from .public_surface import retain_public_routes
from .routes.outcomes import (
    FeedbackExecutor,
    build_outcomes_router,
)
from .routes.jobs import PlanningJobExecutor, build_jobs_router
from .routes.operations import SideEffectOperationExecutor, build_operations_router
from .routes.planning import PlanningExecutor, PlanningRouteSupport, build_planning_router
from .routes.system import build_system_router
from version import SERVICE_VERSION


LOGGER = logging.getLogger(__name__)


ReadinessProbe = Callable[[], ReadinessResponse]


def default_readiness_probe(
    *,
    job_store_probe: Callable[[], bool] | None = None,
) -> ReadinessResponse:
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
    job_store_ready = True
    if job_store_probe is not None:
        try:
            job_store_ready = job_store_probe()
        except (OSError, RuntimeError, sqlite3.Error):
            job_store_ready = False
        checks["durable_job_store"] = "ok" if job_store_ready else "failed"
    return ReadinessResponse(
        status="ready" if audit.ready and state_ready and job_store_ready else "not_ready",
        data_profile=audit.profile.name,
        classification=audit.profile.classification,
        checks=checks,
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

    def job_store_readiness_probe() -> ReadinessResponse:
        return default_readiness_probe(job_store_probe=lambda: jobs().probe())

    probe = readiness_probe or job_store_readiness_probe

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

    def clarifications() -> ClarificationContinuationService:
        nonlocal resolved_clarification_service
        if resolved_clarification_service is None:
            resolved_clarification_service = ClarificationContinuationService()
        return resolved_clarification_service

    planning_support = PlanningRouteSupport(
        feedback=feedback,
        clarifications=clarifications,
        public_demo=public_demo,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            if resolved_job_service is not None:
                close = getattr(resolved_job_service, "close", None)
                if callable(close):
                    close()

    application = FastAPI(
        title="BJ-Pal Planning API",
        version=SERVICE_VERSION,
        lifespan=lifespan,
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

    application.include_router(
        build_system_router(
            readiness_probe=probe,
            jobs=jobs,
            require_read_auth=require_read_auth,
        )
    )

    application.include_router(
        build_planning_router(
            planning_service=planning_service,
            support=planning_support,
            public_demo=public_demo,
        )
    )

    application.include_router(
        build_outcomes_router(
            feedback=feedback,
            require_trial_manage_auth=require_trial_manage_auth,
            require_trial_read_auth=require_trial_read_auth,
        )
    )

    application.include_router(
        build_operations_router(
            operations=operations,
            require_request_auth=require_operation_request_auth,
            require_read_auth=require_operation_read_auth,
            require_approve_auth=require_operation_approve_auth,
            require_reconcile_auth=require_operation_reconcile_auth,
        )
    )

    application.include_router(
        build_jobs_router(
            jobs=jobs,
            clarifications=planning_support.clarifications,
            issue_clarification=planning_support.issue_clarification,
            clarification_error_response=planning_support.clarification_error_response,
            require_submit_auth=require_submit_auth,
            require_read_auth=require_read_auth,
            require_control_auth=require_control_auth,
            require_replay_auth=require_replay_auth,
        )
    )

    if public_demo:
        retain_public_routes(application)
        application.title = "BJ-Pal Synthetic Public Demo API"
        application.description = (
            "A bounded, mock-only portfolio demo backed exclusively by the public "
            "synthetic dataset. It exposes no durable jobs, side effects, trials, "
            "feedback collection, or clarification continuations."
        )
        application.openapi_schema = None

    return application


app = create_app()
