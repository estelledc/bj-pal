"""Approval-gated side-effect operation HTTP routes."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, Depends, Header, Query, Request, status

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
)

from ..auth import ControlPrincipal
from ..responses import REQUEST_ID_PATTERN, error_response, request_id
from ..schemas import (
    ErrorResponse,
    OperationApprovalRequest,
    OperationDenialRequest,
    OperationEventResponse,
    OperationEventsResponse,
    OperationReconciliationResponse,
    OperationReconciliationsResponse,
    SideEffectOperationRequest,
    SideEffectOperationResponse,
)


LOGGER = logging.getLogger(__name__)
OperationServiceProvider = Callable[[], "SideEffectOperationExecutor"]
AuthorizationDependency = Callable[..., ControlPrincipal]


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


def _operation_response(
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


def _operation_events_response(
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
        events=[OperationEventResponse.model_validate(event.__dict__) for event in events],
        next_after_event_id=next_cursor,
        links={
            "self": f"{base}?after_event_id={after_event_id}&limit={limit}",
            "next": f"{base}?after_event_id={next_cursor}&limit={limit}",
        },
    )


def _operation_reconciliations_response(
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
            "next": f"{base}?after_reconciliation_id={next_cursor}&limit={limit}",
        },
    )


def build_operations_router(
    *,
    operations: OperationServiceProvider,
    require_request_auth: AuthorizationDependency,
    require_read_auth: AuthorizationDependency,
    require_approve_auth: AuthorizationDependency,
    require_reconcile_auth: AuthorizationDependency,
) -> APIRouter:
    """Build the operations router from explicit runtime dependencies."""
    router = APIRouter()

    @router.post(
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
        principal: ControlPrincipal = Depends(require_request_auth),
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
            operation = operations().request(
                request_id=request_id(request),
                tenant_id=principal.tenant_id,
                requested_by=principal.principal_id,
                operation_kind=payload.operation_kind,
                action_payload=payload.action.model_dump(mode="json"),
                quote=OperationQuote(**payload.quote.model_dump(mode="json")),
                idempotency_key=idempotency_key,
                approval_ttl_seconds=payload.approval_ttl_seconds,
            )
        except OperationIdempotencyConflict:
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operation_idempotency_conflict",
                message="The idempotency key belongs to another quote-bound operation.",
            )
        except ValueError:
            return error_response(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_operation_request",
                message="The sandbox operation request could not be accepted.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception(
                "side-effect operation request failed",
                extra={"request_id": request_id(request)},
            )
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return _operation_response(operation)

    @router.get(
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
        principal: ControlPrincipal = Depends(require_read_auth),
    ):
        if not re.fullmatch(r"op-[a-f0-9]{32}", operation_id):
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        try:
            operation = operations().get(operation_id, tenant_id=principal.tenant_id)
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("side-effect operation read failed")
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        if operation is None:
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        return _operation_response(operation)

    @router.post(
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
        principal: ControlPrincipal = Depends(require_approve_auth),
    ):
        try:
            operation = operations().approve(
                operation_id=operation_id,
                tenant_id=principal.tenant_id,
                approved_by=principal.principal_id,
                expected_approval_sha256=payload.expected_approval_sha256,
            )
        except OperationSelfApprovalForbidden:
            return error_response(
                request,
                status_code=status.HTTP_403_FORBIDDEN,
                code="operation_self_approval_forbidden",
                message="Requester and approver must be different principals.",
            )
        except OperationNotFound:
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except OperationExpired:
            return error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="operation_approval_expired",
                message="The operation approval or quote has expired.",
            )
        except (OperationApprovalConflict, InvalidOperationTransition):
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operation_approval_conflict",
                message="The operation cannot be approved with this fingerprint or state.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("side-effect operation approval failed")
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return _operation_response(operation)

    @router.post(
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
        principal: ControlPrincipal = Depends(require_approve_auth),
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
            return error_response(
                request,
                status_code=status.HTTP_403_FORBIDDEN,
                code="operation_self_approval_forbidden",
                message="Requester and approver must be different principals.",
            )
        except OperationNotFound:
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except OperationExpired:
            return error_response(
                request,
                status_code=status.HTTP_410_GONE,
                code="operation_approval_expired",
                message="The operation approval or quote has expired.",
            )
        except (OperationApprovalConflict, InvalidOperationTransition):
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operation_denial_conflict",
                message="The operation cannot be denied with this fingerprint or state.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("side-effect operation denial failed")
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return _operation_response(operation)

    @router.get(
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
        principal: ControlPrincipal = Depends(require_read_auth),
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
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except (OSError, RuntimeError, sqlite3.Error):
            LOGGER.exception("side-effect operation event replay failed")
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return _operation_events_response(
            operation_id,
            events,
            after_event_id=after_event_id,
            limit=limit,
        )

    @router.post(
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
        principal: ControlPrincipal = Depends(require_reconcile_auth),
    ):
        if not re.fullmatch(r"op-[a-f0-9]{32}", operation_id):
            return error_response(
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
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except (OperationReconciliationUnavailable, InvalidOperationTransition) as exc:
            return error_response(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="operation_reconciliation_unavailable",
                message=str(exc),
            )
        except SandboxProviderFailure:
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_status_provider_unavailable",
                message="The sandbox status provider is unavailable.",
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            LOGGER.exception("side-effect operation reconciliation failed")
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_reconciliation_store_unavailable",
                message="The operation could not be reconciled safely.",
            )
        return _operation_response(operation)

    @router.get(
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
        principal: ControlPrincipal = Depends(require_read_auth),
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
            return error_response(
                request,
                status_code=status.HTTP_404_NOT_FOUND,
                code="operation_not_found",
                message="The side-effect operation was not found.",
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            LOGGER.exception("side-effect operation reconciliation replay failed")
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="operation_store_unavailable",
                message="The side-effect operation store is unavailable.",
            )
        return _operation_reconciliations_response(
            operation_id,
            reconciliations,
            after_reconciliation_id=after_reconciliation_id,
            limit=limit,
        )

    return router
