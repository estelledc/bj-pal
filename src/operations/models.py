"""Typed state for approval-gated sandbox side effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


OperationKind = Literal["restaurant_booking"]
OperationStatus = Literal[
    "pending_approval",
    "approved",
    "denied",
    "expired",
    "executing",
    "succeeded",
    "failed",
    "uncertain",
]
OperationEventType = Literal[
    "requested",
    "request_reused",
    "approved",
    "denied",
    "expired",
    "execution_started",
    "execution_succeeded",
    "execution_failed",
    "execution_uncertain",
]


@dataclass(frozen=True)
class OperationQuote:
    provider: str
    reference: str
    valid_until: str
    currency: str
    amount_minor: int
    terms_sha256: str
    sandbox: bool

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "reference": self.reference,
            "valid_until": self.valid_until,
            "currency": self.currency,
            "amount_minor": self.amount_minor,
            "terms_sha256": self.terms_sha256,
            "sandbox": self.sandbox,
        }


@dataclass(frozen=True)
class SideEffectOperation:
    operation_id: str
    request_id: str
    tenant_id: str
    requested_by: str
    operation_kind: OperationKind
    status: OperationStatus
    action_payload: dict
    request_sha256: str
    idempotency_key: str
    quote: OperationQuote
    approval_sha256: str
    approval_expires_at: str
    approved_by: str | None
    approved_at: str | None
    denied_by: str | None
    denied_at: str | None
    denial_reason_code: str | None
    execution_owner: str | None
    execution_lease_expires_at: str | None
    attempt: int
    provider_operation_id: str | None
    receipt_payload: dict | None
    receipt_sha256: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class OperationEvent:
    event_id: int
    operation_id: str
    event_type: OperationEventType
    actor_id: str
    payload: dict
    created_at: str


@dataclass(frozen=True)
class OperationReconciliation:
    reconciliation_id: int
    operation_id: str
    tenant_id: str
    actor_id: str
    outcome: Literal["confirmed", "rejected", "still_unknown", "not_found"]
    provider_operation_id: str
    evidence_payload: dict
    evidence_sha256: str
    receipt_sha256: str | None
    created_at: str
