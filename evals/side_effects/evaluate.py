"""Generate raw evidence for approval, receipt, and uncertainty safety."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from operations import (
    DeterministicSandboxBookingProvider,
    OperationApprovalConflict,
    OperationExpired,
    OperationIdempotencyConflict,
    OperationQuote,
    OperationSelfApprovalForbidden,
    SideEffectOperationRepository,
    SideEffectOperationService,
)
from operations import repository as operation_repository_module


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _quote(now: datetime, reference: str) -> OperationQuote:
    return OperationQuote(
        provider="bj-pal-sandbox",
        reference=reference,
        valid_until=_timestamp(now + timedelta(minutes=10)),
        currency="CNY",
        amount_minor=12_800,
        terms_sha256=hashlib.sha256(b"side-effect eval terms").hexdigest(),
        sandbox=True,
    )


def _action(label: str = "评测餐厅") -> dict[str, Any]:
    return {
        "poi_id": "eval-poi",
        "poi_name": label,
        "target_time": "18:30",
        "party_size": 2,
        "contact_reference": "eval-contact-ref",
    }


def _event_payload(repository: SideEffectOperationRepository, operation_id: str) -> list[dict]:
    return [
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "actor_id": event.actor_id,
            "payload": event.payload,
            "created_at": event.created_at,
        }
        for event in repository.list_events(operation_id)
    ]


def _approval_and_receipt(path: Path, clock: list[datetime]) -> dict[str, Any]:
    repository = SideEffectOperationRepository(path)
    service = SideEffectOperationService(repository=repository)
    operation = repository.request(
        request_id="eval-approval-request",
        tenant_id="tenant-alpha",
        requested_by="agent-requester",
        operation_kind="restaurant_booking",
        action_payload=_action(),
        quote=_quote(clock[0], "quote-approval"),
        idempotency_key="eval-approval-key",
    )
    self_error = None
    try:
        repository.approve(
            operation_id=operation.operation_id,
            tenant_id="tenant-alpha",
            approved_by="agent-requester",
            expected_approval_sha256=operation.approval_sha256,
        )
    except OperationSelfApprovalForbidden:
        self_error = "operation_self_approval_forbidden"
    binding_error = None
    try:
        repository.approve(
            operation_id=operation.operation_id,
            tenant_id="tenant-alpha",
            approved_by="human-approver",
            expected_approval_sha256="0" * 64,
        )
    except OperationApprovalConflict:
        binding_error = "operation_approval_conflict"
    approved = repository.approve(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=operation.approval_sha256,
    )
    completed = service.run_once(worker_id="eval-operation-worker")
    if completed is None:
        raise ValueError("approved operation was not executed")
    append_only = False
    with sqlite3.connect(repository.path) as connection:
        try:
            connection.execute(
                "UPDATE side_effect_operation_events SET actor_id = 'tampered'"
            )
        except sqlite3.IntegrityError:
            append_only = True
    live_provider_rejected = False
    try:
        repository.request(
            request_id="eval-live-request",
            tenant_id="tenant-alpha",
            requested_by="agent-requester",
            operation_kind="restaurant_booking",
            action_payload=_action("不应持久化"),
            quote=OperationQuote(
                provider="real-provider",
                reference="quote-live",
                valid_until=_timestamp(clock[0] + timedelta(minutes=10)),
                currency="CNY",
                amount_minor=12_800,
                terms_sha256="0" * 64,
                sandbox=False,
            ),
            idempotency_key="eval-live-key",
        )
    except ValueError:
        live_provider_rejected = True
    return {
        "case_id": "approval_and_receipt",
        "requested": {
            "operation_id": operation.operation_id,
            "tenant_id": operation.tenant_id,
            "requested_by": operation.requested_by,
            "request_sha256": operation.request_sha256,
            "approval_sha256": operation.approval_sha256,
            "quote": operation.quote.to_dict(),
        },
        "self_approval_error": self_error,
        "binding_error": binding_error,
        "approved_by": approved.approved_by,
        "final_status": completed.status,
        "receipt": completed.receipt_payload,
        "receipt_sha256": completed.receipt_sha256,
        "events": _event_payload(repository, operation.operation_id),
        "append_only_enforced": append_only,
        "live_provider_rejected": live_provider_rejected,
    }


def _idempotency_and_tenant(path: Path, clock: list[datetime]) -> dict[str, Any]:
    repository = SideEffectOperationRepository(path)
    kwargs = {
        "request_id": "eval-idempotency-request",
        "tenant_id": "tenant-alpha",
        "requested_by": "agent-requester",
        "operation_kind": "restaurant_booking",
        "action_payload": _action(),
        "quote": _quote(clock[0], "quote-idempotency"),
        "idempotency_key": "eval-idempotency-key",
    }
    first = repository.request(**kwargs)
    reused = repository.request(**{**kwargs, "request_id": "eval-idempotency-reuse"})
    conflict = None
    try:
        repository.request(
            **{
                **kwargs,
                "request_id": "eval-idempotency-conflict",
                "action_payload": _action("另一家餐厅"),
            }
        )
    except OperationIdempotencyConflict:
        conflict = "operation_idempotency_conflict"
    return {
        "case_id": "idempotency_and_tenant",
        "first_operation_id": first.operation_id,
        "reused_operation_id": reused.operation_id,
        "conflict_error": conflict,
        "foreign_tenant_found": repository.get(
            first.operation_id,
            tenant_id="tenant-beta",
        )
        is not None,
        "events": _event_payload(repository, first.operation_id),
    }


def _expiry(path: Path, clock: list[datetime]) -> dict[str, Any]:
    repository = SideEffectOperationRepository(path)
    operation = repository.request(
        request_id="eval-expiry-request",
        tenant_id="tenant-alpha",
        requested_by="agent-requester",
        operation_kind="restaurant_booking",
        action_payload=_action(),
        quote=_quote(clock[0], "quote-expiry"),
        idempotency_key="eval-expiry-key",
        approval_ttl_seconds=1,
    )
    clock[0] += timedelta(seconds=2)
    error = None
    try:
        repository.approve(
            operation_id=operation.operation_id,
            tenant_id="tenant-alpha",
            approved_by="human-approver",
            expected_approval_sha256=operation.approval_sha256,
        )
    except OperationExpired:
        error = "operation_approval_expired"
    restored = repository.get(operation.operation_id)
    return {
        "case_id": "expiry_fail_closed",
        "approval_error": error,
        "final_status": restored.status if restored else None,
        "worker_claimed": repository.claim_next(worker_id="expiry-worker") is not None,
        "events": _event_payload(repository, operation.operation_id),
    }


def _uncertainty(path: Path, clock: list[datetime]) -> dict[str, Any]:
    repository = SideEffectOperationRepository(path)
    operation = repository.request(
        request_id="eval-uncertain-request",
        tenant_id="tenant-alpha",
        requested_by="agent-requester",
        operation_kind="restaurant_booking",
        action_payload=_action(),
        quote=_quote(clock[0], "quote-uncertain"),
        idempotency_key="eval-uncertain-key",
    )
    repository.approve(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=operation.approval_sha256,
    )
    claimed = repository.claim_next(worker_id="lost-worker", lease_seconds=1)
    if claimed is None:
        raise ValueError("uncertainty scenario did not claim operation")
    clock[0] += timedelta(seconds=2)
    retry_claimed = repository.claim_next(worker_id="replacement-worker") is not None
    restored = repository.get(operation.operation_id)
    return {
        "case_id": "uncertainty_no_retry",
        "final_status": restored.status if restored else None,
        "error_code": restored.error_code if restored else None,
        "attempt": restored.attempt if restored else None,
        "receipt": restored.receipt_payload if restored else None,
        "automatic_retry_claimed": retry_claimed,
        "events": _event_payload(repository, operation.operation_id),
    }


def _status_reconciliation(path: Path, clock: list[datetime]) -> dict[str, Any]:
    repository = SideEffectOperationRepository(path)
    provider = DeterministicSandboxBookingProvider(
        outcome="uncertain",
        lookup_outcome="confirmed",
    )
    service = SideEffectOperationService(
        repository=repository,
        provider=provider,
    )
    operation = repository.request(
        request_id="eval-reconciliation-request",
        tenant_id="tenant-alpha",
        requested_by="agent-requester",
        operation_kind="restaurant_booking",
        action_payload=_action(),
        quote=_quote(clock[0], "quote-reconciliation"),
        idempotency_key="eval-reconciliation-key",
    )
    repository.approve(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        approved_by="human-approver",
        expected_approval_sha256=operation.approval_sha256,
    )
    uncertain = service.run_once(worker_id="eval-uncertain-provider-worker")
    if uncertain is None or uncertain.status != "uncertain":
        raise ValueError("status reconciliation scenario did not become uncertain")

    binding_error = None
    tampered_evidence = provider.lookup(uncertain)
    tampered_evidence["provider_operation_id"] = "sbx-tampered-reference"
    try:
        repository.reconcile_uncertain(
            operation_id=operation.operation_id,
            tenant_id="tenant-alpha",
            actor_id="status-reconciler",
            lookup_evidence=tampered_evidence,
        )
    except ValueError:
        binding_error = "status_lookup_binding_rejected"

    resolved, reconciliation = service.reconcile_uncertain(
        operation_id=operation.operation_id,
        tenant_id="tenant-alpha",
        actor_id="status-reconciler",
    )
    append_only = False
    with sqlite3.connect(repository.path) as connection:
        try:
            connection.execute(
                "UPDATE side_effect_operation_reconciliations "
                "SET actor_id = 'tampered'"
            )
        except sqlite3.IntegrityError:
            append_only = True
    return {
        "case_id": "status_reconciliation",
        "operation_id": operation.operation_id,
        "request_sha256": operation.request_sha256,
        "initial_uncertain_status": uncertain.status,
        "provider_operation_id": uncertain.provider_operation_id,
        "automatic_retry_claimed": False,
        "binding_error": binding_error,
        "final_status": resolved.status,
        "receipt": resolved.receipt_payload,
        "receipt_sha256": resolved.receipt_sha256,
        "reconciliation": {
            "reconciliation_id": reconciliation.reconciliation_id,
            "actor_id": reconciliation.actor_id,
            "outcome": reconciliation.outcome,
            "provider_operation_id": reconciliation.provider_operation_id,
            "evidence": reconciliation.evidence_payload,
            "evidence_sha256": reconciliation.evidence_sha256,
            "receipt_sha256": reconciliation.receipt_sha256,
            "created_at": reconciliation.created_at,
        },
        "append_only_enforced": append_only,
        "events": _event_payload(repository, operation.operation_id),
    }


def evaluate_side_effects() -> dict[str, Any]:
    original_clock: Callable[[], datetime] = operation_repository_module._utc_now
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    try:
        with TemporaryDirectory(prefix="bj-pal-side-effect-eval-") as directory:
            base = Path(directory)
            cases = []
            for name, factory in (
                ("approval.db", _approval_and_receipt),
                ("idempotency.db", _idempotency_and_tenant),
                ("expiry.db", _expiry),
                ("uncertainty.db", _uncertainty),
                ("reconciliation.db", _status_reconciliation),
            ):
                clock = [start]
                operation_repository_module._utc_now = lambda clock=clock: clock[0]
                cases.append(factory(base / name, clock))
    finally:
        operation_repository_module._utc_now = original_clock
    metrics = _metrics(cases)
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_contract",
        "policy": {
            "version": "approval_gated_operation_v1",
            "receipt_version": "side_effect_receipt_v1",
            "status_lookup_version": "side_effect_status_lookup_v1",
            "provider": "bj-pal-sandbox",
            "requester_must_differ_from_approver": True,
            "automatic_retry_after_invoke": False,
            "uncertain_resolution_is_read_only": True,
        },
        "result": {"raw_cases": cases, "metrics": metrics},
    }
    artifact["artifact_sha256"] = _canonical_sha256(artifact)
    return artifact


def _metrics(cases: list[dict[str, Any]]) -> dict[str, float | int]:
    indexed = {case["case_id"]: case for case in cases}
    approval = indexed["approval_and_receipt"]
    idempotency = indexed["idempotency_and_tenant"]
    expiry = indexed["expiry_fail_closed"]
    uncertainty = indexed["uncertainty_no_retry"]
    reconciliation = indexed["status_reconciliation"]
    receipt = approval.get("receipt") or {}
    lookup = reconciliation.get("reconciliation") or {}
    evidence = lookup.get("evidence") or {}
    provider_payload = evidence.get("provider_payload") or {}
    resolved_receipt = reconciliation.get("receipt") or {}
    return {
        "case_count": len(cases),
        "separation_of_duty_rate": float(
            approval.get("self_approval_error") == "operation_self_approval_forbidden"
            and approval.get("approved_by") == "human-approver"
        ),
        "approval_binding_rate": float(
            approval.get("binding_error") == "operation_approval_conflict"
        ),
        "idempotency_rate": float(
            idempotency.get("first_operation_id")
            == idempotency.get("reused_operation_id")
            and idempotency.get("conflict_error") == "operation_idempotency_conflict"
        ),
        "tenant_isolation_rate": float(not idempotency.get("foreign_tenant_found")),
        "expiry_fail_closed_rate": float(
            expiry.get("approval_error") == "operation_approval_expired"
            and expiry.get("final_status") == "expired"
            and not expiry.get("worker_claimed")
        ),
        "receipt_integrity_rate": float(
            approval.get("final_status") == "succeeded"
            and receipt.get("sandbox") is True
            and receipt.get("request_sha256")
            == (approval.get("requested") or {}).get("request_sha256")
            and approval.get("receipt_sha256") == _canonical_sha256(receipt)
        ),
        "append_only_audit_rate": float(approval.get("append_only_enforced") is True),
        "sandbox_enforcement_rate": float(approval.get("live_provider_rejected") is True),
        "uncertainty_no_retry_rate": float(
            uncertainty.get("final_status") == "uncertain"
            and uncertainty.get("error_code") == "execution_lease_expired"
            and uncertainty.get("attempt") == 1
            and uncertainty.get("receipt") is None
            and not uncertainty.get("automatic_retry_claimed")
        ),
        "status_lookup_resolution_rate": float(
            reconciliation.get("initial_uncertain_status") == "uncertain"
            and not reconciliation.get("automatic_retry_claimed")
            and reconciliation.get("final_status") == "succeeded"
            and lookup.get("outcome") == "confirmed"
            and evidence.get("outcome") == "confirmed"
            and resolved_receipt.get("outcome") == "confirmed"
            and resolved_receipt.get("response_sha256")
            == evidence.get("response_sha256")
            and reconciliation.get("receipt_sha256")
            == _canonical_sha256(resolved_receipt)
        ),
        "status_lookup_binding_rate": float(
            reconciliation.get("binding_error") == "status_lookup_binding_rejected"
            and evidence.get("operation_id") == reconciliation.get("operation_id")
            and evidence.get("request_sha256")
            == reconciliation.get("request_sha256")
            and evidence.get("provider_operation_id")
            == reconciliation.get("provider_operation_id")
            and provider_payload.get("provider_operation_id")
            == reconciliation.get("provider_operation_id")
            and evidence.get("response_sha256")
            == _canonical_sha256(provider_payload)
        ),
        "reconciliation_audit_rate": float(
            reconciliation.get("append_only_enforced") is True
            and lookup.get("actor_id") == "status-reconciler"
            and lookup.get("evidence_sha256") == _canonical_sha256(evidence)
            and lookup.get("receipt_sha256")
            == reconciliation.get("receipt_sha256")
        ),
    }


def write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
