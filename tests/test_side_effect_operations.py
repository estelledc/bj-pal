from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from operations import (
    DeterministicSandboxBookingProvider,
    OperationApprovalConflict,
    OperationExpired,
    OperationIdempotencyConflict,
    OperationNotFound,
    OperationQuote,
    OperationReconciliationUnavailable,
    OperationSelfApprovalForbidden,
    SideEffectOperationRepository,
    SideEffectOperationService,
)
from operations import repository as operation_repository_module


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _quote(now: datetime, *, reference: str = "quote-001") -> OperationQuote:
    return OperationQuote(
        provider="bj-pal-sandbox",
        reference=reference,
        valid_until=_timestamp(now + timedelta(minutes=10)),
        currency="CNY",
        amount_minor=12_800,
        terms_sha256=hashlib.sha256(b"sandbox terms v1").hexdigest(),
        sandbox=True,
    )


def _action(*, poi_name: str = "测试餐厅") -> dict:
    return {
        "poi_id": "poi-sandbox-001",
        "poi_name": poi_name,
        "target_time": "18:30",
        "party_size": 2,
        "contact_reference": "contact-ref-001",
    }


def _request(
    repository: SideEffectOperationRepository,
    *,
    now: datetime,
    key: str = "booking-key-001",
    tenant_id: str = "tenant-alpha",
):
    return repository.request(
        request_id=f"request-{key}",
        tenant_id=tenant_id,
        requested_by="agent-requester",
        operation_kind="restaurant_booking",
        action_payload=_action(),
        quote=_quote(now),
        idempotency_key=key,
    )


def test_quote_bound_approval_is_separated_idempotent_and_receipted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 7, 20, tzinfo=timezone.utc)]
    monkeypatch.setattr(operation_repository_module, "_utc_now", lambda: clock[0])
    repository = SideEffectOperationRepository(tmp_path / "operations.db")
    service = SideEffectOperationService(repository=repository)
    requested = _request(repository, now=clock[0])
    reused = _request(repository, now=clock[0])

    assert reused.operation_id == requested.operation_id
    assert requested.status == "pending_approval"
    assert requested.quote.sandbox is True
    assert requested.receipt_payload is None
    with pytest.raises(OperationIdempotencyConflict):
        repository.request(
            request_id="request-conflict",
            tenant_id="tenant-alpha",
            requested_by="agent-requester",
            operation_kind="restaurant_booking",
            action_payload=_action(poi_name="另一家餐厅"),
            quote=_quote(clock[0]),
            idempotency_key="booking-key-001",
        )
    with pytest.raises(OperationSelfApprovalForbidden):
        repository.approve(
            operation_id=requested.operation_id,
            tenant_id="tenant-alpha",
            approved_by="agent-requester",
            expected_approval_sha256=requested.approval_sha256,
        )
    with pytest.raises(OperationApprovalConflict):
        repository.approve(
            operation_id=requested.operation_id,
            tenant_id="tenant-alpha",
            approved_by="human-approver",
            expected_approval_sha256="0" * 64,
        )

    approved = repository.approve(
        operation_id=requested.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=requested.approval_sha256,
    )
    approved_again = repository.approve(
        operation_id=requested.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=requested.approval_sha256,
    )
    completed = service.run_once(worker_id="sandbox-worker")

    assert approved_again == approved
    assert completed is not None and completed.status == "succeeded"
    assert completed.receipt_payload is not None
    assert completed.receipt_payload["version"] == "side_effect_receipt_v1"
    assert completed.receipt_payload["operation_id"] == requested.operation_id
    assert completed.receipt_payload["request_sha256"] == requested.request_sha256
    assert completed.receipt_payload["sandbox"] is True
    assert completed.receipt_sha256 is not None
    assert [event.event_type for event in repository.list_events(
        requested.operation_id,
        tenant_id="tenant-alpha",
    )] == [
        "requested",
        "request_reused",
        "approved",
        "execution_started",
        "execution_succeeded",
    ]
    with sqlite3.connect(repository.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE side_effect_operation_events SET actor_id = 'tampered'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM side_effect_operation_events")


def test_denial_and_tenant_scope_fail_closed(tmp_path: Path, monkeypatch) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(operation_repository_module, "_utc_now", lambda: now)
    repository = SideEffectOperationRepository(tmp_path / "operations.db")
    operation = _request(repository, now=now)

    with pytest.raises(OperationNotFound):
        repository.deny(
            operation_id=operation.operation_id,
            tenant_id="tenant-beta",
            denied_by="beta-approver",
            expected_approval_sha256=operation.approval_sha256,
            reason_code="user_declined",
        )
    denied = repository.deny(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        denied_by="human-approver",
        expected_approval_sha256=operation.approval_sha256,
        reason_code="user_declined",
    )

    assert denied.status == "denied"
    assert denied.denied_by == "human-approver"
    assert repository.claim_next(worker_id="worker") is None
    assert repository.get(operation.operation_id, tenant_id="tenant-beta") is None


def test_pending_or_approved_operation_expires_with_its_quote(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 7, 20, tzinfo=timezone.utc)]
    monkeypatch.setattr(operation_repository_module, "_utc_now", lambda: clock[0])
    repository = SideEffectOperationRepository(tmp_path / "operations.db")
    pending = repository.request(
        request_id="request-pending-expiry",
        tenant_id="tenant-alpha",
        requested_by="agent-requester",
        operation_kind="restaurant_booking",
        action_payload=_action(),
        quote=_quote(clock[0], reference="quote-pending"),
        idempotency_key="pending-expiry",
        approval_ttl_seconds=1,
    )
    approved = repository.request(
        request_id="request-approved-expiry",
        tenant_id="tenant-alpha",
        requested_by="agent-requester",
        operation_kind="restaurant_booking",
        action_payload=_action(),
        quote=_quote(clock[0], reference="quote-approved"),
        idempotency_key="approved-expiry",
        approval_ttl_seconds=1,
    )
    repository.approve(
        operation_id=approved.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=approved.approval_sha256,
    )
    clock[0] += timedelta(seconds=2)

    with pytest.raises(OperationExpired):
        repository.approve(
            operation_id=pending.operation_id,
            tenant_id="tenant-alpha",
            approved_by="human-approver",
            expected_approval_sha256=pending.approval_sha256,
        )
    assert repository.get(pending.operation_id).status == "expired"
    assert repository.get(approved.operation_id).status == "expired"
    assert repository.claim_next(worker_id="worker") is None


def test_abandoned_execution_becomes_uncertain_and_is_never_auto_reclaimed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 7, 20, tzinfo=timezone.utc)]
    monkeypatch.setattr(operation_repository_module, "_utc_now", lambda: clock[0])
    repository = SideEffectOperationRepository(tmp_path / "operations.db")
    operation = _request(repository, now=clock[0])
    repository.approve(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=operation.approval_sha256,
    )
    claimed = repository.claim_next(worker_id="lost-worker", lease_seconds=1)
    assert claimed is not None and claimed.status == "executing"
    clock[0] += timedelta(seconds=2)

    assert repository.claim_next(worker_id="replacement-worker") is None
    uncertain = repository.get(operation.operation_id)
    assert uncertain is not None and uncertain.status == "uncertain"
    assert uncertain.error_code == "execution_lease_expired"
    assert uncertain.attempt == 1
    assert repository.list_events(operation.operation_id)[-1].payload[
        "automatic_retry"
    ] is False


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_code", "has_receipt"),
    [
        ("rejected", "failed", "provider_rejected", True),
        ("preflight_failure", "failed", "sandbox_preflight_failure", False),
        ("uncertain", "uncertain", "sandbox_timeout_after_invoke", False),
    ],
)
def test_provider_outcomes_preserve_confirmed_failure_vs_uncertainty(
    tmp_path: Path,
    monkeypatch,
    outcome: str,
    expected_status: str,
    expected_code: str,
    has_receipt: bool,
) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(operation_repository_module, "_utc_now", lambda: now)
    repository = SideEffectOperationRepository(tmp_path / f"{outcome}.db")
    operation = _request(repository, now=now, key=f"key-{outcome}")
    repository.approve(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=operation.approval_sha256,
    )
    service = SideEffectOperationService(
        repository=repository,
        provider=DeterministicSandboxBookingProvider(outcome=outcome),
    )

    completed = service.run_once(worker_id=f"worker-{outcome}")

    assert completed is not None and completed.status == expected_status
    assert completed.error_code == expected_code
    assert (completed.receipt_payload is not None) is has_receipt
    if outcome == "uncertain":
        assert completed.provider_operation_id is not None
        assert repository.claim_next(worker_id="retry-worker") is None


def test_live_or_unbound_quote_is_rejected_before_persistence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(operation_repository_module, "_utc_now", lambda: now)
    repository = SideEffectOperationRepository(tmp_path / "operations.db")
    quote = OperationQuote(
        provider="real-booking-provider",
        reference="quote-live",
        valid_until=_timestamp(now + timedelta(minutes=5)),
        currency="CNY",
        amount_minor=100,
        terms_sha256="0" * 64,
        sandbox=False,
    )

    with pytest.raises(ValueError, match="sandbox"):
        repository.request(
            request_id="request-live",
            tenant_id="tenant-alpha",
            requested_by="agent-requester",
            operation_kind="restaurant_booking",
            action_payload=_action(),
            quote=quote,
            idempotency_key="live-key",
        )
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM side_effect_operations"
        ).fetchone()[0] == 0


def test_uncertain_operation_is_resolved_only_by_bound_provider_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(operation_repository_module, "_utc_now", lambda: now)
    repository = SideEffectOperationRepository(tmp_path / "reconcile.db")
    operation = _request(repository, now=now, key="reconcile-confirmed")
    repository.approve(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=operation.approval_sha256,
    )
    uncertain_service = SideEffectOperationService(
        repository=repository,
        provider=DeterministicSandboxBookingProvider(outcome="uncertain"),
    )
    uncertain = uncertain_service.run_once(worker_id="uncertain-worker")
    assert uncertain is not None and uncertain.status == "uncertain"

    lookup_provider = DeterministicSandboxBookingProvider(
        lookup_outcome="confirmed"
    )
    tampered = lookup_provider.lookup(uncertain)
    tampered["provider_operation_id"] = "sbx-tampered"
    with pytest.raises(ValueError, match="provider_operation_id"):
        repository.reconcile_uncertain(
            operation_id=operation.operation_id,
            tenant_id="tenant-alpha",
            actor_id="status-reconciler",
            lookup_evidence=tampered,
        )

    reconciliation_service = SideEffectOperationService(
        repository=repository,
        provider=lookup_provider,
    )
    resolved, reconciliation = reconciliation_service.reconcile_uncertain(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        actor_id="status-reconciler",
    )

    assert resolved.status == "succeeded"
    assert resolved.receipt_payload is not None
    assert resolved.receipt_payload["response_sha256"] == (
        reconciliation.evidence_payload["response_sha256"]
    )
    assert reconciliation.outcome == "confirmed"
    assert reconciliation.receipt_sha256 == resolved.receipt_sha256
    assert repository.list_reconciliations(
        operation.operation_id,
        tenant_id="tenant-alpha",
    ) == (reconciliation,)
    final_event = repository.list_events(operation.operation_id)[-1]
    assert final_event.event_type == "execution_succeeded"
    assert final_event.payload["resolution_source"] == "side_effect_status_lookup_v1"


def test_unresolved_lookup_stays_uncertain_and_reconciliation_is_append_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(operation_repository_module, "_utc_now", lambda: now)
    repository = SideEffectOperationRepository(tmp_path / "still-unknown.db")
    operation = _request(repository, now=now, key="reconcile-still-unknown")
    repository.approve(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=operation.approval_sha256,
    )
    SideEffectOperationService(
        repository=repository,
        provider=DeterministicSandboxBookingProvider(outcome="uncertain"),
    ).run_once(worker_id="uncertain-worker")
    service = SideEffectOperationService(
        repository=repository,
        provider=DeterministicSandboxBookingProvider(
            lookup_outcome="still_unknown"
        ),
    )

    unresolved, reconciliation = service.reconcile_uncertain(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        actor_id="status-reconciler",
    )

    assert unresolved.status == "uncertain"
    assert unresolved.receipt_payload is None
    assert unresolved.error_code == "status_lookup_still_unknown"
    assert reconciliation.receipt_sha256 is None
    assert repository.list_events(operation.operation_id)[-1].payload[
        "automatic_retry"
    ] is False
    with sqlite3.connect(repository.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE side_effect_operation_reconciliations "
                "SET actor_id = 'tampered'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM side_effect_operation_reconciliations")


def test_lease_expiry_without_provider_reference_requires_manual_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 7, 20, tzinfo=timezone.utc)]
    monkeypatch.setattr(operation_repository_module, "_utc_now", lambda: clock[0])
    repository = SideEffectOperationRepository(tmp_path / "missing-reference.db")
    operation = _request(repository, now=clock[0], key="missing-provider-reference")
    repository.approve(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=operation.approval_sha256,
    )
    repository.claim_next(worker_id="lost-worker", lease_seconds=1)
    clock[0] += timedelta(seconds=2)
    uncertain = repository.get(operation.operation_id)
    assert uncertain is not None and uncertain.provider_operation_id is None

    with pytest.raises(OperationReconciliationUnavailable):
        SideEffectOperationService(repository=repository).reconcile_uncertain(
            operation_id=operation.operation_id,
            tenant_id="tenant-alpha",
            actor_id="status-reconciler",
        )
