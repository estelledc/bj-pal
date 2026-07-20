"""Independently recompute access decisions from principals and raw HTTP outcomes."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


SCOPES = frozenset(
    {
        "jobs:submit",
        "jobs:read",
        "jobs:control",
        "jobs:replay",
        "operations:request",
        "operations:read",
        "operations:approve",
        "operations:reconcile",
        "trials:manage",
        "trials:read",
    }
)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def verify_access_control_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported access-control artifact schema")
    canonical = deepcopy(artifact)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _canonical_sha256(canonical):
        raise ValueError("access-control artifact SHA-256 mismatch")
    if artifact.get("policy") != {
        "version": "identity_scope_v1",
        "scopes": sorted(SCOPES),
        "tenant_mismatch_status": 404,
        "scope_denial_status": 403,
        "priority_denial_status": 403,
        "admission_denial_status": 429,
        "admission_policy": "tenant_admission_v1",
        "submission_window_seconds": 60,
    }:
        raise ValueError("access-control policy metadata mismatch")

    principals = artifact.get("principals") or []
    by_principal = {item.get("principal_id"): item for item in principals}
    if not principals or len(by_principal) != len(principals):
        raise ValueError("access-control principals must be non-empty and unique")
    tenant_policies: dict[str, tuple[int, int]] = {}
    for principal in principals:
        scopes = principal.get("scopes") or []
        if not scopes or not set(scopes) <= SCOPES:
            raise ValueError("access-control principal scope is invalid")
        max_priority = principal.get("max_priority")
        if isinstance(max_priority, bool) or not isinstance(max_priority, int):
            raise ValueError("access-control principal priority cap is invalid")
        if not 0 <= max_priority <= 9:
            raise ValueError("access-control principal priority cap is out of range")
        active_limit = principal.get("tenant_active_job_limit")
        rate_limit = principal.get("tenant_submission_limit_per_minute")
        if (
            isinstance(active_limit, bool)
            or not isinstance(active_limit, int)
            or not 1 <= active_limit <= 10_000
        ):
            raise ValueError("access-control tenant active limit is invalid")
        if (
            isinstance(rate_limit, bool)
            or not isinstance(rate_limit, int)
            or not 1 <= rate_limit <= 10_000
        ):
            raise ValueError("access-control tenant rate limit is invalid")
        policy = (active_limit, rate_limit)
        existing_policy = tenant_policies.setdefault(principal.get("tenant_id"), policy)
        if existing_policy != policy:
            raise ValueError("access-control tenant admission policy is inconsistent")

    result = artifact.get("result") or {}
    raw_cases = result.get("raw_cases") or []
    by_case = {case.get("case_id"): case for case in raw_cases}
    if set(by_case) != {
        "route_scope_matrix",
        "priority_admission",
        "tenant_isolation",
        "continuation_isolation",
        "tenant_admission",
        "continuation_admission_recovery",
    }:
        raise ValueError("access-control case contract mismatch")
    decisions_by_case = {
        case_id: [
            _verify_request(request, by_principal)
            for request in case.get("requests") or []
        ]
        for case_id, case in by_case.items()
    }
    scope_decisions = decisions_by_case["route_scope_matrix"]
    priority_decisions = decisions_by_case["priority_admission"]
    tenant_decisions = decisions_by_case["tenant_isolation"]
    admission_decisions = decisions_by_case["tenant_admission"]
    recovery_decisions = decisions_by_case["continuation_admission_recovery"]

    tenant_case = by_case["tenant_isolation"]
    alpha_job = tenant_case.get("alpha_job") or {}
    beta_job = tenant_case.get("beta_job") or {}
    idempotency_namespace_valid = (
        bool(tenant_case.get("idempotency_key"))
        and alpha_job.get("job_id")
        and beta_job.get("job_id")
        and alpha_job.get("job_id") != beta_job.get("job_id")
        and alpha_job.get("tenant_id") == "tenant-alpha"
        and beta_job.get("tenant_id") == "tenant-beta"
        and alpha_job.get("submitted_by") == "alpha-admin"
        and beta_job.get("submitted_by") == "beta-admin"
    )
    tenant_lists_valid = (
        beta_job.get("job_id") not in (tenant_case.get("alpha_list_job_ids") or [])
        and alpha_job.get("job_id") not in (tenant_case.get("beta_list_job_ids") or [])
        and alpha_job.get("job_id") in (tenant_case.get("alpha_list_job_ids") or [])
        and beta_job.get("job_id") in (tenant_case.get("beta_list_job_ids") or [])
    )

    continuation_case = by_case["continuation_isolation"]
    continuation_decisions = decisions_by_case["continuation_isolation"]
    continuation_valid = (
        continuation_case.get("session_tenant") == "tenant-alpha"
        and continuation_case.get("session_priority") == 3
        and continuation_case.get("status_after_foreign") == "pending"
        and continuation_case.get("status_after_cap") == "pending"
        and all(item["valid"] for item in continuation_decisions)
    )
    admission_case = by_case["tenant_admission"]
    admission_scenarios = [
        request.get("admission_scenario")
        for request in admission_case.get("requests") or []
    ]
    if admission_scenarios != [
        None,
        None,
        "active_job_limit",
        None,
        "submission_rate",
    ]:
        raise ValueError("tenant admission request contract mismatch")
    admission_audit_valid = _verify_admission_audit(
        admission_case.get("audit_events") or [],
        tenant_id="tenant-gamma",
        submitted_by="gamma-admin",
        active_limit=1,
        rate_limit=2,
        expected_decisions=[
            "admitted",
            "idempotent_reuse",
            "rejected",
            "admitted",
            "rejected",
        ],
        expected_reasons=[
            None,
            None,
            "tenant_active_job_limit_exceeded",
            None,
            "tenant_submission_rate_exceeded",
        ],
    )

    recovery_case = by_case["continuation_admission_recovery"]
    recovery_scenarios = [
        request.get("admission_scenario")
        for request in recovery_case.get("requests") or []
    ]
    if recovery_scenarios != ["active_job_limit", None]:
        raise ValueError("continuation admission request contract mismatch")
    recovery_audit_valid = _verify_admission_audit(
        recovery_case.get("audit_events") or [],
        tenant_id="tenant-delta",
        submitted_by="delta-admin",
        active_limit=1,
        rate_limit=10,
        expected_decisions=["admitted", "rejected", "admitted"],
        expected_reasons=[None, "tenant_active_job_limit_exceeded", None],
    )
    recovery_valid = (
        recovery_case.get("status_after_rejection") == "resolved"
        and recovery_case.get("status_after_retry") == "completed"
        and all(item["valid"] for item in recovery_decisions)
        and recovery_audit_valid
    )
    active_limit_decisions = [
        item
        for item in admission_decisions + recovery_decisions
        if item["basis"] == "active_admission"
    ]
    rate_limit_decisions = [
        item for item in admission_decisions if item["basis"] == "rate_admission"
    ]
    credential_exclusion_valid = _verify_credential_exclusion(artifact)
    metrics = {
        "case_count": len(raw_cases),
        "route_scope_enforcement_rate": _rate(scope_decisions),
        "priority_cap_enforcement_rate": _rate(priority_decisions),
        "tenant_isolation_rate": round(
            (sum(item["valid"] for item in tenant_decisions) + int(tenant_lists_valid))
            / (len(tenant_decisions) + 1),
            3,
        ),
        "idempotency_namespace_rate": 1.0 if idempotency_namespace_valid else 0.0,
        "continuation_isolation_rate": 1.0 if continuation_valid else 0.0,
        "credential_exclusion_rate": 1.0 if credential_exclusion_valid else 0.0,
        "active_job_limit_enforcement_rate": _rate(active_limit_decisions),
        "submission_rate_enforcement_rate": _rate(rate_limit_decisions),
        "admission_audit_rate": (
            1.0 if admission_audit_valid and recovery_audit_valid else 0.0
        ),
        "continuation_admission_recovery_rate": 1.0 if recovery_valid else 0.0,
    }
    if result.get("metrics") != metrics:
        raise ValueError("access-control metrics do not match raw cases")
    if any(value != 1.0 for key, value in metrics.items() if key != "case_count"):
        raise ValueError("access-control contract gate did not pass")
    return artifact


def _verify_request(
    request: dict[str, Any],
    principals: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    actor = request.get("actor")
    principal = principals.get(actor)
    if principal is None:
        raise ValueError("access-control request actor is unknown")
    operation = request.get("operation")
    required_scope = "jobs:submit" if operation == "jobs:continue" else operation
    if required_scope not in SCOPES:
        raise ValueError("access-control request operation is unknown")

    if required_scope not in set(principal.get("scopes") or []):
        expected_status = 403
        expected_error = "control_plane_forbidden"
        basis = "scope"
    elif request.get("target_tenant") != principal.get("tenant_id"):
        expected_status = 404
        expected_error = (
            "clarification_not_found" if operation == "jobs:continue" else "job_not_found"
        )
        basis = "tenant"
    elif request.get("priority") is not None and request["priority"] > principal["max_priority"]:
        expected_status = 403
        expected_error = "priority_forbidden"
        basis = "priority"
    elif request.get("admission_scenario") == "active_job_limit":
        expected_status = 429
        expected_error = "tenant_active_job_limit_exceeded"
        basis = "active_admission"
    elif request.get("admission_scenario") == "submission_rate":
        expected_status = 429
        expected_error = "tenant_submission_rate_exceeded"
        basis = "rate_admission"
    else:
        expected_status = request.get("success_status")
        expected_error = None
        basis = "allowed"
    valid = (
        request.get("observed_status") == expected_status
        and request.get("error_code") == expected_error
    )
    if expected_error is None and operation in {"jobs:submit", "jobs:continue"}:
        valid = valid and request.get("response_tenant") == principal.get("tenant_id")
        valid = valid and request.get("response_submitted_by") == actor
    if basis == "active_admission":
        valid = valid and request.get("retry_after") is None
    if basis == "rate_admission":
        retry_after = request.get("retry_after")
        valid = valid and isinstance(retry_after, str) and retry_after.isdigit()
        valid = valid and int(retry_after) >= 1
    return {"operation": operation, "basis": basis, "valid": bool(valid)}


def _verify_admission_audit(
    events: list[dict[str, Any]],
    *,
    tenant_id: str,
    submitted_by: str,
    active_limit: int,
    rate_limit: int,
    expected_decisions: list[str],
    expected_reasons: list[str | None],
) -> bool:
    if [event.get("decision") for event in events] != expected_decisions:
        return False
    if [event.get("reason_code") for event in events] != expected_reasons:
        return False
    event_ids = [event.get("event_id") for event in events]
    if (
        not event_ids
        or any(isinstance(event_id, bool) or not isinstance(event_id, int) for event_id in event_ids)
        or event_ids != sorted(event_ids)
        or len(event_ids) != len(set(event_ids))
    ):
        return False
    for event in events:
        if event.get("policy_version") != "tenant_admission_v1":
            return False
        if event.get("tenant_id") != tenant_id or event.get("submitted_by") != submitted_by:
            return False
        if event.get("operation") != "submit":
            return False
        if event.get("active_job_limit") != active_limit:
            return False
        if event.get("submission_limit_per_minute") != rate_limit:
            return False
        if event.get("submission_window_seconds") != 60:
            return False
        if event.get("decision") == "rejected" and event.get("job_id") is not None:
            return False
        if event.get("decision") != "rejected" and not event.get("job_id"):
            return False
    return True


def _verify_credential_exclusion(artifact: dict[str, Any]) -> bool:
    forbidden = set((artifact.get("privacy") or {}).get("forbidden_value_sha256") or [])
    if not forbidden or not all(
        isinstance(item, str) and len(item) == 64 for item in forbidden
    ):
        return False
    payload = deepcopy(artifact)
    payload.pop("artifact_sha256", None)
    payload.pop("privacy", None)
    for key, value in _walk(payload):
        lowered = key.lower()
        if lowered != "credential_exclusion_rate" and any(
            marker in lowered for marker in ("token", "authorization", "credential")
        ):
            return False
        if isinstance(value, str):
            if hashlib.sha256(value.encode("utf-8")).hexdigest() in forbidden:
                return False
    return True


def _walk(value: Any, key: str = ""):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield from _walk(child_value, str(child_key))
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, key)
    else:
        yield key, value


def _rate(decisions: list[dict[str, Any]]) -> float:
    if not decisions:
        raise ValueError("access-control metric has no applicable decisions")
    return round(sum(item["valid"] for item in decisions) / len(decisions), 3)
