"""Worker orchestration for approval-gated sandbox side effects."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Protocol

from .models import OperationQuote, SideEffectOperation
from .repository import (
    RECEIPT_VERSION,
    STATUS_LOOKUP_VERSION,
    InvalidOperationTransition,
    OperationNotFound,
    OperationReconciliationUnavailable,
    SideEffectOperationRepository,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SandboxProviderFailure(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        uncertain: bool,
        provider_operation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.uncertain = uncertain
        self.provider_operation_id = provider_operation_id


class SandboxOperationProvider(Protocol):
    def execute(self, operation: SideEffectOperation) -> dict: ...

    def lookup(self, operation: SideEffectOperation) -> dict: ...


class DeterministicSandboxBookingProvider:
    """Explicitly synthetic provider; it never performs network or real booking I/O."""

    provider_name = "bj-pal-sandbox"

    def __init__(
        self,
        *,
        outcome: str = "confirmed",
        lookup_outcome: str = "confirmed",
    ) -> None:
        if outcome not in {"confirmed", "rejected", "preflight_failure", "uncertain"}:
            raise ValueError("unsupported sandbox outcome")
        if lookup_outcome not in {
            "confirmed",
            "rejected",
            "still_unknown",
            "not_found",
        }:
            raise ValueError("unsupported sandbox lookup outcome")
        self.outcome = outcome
        self.lookup_outcome = lookup_outcome

    def execute(self, operation: SideEffectOperation) -> dict:
        if operation.quote.provider != self.provider_name or not operation.quote.sandbox:
            raise SandboxProviderFailure(
                code="sandbox_provider_mismatch",
                message="Operation is not bound to the sandbox provider.",
                uncertain=False,
            )
        if self.outcome == "preflight_failure":
            raise SandboxProviderFailure(
                code="sandbox_preflight_failure",
                message="Sandbox provider failed before invocation.",
                uncertain=False,
            )
        provider_operation_id = f"sbx-{operation.operation_id[3:19]}"
        if self.outcome == "uncertain":
            raise SandboxProviderFailure(
                code="sandbox_timeout_after_invoke",
                message="Sandbox provider timed out after invocation.",
                uncertain=True,
                provider_operation_id=provider_operation_id,
            )
        response = {
            "provider_operation_id": provider_operation_id,
            "outcome": self.outcome,
            "quote_reference": operation.quote.reference,
            "operation_kind": operation.operation_kind,
            "sandbox": True,
        }
        return {
            "version": RECEIPT_VERSION,
            "operation_id": operation.operation_id,
            "request_sha256": operation.request_sha256,
            "provider": self.provider_name,
            "provider_operation_id": provider_operation_id,
            "outcome": self.outcome,
            "executed_at": _timestamp(),
            "response_sha256": _sha256(response),
            "sandbox": True,
        }

    def lookup(self, operation: SideEffectOperation) -> dict:
        if operation.quote.provider != self.provider_name or not operation.quote.sandbox:
            raise SandboxProviderFailure(
                code="sandbox_provider_mismatch",
                message="Operation is not bound to the sandbox provider.",
                uncertain=False,
            )
        if not operation.provider_operation_id:
            raise SandboxProviderFailure(
                code="provider_operation_reference_missing",
                message="Status lookup requires a provider operation reference.",
                uncertain=True,
            )
        provider_payload = {
            "provider_operation_id": operation.provider_operation_id,
            "outcome": self.lookup_outcome,
            "quote_reference": operation.quote.reference,
            "sandbox": True,
        }
        return {
            "version": STATUS_LOOKUP_VERSION,
            "operation_id": operation.operation_id,
            "request_sha256": operation.request_sha256,
            "provider": self.provider_name,
            "provider_operation_id": operation.provider_operation_id,
            "outcome": self.lookup_outcome,
            "observed_at": _timestamp(),
            "provider_payload": provider_payload,
            "response_sha256": _sha256(provider_payload),
            "sandbox": True,
        }


class SideEffectOperationService:
    def __init__(
        self,
        *,
        repository: SideEffectOperationRepository | None = None,
        provider: SandboxOperationProvider | None = None,
    ) -> None:
        self.repository = repository or SideEffectOperationRepository()
        self.provider = provider or DeterministicSandboxBookingProvider()

    def request(self, **kwargs) -> SideEffectOperation:
        return self.repository.request(**kwargs)

    def approve(self, **kwargs) -> SideEffectOperation:
        return self.repository.approve(**kwargs)

    def deny(self, **kwargs) -> SideEffectOperation:
        return self.repository.deny(**kwargs)

    def get(self, operation_id: str, *, tenant_id: str | None = None):
        return self.repository.get(operation_id, tenant_id=tenant_id)

    def events(self, operation_id: str, **kwargs):
        return self.repository.list_events(operation_id, **kwargs)

    def reconciliations(self, operation_id: str, **kwargs):
        return self.repository.list_reconciliations(operation_id, **kwargs)

    def reconcile_uncertain(
        self,
        *,
        operation_id: str,
        tenant_id: str,
        actor_id: str,
    ):
        operation = self.repository.get(operation_id, tenant_id=tenant_id)
        if operation is None:
            raise OperationNotFound("side-effect operation was not found")
        if operation.status != "uncertain":
            raise InvalidOperationTransition(
                f"cannot reconcile an operation in status {operation.status}"
            )
        if not operation.provider_operation_id:
            raise OperationReconciliationUnavailable(
                "uncertain operation has no provider operation reference"
            )
        evidence = self.provider.lookup(operation)
        return self.repository.reconcile_uncertain(
            operation_id=operation_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            lookup_evidence=evidence,
        )

    def run_once(
        self,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 30,
    ) -> SideEffectOperation | None:
        resolved_worker = worker_id or f"operation-worker-{uuid.uuid4().hex[:12]}"
        operation = self.repository.claim_next(
            worker_id=resolved_worker,
            lease_seconds=lease_seconds,
        )
        if operation is None:
            return None
        try:
            receipt = self.provider.execute(operation)
        except SandboxProviderFailure as exc:
            return self.repository.fail_execution(
                operation_id=operation.operation_id,
                worker_id=resolved_worker,
                error_code=exc.code,
                error_message=exc.message,
                uncertain=exc.uncertain,
                provider_operation_id=exc.provider_operation_id,
            )
        return self.repository.complete_with_receipt(
            operation_id=operation.operation_id,
            worker_id=resolved_worker,
            receipt_payload=receipt,
        )


__all__ = [
    "DeterministicSandboxBookingProvider",
    "OperationQuote",
    "SandboxProviderFailure",
    "SideEffectOperationService",
]
