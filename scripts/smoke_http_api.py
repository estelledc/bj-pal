"""Run a no-socket HTTP contract smoke test against the public demo backend."""

from __future__ import annotations

import os
import hashlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ["BJ_PAL_LLM"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402
from clarifications import (  # noqa: E402
    ClarificationContinuationService,
    ClarificationRepository,
)
from http_api.app import create_app  # noqa: E402
from http_api.auth import (  # noqa: E402
    CONTROL_SCOPES,
    JOBS_SUBMIT,
    OPERATIONS_APPROVE,
    OPERATIONS_READ,
    OPERATIONS_RECONCILE,
    ControlPlaneCredential,
    ControlPrincipal,
)
from jobs import PlanningJobRepository, PlanningJobService  # noqa: E402
from operations import (  # noqa: E402
    DeterministicSandboxBookingProvider,
    SideEffectOperationRepository,
    SideEffectOperationService,
)
from outcomes import PlanFeedbackRepository, PlanFeedbackService  # noqa: E402


ADMIN_TOKEN = "smoke-admin-token-0123456789-abcdef-0001"
LOW_PRIORITY_TOKEN = "smoke-low-token-0123456789-abcdef-000002"
APPROVER_TOKEN = "smoke-approver-token-0123456789-abcdef-0003"


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="bj-pal-http-smoke-")
    job_service = PlanningJobService(
        repository=PlanningJobRepository(Path(temporary.name) / "jobs.db")
    )
    clarification_service = ClarificationContinuationService(
        repository=ClarificationRepository(
            Path(temporary.name) / "clarifications.db"
        )
    )
    operation_service = SideEffectOperationService(
        repository=SideEffectOperationRepository(
            Path(temporary.name) / "operations.db"
        )
    )
    feedback_service = PlanFeedbackService(
        repository=PlanFeedbackRepository(
            Path(temporary.name) / "feedback.db"
        )
    )
    app = create_app(
        job_service=job_service,
        clarification_service=clarification_service,
        operation_service=operation_service,
        feedback_service=feedback_service,
        control_credentials=(
            ControlPlaneCredential.from_token(
                token=ADMIN_TOKEN,
                principal=ControlPrincipal(
                    principal_id="smoke-admin",
                    tenant_id="smoke-tenant",
                    scopes=CONTROL_SCOPES,
                    max_priority=7,
                    tenant_active_job_limit=1,
                    tenant_submission_limit_per_minute=2,
                ),
            ),
            ControlPlaneCredential.from_token(
                token=LOW_PRIORITY_TOKEN,
                principal=ControlPrincipal(
                    principal_id="smoke-low",
                    tenant_id="smoke-tenant",
                    scopes=frozenset({JOBS_SUBMIT}),
                    max_priority=1,
                    tenant_active_job_limit=1,
                    tenant_submission_limit_per_minute=2,
                ),
            ),
            ControlPlaneCredential.from_token(
                token=APPROVER_TOKEN,
                principal=ControlPrincipal(
                    principal_id="smoke-human-approver",
                    tenant_id="smoke-tenant",
                    scopes=frozenset({OPERATIONS_APPROVE, OPERATIONS_READ}),
                    max_priority=0,
                    tenant_active_job_limit=1,
                    tenant_submission_limit_per_minute=2,
                ),
            ),
        ),
    )
    with TestClient(
        app,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    ) as client:
        health = client.get("/healthz", headers={"X-Request-ID": "smoke-health"})
        health.raise_for_status()
        readiness = client.get("/readyz")
        readiness.raise_for_status()
        unauthorized = client.get(
            "/v1/planning-jobs",
            headers={"Authorization": "Bearer wrong-smoke-token-0123456789-abcdef"},
        )
        assert unauthorized.status_code == 401
        assert ADMIN_TOKEN not in unauthorized.text
        plan = client.post(
            "/v1/plans",
            headers={"X-Request-ID": "smoke-plan"},
            json={
                "user_input": "周末下午带 5 岁孩子在五道营附近玩四小时，不吃辣",
                "persona": "family",
                "preferences": {
                    "party_size": 3,
                    "has_child": True,
                    "child_age": 5,
                    "diet_flags": ["no_spicy"],
                    "duration_hours": 4,
                },
            },
        )
        plan.raise_for_status()
        payload = plan.json()
        conflict = client.post(
            "/v1/plans",
            json={
                "user_input": "下午三点，两个人在三里屯玩三小时",
                "preferences": {
                    "party_size": 4,
                    "target_start": "15:00",
                    "duration_hours": 3,
                },
            },
        )
        assert conflict.status_code == 409
        continuation = conflict.json()["error"]["details"]["continuation"]
        resumed = client.post(
            continuation["continue_url"],
            json={"option_id": "use_text_value"},
        )
        resumed.raise_for_status()
        repeated = client.post(
            continuation["continue_url"],
            json={"option_id": "use_text_value"},
        )
        repeated.raise_for_status()
        assert resumed.json()["request"]["preferences"]["party_size"] == 2
        assert (
            repeated.json()["final_plan"]["plan_id"]
            == resumed.json()["final_plan"]["plan_id"]
        )
        job = client.post(
            "/v1/planning-jobs",
            headers={"X-Request-ID": "smoke-job", "Idempotency-Key": "smoke-http-v1"},
            json={
                "user_input": "周末下午在五道营附近走走",
                "deadline_seconds": 30,
                "priority": 7,
            },
        )
        job.raise_for_status()
        assert job.json()["deadline_seconds"] == 30
        assert job.json()["priority"] == 7
        assert job.json()["tenant_id"] == "smoke-tenant"
        assert job.json()["submitted_by"] == "smoke-admin"
        assert job.json()["deadline_at"] is not None
        capped = client.post(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {LOW_PRIORITY_TOKEN}"},
            json={"user_input": "不应越权的紧急任务", "priority": 2},
        )
        assert capped.status_code == 403
        assert capped.json()["error"]["code"] == "priority_forbidden"
        capacity_denied = client.post(
            "/v1/planning-jobs",
            headers={"Idempotency-Key": "smoke-http-capacity"},
            json={"user_input": "活动任务上限应拒绝", "priority": 1},
        )
        assert capacity_denied.status_code == 429
        assert capacity_denied.json()["error"]["code"] == (
            "tenant_active_job_limit_exceeded"
        )
        job_id = job.json()["job_id"]
        persisted = client.get(f"/v1/planning-jobs/{job_id}")
        persisted.raise_for_status()
        events = client.get(f"/v1/planning-jobs/{job_id}/events")
        events.raise_for_status()
        assert [event["event_type"] for event in events.json()["events"]] == ["submitted"]
        stream = client.get(
            f"/v1/planning-jobs/{job_id}/events/stream",
            params={"stream_seconds": 0.01, "poll_interval_ms": 10},
        )
        stream.raise_for_status()
        assert "event: submitted" in stream.text
        assert ": stream-timeout" in stream.text
        queued_jobs = client.get("/v1/planning-jobs", params={"status": "queued"})
        queued_jobs.raise_for_status()
        assert [item["job_id"] for item in queued_jobs.json()["jobs"]] == [job_id]
        cancelled = client.post(
            f"/v1/planning-jobs/{job_id}/cancel",
            json={"reason_code": "operator_requested"},
        )
        cancelled.raise_for_status()
        assert cancelled.json()["status"] == "cancelled"
        cancelled_stream = client.get(f"/v1/planning-jobs/{job_id}/events/stream")
        cancelled_stream.raise_for_status()
        assert "event: cancelled" in cancelled_stream.text
        second_job = client.post(
            "/v1/planning-jobs",
            headers={"Idempotency-Key": "smoke-http-second"},
            json={"user_input": "窗口内第二个任务", "priority": 1},
        )
        second_job.raise_for_status()
        second_cancelled = client.post(
            f"/v1/planning-jobs/{second_job.json()['job_id']}/cancel",
            json={"reason_code": "operator_requested"},
        )
        second_cancelled.raise_for_status()
        rate_denied = client.post(
            "/v1/planning-jobs",
            headers={"Idempotency-Key": "smoke-http-rate"},
            json={"user_input": "窗口内第三个任务", "priority": 1},
        )
        assert rate_denied.status_code == 429
        assert rate_denied.json()["error"]["code"] == (
            "tenant_submission_rate_exceeded"
        )
        assert int(rate_denied.headers["Retry-After"]) >= 1
        admission_events = client.get("/v1/planning-admission-events")
        admission_events.raise_for_status()
        assert [
            event["decision"] for event in admission_events.json()["events"]
        ] == ["admitted", "rejected", "admitted", "rejected"]
        quote_valid_until = (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        operation = client.post(
            "/v1/operations",
            headers={"Idempotency-Key": "smoke-operation-v1"},
            json={
                "operation_kind": "restaurant_booking",
                "action": {
                    "poi_id": "smoke-poi",
                    "poi_name": "Smoke Sandbox Restaurant",
                    "target_time": "18:30",
                    "party_size": 2,
                    "contact_reference": "smoke-contact-ref",
                },
                "quote": {
                    "provider": "bj-pal-sandbox",
                    "reference": "smoke-quote-v1",
                    "valid_until": quote_valid_until,
                    "currency": "CNY",
                    "amount_minor": 12_800,
                    "terms_sha256": hashlib.sha256(
                        b"smoke sandbox terms"
                    ).hexdigest(),
                    "sandbox": True,
                },
            },
        )
        operation.raise_for_status()
        operation_id = operation.json()["operation_id"]
        approval_sha256 = operation.json()["approval_sha256"]
        self_approval = client.post(
            f"/v1/operations/{operation_id}/approve",
            json={"expected_approval_sha256": approval_sha256},
        )
        assert self_approval.status_code == 403
        approved = client.post(
            f"/v1/operations/{operation_id}/approve",
            headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
            json={"expected_approval_sha256": approval_sha256},
        )
        approved.raise_for_status()
        executed = operation_service.run_once(worker_id="smoke-operation-worker")
        assert executed is not None and executed.status == "succeeded"
        restored_operation = client.get(f"/v1/operations/{operation_id}")
        restored_operation.raise_for_status()
        assert restored_operation.json()["receipt"]["sandbox"] is True
        operation_events = client.get(f"/v1/operations/{operation_id}/events")
        operation_events.raise_for_status()
        assert [
            event["event_type"] for event in operation_events.json()["events"]
        ] == [
            "requested",
            "approved",
            "execution_started",
            "execution_succeeded",
        ]
        operation_service.provider = DeterministicSandboxBookingProvider(
            outcome="uncertain",
            lookup_outcome="confirmed",
        )
        uncertain_operation = client.post(
            "/v1/operations",
            headers={"Idempotency-Key": "smoke-operation-reconcile-v1"},
            json={
                "operation_kind": "restaurant_booking",
                "action": {
                    "poi_id": "smoke-reconcile-poi",
                    "poi_name": "Smoke Reconciliation Restaurant",
                    "target_time": "19:00",
                    "party_size": 2,
                    "contact_reference": "smoke-reconcile-contact-ref",
                },
                "quote": {
                    "provider": "bj-pal-sandbox",
                    "reference": "smoke-reconcile-quote-v1",
                    "valid_until": quote_valid_until,
                    "currency": "CNY",
                    "amount_minor": 13_800,
                    "terms_sha256": hashlib.sha256(
                        b"smoke reconciliation sandbox terms"
                    ).hexdigest(),
                    "sandbox": True,
                },
            },
        )
        uncertain_operation.raise_for_status()
        uncertain_operation_id = uncertain_operation.json()["operation_id"]
        uncertain_approval_sha256 = uncertain_operation.json()["approval_sha256"]
        uncertain_approved = client.post(
            f"/v1/operations/{uncertain_operation_id}/approve",
            headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
            json={
                "expected_approval_sha256": uncertain_approval_sha256,
            },
        )
        uncertain_approved.raise_for_status()
        uncertain_execution = operation_service.run_once(
            worker_id="smoke-uncertain-operation-worker"
        )
        assert uncertain_execution is not None
        assert uncertain_execution.status == "uncertain"
        reconciled = client.post(
            f"/v1/operations/{uncertain_operation_id}/reconcile"
        )
        reconciled.raise_for_status()
        assert reconciled.json()["status"] == "succeeded"
        reconciliations = client.get(
            f"/v1/operations/{uncertain_operation_id}/reconciliations"
        )
        reconciliations.raise_for_status()
        reconciliation_items = reconciliations.json()["reconciliations"]
        assert len(reconciliation_items) == 1
        assert reconciliation_items[0]["outcome"] == "confirmed"
        assert (
            reconciliation_items[0]["receipt_sha256"]
            == reconciled.json()["receipt_sha256"]
        )
        assert OPERATIONS_RECONCILE in CONTROL_SCOPES
        print(
            "http-api smoke: "
            f"health={health.json()['status']} "
            f"ready={readiness.json()['status']} "
            f"steps={len(payload['final_plan']['steps'])} "
            f"profile={payload['data_profile']['name']} "
            f"job={persisted.json()['status']} "
            f"events={len(events.json()['events'])} "
            f"sse={'ok' if 'event: submitted' in stream.text else 'failed'} "
            f"control={'ok' if cancelled.json()['status'] == 'cancelled' else 'failed'} "
            f"auth={'ok' if unauthorized.status_code == 401 else 'failed'} "
            f"clarification={'ok' if resumed.status_code == 200 else 'failed'} "
            f"deadline={job.json()['deadline_seconds']}s "
            f"priority={job.json()['priority']} "
            f"tenant={job.json()['tenant_id']} "
            f"principal={job.json()['submitted_by']} "
            f"priority_cap={'ok' if capped.status_code == 403 else 'failed'} "
            f"admission={'ok' if rate_denied.status_code == 429 else 'failed'} "
            f"admission_events={len(admission_events.json()['events'])} "
            f"operation={restored_operation.json()['status']} "
            f"approval_separation={'ok' if self_approval.status_code == 403 else 'failed'} "
            f"receipt={'ok' if restored_operation.json()['receipt']['sandbox'] else 'failed'} "
            f"reconciliation={'ok' if reconciled.json()['status'] == 'succeeded' else 'failed'} "
            f"request_id={plan.headers['X-Request-ID']}"
        )
    temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
