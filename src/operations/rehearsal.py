"""Shared helpers for a visible, approval-gated sandbox booking rehearsal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib

from .models import OperationQuote, SideEffectOperation
from .service import SideEffectOperationService


DEMO_TENANT_ID = "demo-local"
DEMO_REQUESTER_ID = "demo-requester"
DEMO_APPROVER_ID = "demo-human-approver"
DEMO_RECONCILER_ID = "demo-status-reconciler"
DEMO_WORKER_ID = "demo-sandbox-worker"
SANDBOX_BOOKING_TERMS = (
    "BJ-Pal resume rehearsal only; no payment, merchant request, message, "
    "or other external write is performed."
)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SandboxBookingDraft:
    """The exact action and quote shown before a distinct principal approves it."""

    request_id: str
    tenant_id: str
    requested_by: str
    action_payload: dict
    quote: OperationQuote
    idempotency_key: str


def build_sandbox_booking_draft(
    *,
    session_id: str,
    poi_id: str,
    poi_name: str,
    target_time: str,
    party_size: int,
    amount_minor: int,
    now: datetime | None = None,
) -> SandboxBookingDraft:
    """Build one explicit sandbox quote without storing contact or payment data."""
    if not session_id.strip():
        raise ValueError("session_id must not be empty")
    if not poi_id.strip() or not poi_name.strip():
        raise ValueError("poi_id and poi_name must not be empty")
    if party_size < 1:
        raise ValueError("party_size must be positive")
    if amount_minor < 0:
        raise ValueError("amount_minor must be non-negative")

    issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    identity = _digest(
        "|".join(
            (
                session_id,
                poi_id,
                poi_name,
                target_time,
                str(party_size),
                str(amount_minor),
            )
        )
    )
    short_identity = identity[:24]
    return SandboxBookingDraft(
        request_id=f"demo-request-{short_identity}",
        tenant_id=DEMO_TENANT_ID,
        requested_by=DEMO_REQUESTER_ID,
        action_payload={
            "poi_id": poi_id,
            "poi_name": poi_name,
            "target_time": target_time,
            "party_size": party_size,
            "contact_reference": f"demo-contact-{identity[24:40]}",
        },
        quote=OperationQuote(
            provider="bj-pal-sandbox",
            reference=f"demo-quote-{short_identity}",
            valid_until=_timestamp(issued_at + timedelta(minutes=15)),
            currency="CNY",
            amount_minor=amount_minor,
            terms_sha256=_digest(SANDBOX_BOOKING_TERMS),
            sandbox=True,
        ),
        idempotency_key=f"demo-booking-{short_identity}",
    )


def request_sandbox_booking(
    service: SideEffectOperationService,
    draft: SandboxBookingDraft,
) -> SideEffectOperation:
    """Persist the quote-bound request; this does not approve or execute it."""
    return service.request(
        request_id=draft.request_id,
        tenant_id=draft.tenant_id,
        requested_by=draft.requested_by,
        operation_kind="restaurant_booking",
        action_payload=draft.action_payload,
        quote=draft.quote,
        idempotency_key=draft.idempotency_key,
    )


def approve_sandbox_booking(
    service: SideEffectOperationService,
    operation: SideEffectOperation,
    *,
    approved_by: str = DEMO_APPROVER_ID,
) -> SideEffectOperation:
    """Approve the exact fingerprint as a principal distinct from the requester."""
    return service.approve(
        operation_id=operation.operation_id,
        tenant_id=operation.tenant_id,
        approved_by=approved_by,
        expected_approval_sha256=operation.approval_sha256,
    )


def execute_next_sandbox_booking(
    service: SideEffectOperationService,
    *,
    worker_id: str = DEMO_WORKER_ID,
) -> SideEffectOperation | None:
    """Run one approved sandbox operation through the normal leased worker path."""
    return service.run_once(worker_id=worker_id)
