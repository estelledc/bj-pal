"""Liveness, readiness, and privacy-minimized operational evidence routes."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, Depends, Query, Request, Response, status

from agents.tracing import trace_export_status
from jobs import JobWorkloadEvidenceLimitExceeded
from jobs.workload_health import validate_closed_window
from monitoring import OperationalAlertSnapshot

from ..auth import ControlPrincipal
from ..responses import error_response, request_id
from ..schemas import (
    ErrorResponse,
    HealthResponse,
    OperationalAlertSnapshotResponse,
    ReadinessResponse,
    TraceExportStatusResponse,
)
from version import SERVICE_VERSION


LOGGER = logging.getLogger(__name__)
ReadinessProbe = Callable[[], ReadinessResponse]
AuthorizationDependency = Callable[..., ControlPrincipal]


class WorkloadHealthExecutor(Protocol):
    def workload_health(
        self,
        *,
        tenant_id: str,
        window_start: str,
        window_end: str,
    ): ...


def build_system_router(
    *,
    readiness_probe: ReadinessProbe,
    jobs: Callable[[], WorkloadHealthExecutor],
    require_read_auth: AuthorizationDependency,
) -> APIRouter:
    """Build operational routes from explicit probes and service providers."""
    router = APIRouter()

    @router.get(
        "/healthz",
        response_model=HealthResponse,
        tags=["operations"],
        summary="Process liveness",
    )
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok", service="bj-pal", version=SERVICE_VERSION)

    @router.get(
        "/readyz",
        response_model=ReadinessResponse,
        tags=["operations"],
        summary="Dataset and database readiness",
    )
    def readyz(response: Response) -> ReadinessResponse:
        result = readiness_probe()
        if result.status != "ready":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return result

    @router.get(
        "/v1/trace-export-status",
        response_model=TraceExportStatusResponse,
        tags=["operations"],
        summary="Read privacy-minimized trace export health",
    )
    def get_trace_export_status(
        principal: ControlPrincipal = Depends(require_read_auth),
    ) -> TraceExportStatusResponse:
        del principal
        return TraceExportStatusResponse.model_validate(trace_export_status().to_dict())

    @router.get(
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
            return error_response(
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
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operational_alert_evidence_limit_exceeded",
                message="The alert window exceeds the bounded evidence limit.",
            )
        except ValueError:
            LOGGER.exception(
                "operational alert evidence is invalid",
                extra={"request_id": request_id(request)},
            )
            return error_response(
                request,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_operational_alert_evidence",
                message="The operational alert evidence is invalid.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "operational alert evaluation failed",
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
            "workload": (
                "/v1/planning-job-health"
                f"?window_start={snapshot.window_start}&window_end={snapshot.window_end}"
            ),
            "trace_export": "/v1/trace-export-status",
        }
        return OperationalAlertSnapshotResponse.model_validate(payload)

    return router
