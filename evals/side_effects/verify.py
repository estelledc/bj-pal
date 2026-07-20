"""Independently verify approval-gated side-effect evidence."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


OPERATION_ID_PATTERN = re.compile(r"^op-[a-f0-9]{32}$")


def _sha(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_side_effect_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported side-effect artifact schema")
    canonical = deepcopy(artifact)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _sha(canonical):
        raise ValueError("side-effect artifact SHA-256 mismatch")
    if artifact.get("policy") != {
        "version": "approval_gated_operation_v1",
        "receipt_version": "side_effect_receipt_v1",
        "status_lookup_version": "side_effect_status_lookup_v1",
        "provider": "bj-pal-sandbox",
        "requester_must_differ_from_approver": True,
        "automatic_retry_after_invoke": False,
        "uncertain_resolution_is_read_only": True,
    }:
        raise ValueError("side-effect policy metadata mismatch")
    raw_cases = (artifact.get("result") or {}).get("raw_cases") or []
    indexed = {case.get("case_id"): case for case in raw_cases}
    if set(indexed) != {
        "approval_and_receipt",
        "idempotency_and_tenant",
        "expiry_fail_closed",
        "uncertainty_no_retry",
        "status_reconciliation",
    } or len(indexed) != len(raw_cases):
        raise ValueError("side-effect case contract mismatch")
    approval = indexed["approval_and_receipt"]
    requested = approval.get("requested") or {}
    operation_id = requested.get("operation_id")
    receipt = approval.get("receipt") or {}
    receipt_valid = (
        isinstance(operation_id, str)
        and OPERATION_ID_PATTERN.fullmatch(operation_id) is not None
        and receipt.get("version") == "side_effect_receipt_v1"
        and receipt.get("operation_id") == operation_id
        and receipt.get("request_sha256") == requested.get("request_sha256")
        and receipt.get("provider") == "bj-pal-sandbox"
        and receipt.get("outcome") == "confirmed"
        and receipt.get("sandbox") is True
        and approval.get("receipt_sha256") == _sha(receipt)
    )
    approval_events = [event.get("event_type") for event in approval.get("events") or []]
    idempotency = indexed["idempotency_and_tenant"]
    idempotency_events = [
        event.get("event_type") for event in idempotency.get("events") or []
    ]
    expiry = indexed["expiry_fail_closed"]
    expiry_events = [event.get("event_type") for event in expiry.get("events") or []]
    uncertainty = indexed["uncertainty_no_retry"]
    uncertainty_events = [
        event.get("event_type") for event in uncertainty.get("events") or []
    ]
    status_reconciliation = indexed["status_reconciliation"]
    reconciliation = status_reconciliation.get("reconciliation") or {}
    evidence = reconciliation.get("evidence") or {}
    provider_payload = evidence.get("provider_payload") or {}
    resolved_receipt = status_reconciliation.get("receipt") or {}
    reconciliation_events = status_reconciliation.get("events") or []
    reconciliation_event_types = [
        event.get("event_type") for event in reconciliation_events
    ]
    resolution_event_payload = (
        reconciliation_events[-1].get("payload")
        if reconciliation_events
        else {}
    ) or {}
    evidence_valid = (
        set(evidence)
        == {
            "version",
            "operation_id",
            "request_sha256",
            "provider",
            "provider_operation_id",
            "outcome",
            "observed_at",
            "provider_payload",
            "response_sha256",
            "sandbox",
        }
        and evidence.get("version") == "side_effect_status_lookup_v1"
        and evidence.get("operation_id")
        == status_reconciliation.get("operation_id")
        and evidence.get("request_sha256")
        == status_reconciliation.get("request_sha256")
        and evidence.get("provider") == "bj-pal-sandbox"
        and evidence.get("provider_operation_id")
        == status_reconciliation.get("provider_operation_id")
        and evidence.get("outcome") == "confirmed"
        and evidence.get("sandbox") is True
        and set(provider_payload)
        == {"provider_operation_id", "outcome", "quote_reference", "sandbox"}
        and provider_payload.get("provider_operation_id")
        == status_reconciliation.get("provider_operation_id")
        and provider_payload.get("outcome") == "confirmed"
        and provider_payload.get("sandbox") is True
        and evidence.get("response_sha256") == _sha(provider_payload)
        and reconciliation.get("evidence_sha256") == _sha(evidence)
    )
    resolved_receipt_valid = (
        set(resolved_receipt)
        == {
            "version",
            "operation_id",
            "request_sha256",
            "provider",
            "provider_operation_id",
            "outcome",
            "executed_at",
            "response_sha256",
            "sandbox",
        }
        and resolved_receipt.get("version") == "side_effect_receipt_v1"
        and resolved_receipt.get("operation_id")
        == status_reconciliation.get("operation_id")
        and resolved_receipt.get("request_sha256")
        == status_reconciliation.get("request_sha256")
        and resolved_receipt.get("provider") == "bj-pal-sandbox"
        and resolved_receipt.get("provider_operation_id")
        == status_reconciliation.get("provider_operation_id")
        and resolved_receipt.get("outcome") == "confirmed"
        and resolved_receipt.get("executed_at") == evidence.get("observed_at")
        and resolved_receipt.get("response_sha256")
        == evidence.get("response_sha256")
        and resolved_receipt.get("sandbox") is True
        and status_reconciliation.get("receipt_sha256") == _sha(resolved_receipt)
        and reconciliation.get("receipt_sha256")
        == status_reconciliation.get("receipt_sha256")
    )
    metrics = {
        "case_count": len(raw_cases),
        "separation_of_duty_rate": float(
            approval.get("self_approval_error") == "operation_self_approval_forbidden"
            and requested.get("requested_by") == "agent-requester"
            and approval.get("approved_by") == "human-approver"
            and approval_events
            == ["requested", "approved", "execution_started", "execution_succeeded"]
        ),
        "approval_binding_rate": float(
            approval.get("binding_error") == "operation_approval_conflict"
        ),
        "idempotency_rate": float(
            idempotency.get("first_operation_id")
            == idempotency.get("reused_operation_id")
            and idempotency.get("conflict_error") == "operation_idempotency_conflict"
            and idempotency_events == ["requested", "request_reused"]
        ),
        "tenant_isolation_rate": float(not idempotency.get("foreign_tenant_found")),
        "expiry_fail_closed_rate": float(
            expiry.get("approval_error") == "operation_approval_expired"
            and expiry.get("final_status") == "expired"
            and not expiry.get("worker_claimed")
            and expiry_events == ["requested", "expired"]
        ),
        "receipt_integrity_rate": float(
            approval.get("final_status") == "succeeded" and receipt_valid
        ),
        "append_only_audit_rate": float(approval.get("append_only_enforced") is True),
        "sandbox_enforcement_rate": float(approval.get("live_provider_rejected") is True),
        "uncertainty_no_retry_rate": float(
            uncertainty.get("final_status") == "uncertain"
            and uncertainty.get("error_code") == "execution_lease_expired"
            and uncertainty.get("attempt") == 1
            and uncertainty.get("receipt") is None
            and not uncertainty.get("automatic_retry_claimed")
            and uncertainty_events
            == ["requested", "approved", "execution_started", "execution_uncertain"]
        ),
        "status_lookup_resolution_rate": float(
            status_reconciliation.get("initial_uncertain_status") == "uncertain"
            and not status_reconciliation.get("automatic_retry_claimed")
            and status_reconciliation.get("final_status") == "succeeded"
            and reconciliation.get("outcome") == "confirmed"
            and evidence_valid
            and resolved_receipt_valid
            and reconciliation_event_types
            == [
                "requested",
                "approved",
                "execution_started",
                "execution_uncertain",
                "execution_succeeded",
            ]
        ),
        "status_lookup_binding_rate": float(
            status_reconciliation.get("binding_error")
            == "status_lookup_binding_rejected"
            and evidence_valid
        ),
        "reconciliation_audit_rate": float(
            status_reconciliation.get("append_only_enforced") is True
            and reconciliation.get("actor_id") == "status-reconciler"
            and resolution_event_payload.get("resolution_source")
            == "side_effect_status_lookup_v1"
            and resolution_event_payload.get("lookup_evidence_sha256")
            == reconciliation.get("evidence_sha256")
            and resolution_event_payload.get("receipt_sha256")
            == status_reconciliation.get("receipt_sha256")
        ),
    }
    if (artifact.get("result") or {}).get("metrics") != metrics:
        raise ValueError("side-effect metrics do not match raw cases")
    if any(value != 1.0 for key, value in metrics.items() if key != "case_count"):
        raise ValueError("side-effect safety gate did not pass")
    return artifact
