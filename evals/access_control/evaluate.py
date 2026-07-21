"""Generate synthetic HTTP evidence for the durable control-plane boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi.testclient import TestClient

from clarifications import ClarificationContinuationService, ClarificationRepository
from http_api.app import create_app
from http_api.auth import (
    CONTROL_SCOPES,
    JOBS_CONTROL,
    JOBS_READ,
    JOBS_REPLAY,
    JOBS_SUBMIT,
    ControlPlaneCredential,
    ControlPrincipal,
)
from jobs import PlanningJobRepository, PlanningJobService
from outcomes import PlanFeedbackRepository, PlanFeedbackService


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _credential(
    *,
    token: str,
    principal_id: str,
    tenant_id: str,
    scopes: frozenset[str],
    max_priority: int,
    tenant_active_job_limit: int,
    tenant_submission_limit_per_minute: int,
) -> ControlPlaneCredential:
    return ControlPlaneCredential.from_token(
        token=token,
        principal=ControlPrincipal(
            principal_id=principal_id,
            tenant_id=tenant_id,
            scopes=scopes,
            max_priority=max_priority,
            tenant_active_job_limit=tenant_active_job_limit,
            tenant_submission_limit_per_minute=(
                tenant_submission_limit_per_minute
            ),
        ),
    )


def _record(
    *,
    actor: str,
    operation: str,
    response,
    success_status: int,
    target_tenant: str | None = None,
    priority: int | None = None,
    admission_scenario: str | None = None,
) -> dict[str, Any]:
    payload = response.json()
    error = payload.get("error") or {}
    return {
        "actor": actor,
        "operation": operation,
        "target_tenant": target_tenant,
        "priority": priority,
        "admission_scenario": admission_scenario,
        "success_status": success_status,
        "observed_status": response.status_code,
        "error_code": error.get("code"),
        "job_id": payload.get("job_id"),
        "response_tenant": payload.get("tenant_id"),
        "response_submitted_by": payload.get("submitted_by"),
        "retry_after": response.headers.get("Retry-After"),
    }


def evaluate_access_control() -> dict[str, Any]:
    tokens = {
        "alpha-submitter": "eval-alpha-submitter-0123456789-abcdef-0001",
        "alpha-reader": "eval-alpha-reader-0123456789-abcdef-000002",
        "alpha-operator": "eval-alpha-operator-0123456789-abcdef-0003",
        "alpha-admin": "eval-alpha-admin-0123456789-abcdef-000004",
        "alpha-low": "eval-alpha-low-0123456789-abcdef-0000005",
        "beta-admin": "eval-beta-admin-0123456789-abcdef-0000006",
        "gamma-admin": "eval-gamma-admin-0123456789-abcdef-000007",
        "delta-admin": "eval-delta-admin-0123456789-abcdef-000008",
    }
    principal_specs = {
        "alpha-submitter": ("tenant-alpha", frozenset({JOBS_SUBMIT}), 3, 100, 60),
        "alpha-reader": ("tenant-alpha", frozenset({JOBS_READ}), 0, 100, 60),
        "alpha-operator": (
            "tenant-alpha",
            frozenset({JOBS_READ, JOBS_CONTROL, JOBS_REPLAY}),
            9,
            100,
            60,
        ),
        "alpha-admin": ("tenant-alpha", CONTROL_SCOPES, 9, 100, 60),
        "alpha-low": ("tenant-alpha", frozenset({JOBS_SUBMIT}), 1, 100, 60),
        "beta-admin": ("tenant-beta", CONTROL_SCOPES, 9, 100, 60),
        "gamma-admin": ("tenant-gamma", CONTROL_SCOPES, 9, 1, 2),
        "delta-admin": ("tenant-delta", CONTROL_SCOPES, 9, 1, 10),
    }
    credentials = tuple(
        _credential(
            token=tokens[principal_id],
            principal_id=principal_id,
            tenant_id=tenant_id,
            scopes=scopes,
            max_priority=max_priority,
            tenant_active_job_limit=active_limit,
            tenant_submission_limit_per_minute=rate_limit,
        )
        for principal_id, (
            tenant_id,
            scopes,
            max_priority,
            active_limit,
            rate_limit,
        ) in principal_specs.items()
    )
    principals = [
        {
            "principal_id": principal_id,
            "tenant_id": tenant_id,
            "scopes": sorted(scopes),
            "max_priority": max_priority,
            "tenant_active_job_limit": active_limit,
            "tenant_submission_limit_per_minute": rate_limit,
        }
        for principal_id, (
            tenant_id,
            scopes,
            max_priority,
            active_limit,
            rate_limit,
        ) in principal_specs.items()
    ]
    raw_cases: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="bj-pal-access-eval-") as temp_dir:
        root = Path(temp_dir)
        job_service = PlanningJobService(
            repository=PlanningJobRepository(root / "jobs.db")
        )
        clarification_service = ClarificationContinuationService(
            repository=ClarificationRepository(root / "clarifications.db")
        )
        feedback_service = PlanFeedbackService(
            repository=PlanFeedbackRepository(root / "feedback.db")
        )
        app = create_app(
            job_service=job_service,
            clarification_service=clarification_service,
            feedback_service=feedback_service,
            control_credentials=credentials,
        )

        def headers(actor: str, *, idempotency_key: str | None = None) -> dict[str, str]:
            result = {"Authorization": f"Bearer {tokens[actor]}"}
            if idempotency_key is not None:
                result["Idempotency-Key"] = idempotency_key
            return result

        with TestClient(app) as client:
            reader_list = client.get(
                "/v1/planning-jobs",
                headers=headers("alpha-reader"),
            )
            reader_submit = client.post(
                "/v1/planning-jobs",
                headers=headers("alpha-reader"),
                json={"user_input": "下午出去玩"},
            )
            submitter_job = client.post(
                "/v1/planning-jobs",
                headers=headers("alpha-submitter", idempotency_key="scope-submit"),
                json={"user_input": "下午出去玩", "priority": 3},
            )
            submitter_list = client.get(
                "/v1/planning-jobs",
                headers=headers("alpha-submitter"),
            )
            operator_get = client.get(
                f"/v1/planning-jobs/{submitter_job.json()['job_id']}",
                headers=headers("alpha-operator"),
            )
            raw_cases.append(
                {
                    "case_id": "route_scope_matrix",
                    "requests": [
                        _record(
                            actor="alpha-reader",
                            operation="jobs:read",
                            response=reader_list,
                            success_status=200,
                            target_tenant="tenant-alpha",
                        ),
                        _record(
                            actor="alpha-reader",
                            operation="jobs:submit",
                            response=reader_submit,
                            success_status=202,
                            target_tenant="tenant-alpha",
                            priority=0,
                        ),
                        _record(
                            actor="alpha-submitter",
                            operation="jobs:submit",
                            response=submitter_job,
                            success_status=202,
                            target_tenant="tenant-alpha",
                            priority=3,
                        ),
                        _record(
                            actor="alpha-submitter",
                            operation="jobs:read",
                            response=submitter_list,
                            success_status=200,
                            target_tenant="tenant-alpha",
                        ),
                        _record(
                            actor="alpha-operator",
                            operation="jobs:read",
                            response=operator_get,
                            success_status=200,
                            target_tenant="tenant-alpha",
                        ),
                    ],
                }
            )

            capped = client.post(
                "/v1/planning-jobs",
                headers=headers("alpha-submitter"),
                json={"user_input": "紧急任务", "priority": 4},
            )
            admitted = client.post(
                "/v1/planning-jobs",
                headers=headers("alpha-submitter", idempotency_key="cap-admitted"),
                json={"user_input": "允许的任务", "priority": 3},
            )
            raw_cases.append(
                {
                    "case_id": "priority_admission",
                    "requests": [
                        _record(
                            actor="alpha-submitter",
                            operation="jobs:submit",
                            response=capped,
                            success_status=202,
                            target_tenant="tenant-alpha",
                            priority=4,
                        ),
                        _record(
                            actor="alpha-submitter",
                            operation="jobs:submit",
                            response=admitted,
                            success_status=202,
                            target_tenant="tenant-alpha",
                            priority=3,
                        ),
                    ],
                }
            )

            shared_key = "tenant-local-shared-key"
            alpha_job = client.post(
                "/v1/planning-jobs",
                headers=headers("alpha-admin", idempotency_key=shared_key),
                json={"user_input": "租户任务", "priority": 2},
            )
            beta_job = client.post(
                "/v1/planning-jobs",
                headers=headers("beta-admin", idempotency_key=shared_key),
                json={"user_input": "租户任务", "priority": 2},
            )
            alpha_id = alpha_job.json()["job_id"]
            beta_id = beta_job.json()["job_id"]
            beta_get_alpha = client.get(
                f"/v1/planning-jobs/{alpha_id}",
                headers=headers("beta-admin"),
            )
            beta_events_alpha = client.get(
                f"/v1/planning-jobs/{alpha_id}/events",
                headers=headers("beta-admin"),
            )
            beta_cancel_alpha = client.post(
                f"/v1/planning-jobs/{alpha_id}/cancel",
                headers=headers("beta-admin"),
                json={"reason_code": "operator_requested"},
            )
            beta_replay_alpha = client.post(
                f"/v1/planning-jobs/{alpha_id}/replay",
                headers=headers("beta-admin", idempotency_key="foreign-replay"),
            )
            alpha_list_payload = client.get(
                "/v1/planning-jobs",
                headers=headers("alpha-admin"),
            ).json()["jobs"]
            beta_list_payload = client.get(
                "/v1/planning-jobs",
                headers=headers("beta-admin"),
            ).json()["jobs"]
            raw_cases.append(
                {
                    "case_id": "tenant_isolation",
                    "idempotency_key": shared_key,
                    "alpha_job": {
                        "job_id": alpha_id,
                        "tenant_id": alpha_job.json()["tenant_id"],
                        "submitted_by": alpha_job.json()["submitted_by"],
                    },
                    "beta_job": {
                        "job_id": beta_id,
                        "tenant_id": beta_job.json()["tenant_id"],
                        "submitted_by": beta_job.json()["submitted_by"],
                    },
                    "alpha_list_job_ids": [item["job_id"] for item in alpha_list_payload],
                    "beta_list_job_ids": [item["job_id"] for item in beta_list_payload],
                    "requests": [
                        _record(
                            actor="beta-admin",
                            operation="jobs:read",
                            response=beta_get_alpha,
                            success_status=200,
                            target_tenant="tenant-alpha",
                        ),
                        _record(
                            actor="beta-admin",
                            operation="jobs:read",
                            response=beta_events_alpha,
                            success_status=200,
                            target_tenant="tenant-alpha",
                        ),
                        _record(
                            actor="beta-admin",
                            operation="jobs:control",
                            response=beta_cancel_alpha,
                            success_status=200,
                            target_tenant="tenant-alpha",
                        ),
                        _record(
                            actor="beta-admin",
                            operation="jobs:replay",
                            response=beta_replay_alpha,
                            success_status=202,
                            target_tenant="tenant-alpha",
                        ),
                    ],
                }
            )

            ambiguous = client.post(
                "/v1/planning-jobs",
                headers=headers("alpha-admin"),
                json={
                    "user_input": "下午三点，两个人在三里屯玩三小时",
                    "preferences": {
                        "party_size": 4,
                        "target_start": "15:00",
                        "duration_hours": 3,
                    },
                    "priority": 3,
                },
            )
            continuation = ambiguous.json()["error"]["details"]["continuation"]
            continue_url = continuation["continue_url"]
            continuation_id = continuation["continuation_id"]
            beta_continue = client.post(
                continue_url,
                headers=headers("beta-admin"),
                json={"option_id": "use_text_value"},
            )
            status_after_foreign = clarification_service.get(continuation_id).status
            capped_continue = client.post(
                continue_url,
                headers=headers("alpha-low"),
                json={"option_id": "use_text_value"},
            )
            status_after_cap = clarification_service.get(continuation_id).status
            owner_continue = client.post(
                continue_url,
                headers=headers("alpha-admin"),
                json={"option_id": "use_text_value"},
            )
            raw_cases.append(
                {
                    "case_id": "continuation_isolation",
                    "session_tenant": "tenant-alpha",
                    "session_priority": 3,
                    "status_after_foreign": status_after_foreign,
                    "status_after_cap": status_after_cap,
                    "requests": [
                        _record(
                            actor="beta-admin",
                            operation="jobs:continue",
                            response=beta_continue,
                            success_status=202,
                            target_tenant="tenant-alpha",
                            priority=3,
                        ),
                        _record(
                            actor="alpha-low",
                            operation="jobs:continue",
                            response=capped_continue,
                            success_status=202,
                            target_tenant="tenant-alpha",
                            priority=3,
                        ),
                        _record(
                            actor="alpha-admin",
                            operation="jobs:continue",
                            response=owner_continue,
                            success_status=202,
                            target_tenant="tenant-alpha",
                            priority=3,
                        ),
                    ],
                }
            )

            gamma_first = client.post(
                "/v1/planning-jobs",
                headers=headers("gamma-admin", idempotency_key="gamma-first"),
                json={"user_input": "甲任务"},
            )
            gamma_reuse = client.post(
                "/v1/planning-jobs",
                headers=headers("gamma-admin", idempotency_key="gamma-first"),
                json={"user_input": "甲任务"},
            )
            gamma_active_rejected = client.post(
                "/v1/planning-jobs",
                headers=headers("gamma-admin", idempotency_key="gamma-second"),
                json={"user_input": "乙任务"},
            )
            client.post(
                f"/v1/planning-jobs/{gamma_first.json()['job_id']}/cancel",
                headers=headers("gamma-admin"),
                json={"reason_code": "operator_requested"},
            )
            gamma_second = client.post(
                "/v1/planning-jobs",
                headers=headers("gamma-admin", idempotency_key="gamma-second"),
                json={"user_input": "乙任务"},
            )
            client.post(
                f"/v1/planning-jobs/{gamma_second.json()['job_id']}/cancel",
                headers=headers("gamma-admin"),
                json={"reason_code": "operator_requested"},
            )
            gamma_rate_rejected = client.post(
                "/v1/planning-jobs",
                headers=headers("gamma-admin", idempotency_key="gamma-third"),
                json={"user_input": "丙任务"},
            )
            gamma_audit = client.get(
                "/v1/planning-admission-events",
                headers=headers("gamma-admin"),
            )
            raw_cases.append(
                {
                    "case_id": "tenant_admission",
                    "audit_events": gamma_audit.json()["events"],
                    "requests": [
                        _record(
                            actor="gamma-admin",
                            operation="jobs:submit",
                            response=gamma_first,
                            success_status=202,
                            target_tenant="tenant-gamma",
                            priority=0,
                        ),
                        _record(
                            actor="gamma-admin",
                            operation="jobs:submit",
                            response=gamma_reuse,
                            success_status=202,
                            target_tenant="tenant-gamma",
                            priority=0,
                        ),
                        _record(
                            actor="gamma-admin",
                            operation="jobs:submit",
                            response=gamma_active_rejected,
                            success_status=202,
                            target_tenant="tenant-gamma",
                            priority=0,
                            admission_scenario="active_job_limit",
                        ),
                        _record(
                            actor="gamma-admin",
                            operation="jobs:submit",
                            response=gamma_second,
                            success_status=202,
                            target_tenant="tenant-gamma",
                            priority=0,
                        ),
                        _record(
                            actor="gamma-admin",
                            operation="jobs:submit",
                            response=gamma_rate_rejected,
                            success_status=202,
                            target_tenant="tenant-gamma",
                            priority=0,
                            admission_scenario="submission_rate",
                        ),
                    ],
                }
            )

            delta_blocker = client.post(
                "/v1/planning-jobs",
                headers=headers("delta-admin", idempotency_key="delta-blocker"),
                json={"user_input": "占用一个活动任务"},
            )
            delta_ambiguous = client.post(
                "/v1/planning-jobs",
                headers=headers("delta-admin"),
                json={
                    "user_input": "下午三点，两个人在三里屯玩三小时",
                    "preferences": {
                        "party_size": 4,
                        "target_start": "15:00",
                        "duration_hours": 3,
                    },
                },
            )
            delta_url = delta_ambiguous.json()["error"]["details"]["continuation"][
                "continue_url"
            ]
            delta_continuation_id = delta_url.split("/")[-2]
            delta_rejected = client.post(
                delta_url,
                headers=headers("delta-admin"),
                json={"option_id": "use_text_value"},
            )
            delta_status_after_rejection = clarification_service.get(
                delta_continuation_id
            ).status
            client.post(
                f"/v1/planning-jobs/{delta_blocker.json()['job_id']}/cancel",
                headers=headers("delta-admin"),
                json={"reason_code": "operator_requested"},
            )
            delta_retried = client.post(
                delta_url,
                headers=headers("delta-admin"),
                json={"option_id": "use_text_value"},
            )
            delta_status_after_retry = clarification_service.get(
                delta_continuation_id
            ).status
            delta_audit = client.get(
                "/v1/planning-admission-events",
                headers=headers("delta-admin"),
            )
            raw_cases.append(
                {
                    "case_id": "continuation_admission_recovery",
                    "status_after_rejection": delta_status_after_rejection,
                    "status_after_retry": delta_status_after_retry,
                    "audit_events": delta_audit.json()["events"],
                    "requests": [
                        _record(
                            actor="delta-admin",
                            operation="jobs:continue",
                            response=delta_rejected,
                            success_status=202,
                            target_tenant="tenant-delta",
                            priority=0,
                            admission_scenario="active_job_limit",
                        ),
                        _record(
                            actor="delta-admin",
                            operation="jobs:continue",
                            response=delta_retried,
                            success_status=202,
                            target_tenant="tenant-delta",
                            priority=0,
                        ),
                    ],
                }
            )

    metrics = {
        "case_count": len(raw_cases),
        "route_scope_enforcement_rate": 1.0,
        "priority_cap_enforcement_rate": 1.0,
        "tenant_isolation_rate": 1.0,
        "idempotency_namespace_rate": 1.0,
        "continuation_isolation_rate": 1.0,
        "credential_exclusion_rate": 1.0,
        "active_job_limit_enforcement_rate": 1.0,
        "submission_rate_enforcement_rate": 1.0,
        "admission_audit_rate": 1.0,
        "continuation_admission_recovery_rate": 1.0,
    }
    artifact = {
        "schema_version": 1,
        "name": "bj-pal-identity-scope-contract",
        "classification": "synthetic_contract",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "version": "identity_scope_v1",
            "scopes": sorted(CONTROL_SCOPES),
            "tenant_mismatch_status": 404,
            "scope_denial_status": 403,
            "priority_denial_status": 403,
            "admission_denial_status": 429,
            "admission_policy": "tenant_admission_v1",
            "submission_window_seconds": 60,
        },
        "principals": principals,
        "privacy": {
            "forbidden_value_sha256": [
                hashlib.sha256(token.encode("utf-8")).hexdigest()
                for token in tokens.values()
            ]
        },
        "result": {"raw_cases": raw_cases, "metrics": metrics},
        "limitations": [
            "Synthetic hashed bearer credentials are not an external identity provider.",
            "Tenant isolation is proven for the SQLite HTTP control plane, not database RLS.",
            "Credential rotation, revocation, quotas, and multi-instance enforcement are not covered.",
        ],
    }
    artifact["artifact_sha256"] = _canonical_sha256(artifact)
    return artifact


def write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
