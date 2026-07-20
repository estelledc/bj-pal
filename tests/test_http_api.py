from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.types import Plan, RerouteEvent, Step  # noqa: E402
from application import (  # noqa: E402
    ModelOutputContractError,
    ModelOutputContractSnapshot,
    PlanRequest,
    PlanResult,
    PlanningService,
    RequirementNormalizer,
)
from clarifications import (  # noqa: E402
    ClarificationContinuationService,
    ClarificationRepository,
)
from data_profile import DataProfile  # noqa: E402
from http_api.app import SERVICE_VERSION, create_app  # noqa: E402
from http_api.auth import (  # noqa: E402
    CONTROL_SCOPES,
    JOBS_CONTROL,
    JOBS_READ,
    JOBS_REPLAY,
    JOBS_SUBMIT,
    OPERATIONS_APPROVE,
    OPERATIONS_READ,
    OPERATIONS_RECONCILE,
    OPERATIONS_REQUEST,
    TRIALS_MANAGE,
    TRIALS_READ,
    ControlPlaneCredential,
    ControlPrincipal,
)
from http_api.schemas import (  # noqa: E402
    ModelOutputContextResponse,
    PlanCreateRequest,
    PlanCreateResponse,
    ReadinessResponse,
)
from jobs import PlanningJobRepository, PlanningJobService  # noqa: E402
from operations import (  # noqa: E402
    DeterministicSandboxBookingProvider,
    SideEffectOperationRepository,
    SideEffectOperationService,
)
from outcomes import PlanFeedbackRepository, PlanFeedbackService  # noqa: E402


CONTROL_TOKEN = "test-control-token-0123456789-abcdef"
CONTROL_HEADERS = {"Authorization": f"Bearer {CONTROL_TOKEN}"}


@pytest.fixture(autouse=True)
def configure_control_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BJ_PAL_CONTROL_TOKEN", CONTROL_TOKEN)
    monkeypatch.setenv("BJ_PAL_FEEDBACK_DB", str(tmp_path / "default-feedback.db"))


def _client(app) -> TestClient:
    return TestClient(app, headers=CONTROL_HEADERS)


def _credential(
    *,
    token: str,
    principal_id: str,
    tenant_id: str,
    scopes: frozenset[str] = CONTROL_SCOPES,
    max_priority: int = 9,
    tenant_active_job_limit: int = 100,
    tenant_submission_limit_per_minute: int = 60,
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


def _sandbox_operation_payload() -> dict:
    valid_until = (
        datetime.now(timezone.utc) + timedelta(minutes=10)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "operation_kind": "restaurant_booking",
        "action": {
            "poi_id": "poi-sandbox-http",
            "poi_name": "HTTP 测试餐厅",
            "target_time": "18:30",
            "party_size": 2,
            "contact_reference": "contact-http-001",
        },
        "quote": {
            "provider": "bj-pal-sandbox",
            "reference": "quote-http-001",
            "valid_until": valid_until,
            "currency": "CNY",
            "amount_minor": 12_800,
            "terms_sha256": hashlib.sha256(b"sandbox http terms").hexdigest(),
            "sandbox": True,
        },
        "approval_ttl_seconds": 300,
    }


def _profile() -> DataProfile:
    return DataProfile(
        name="demo",
        classification="synthetic",
        public_reproducible=True,
        sources={"pois": "fixture"},
        counts={"pois": 1},
        limitations=("not live",),
    )


def _plan(plan_id: str, poi_name: str, *, rerouted: bool = False) -> Plan:
    return Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=[
            Step(
                step_index=1,
                kind="meal",
                poi_id=f"poi-{plan_id}",
                poi_name=poi_name,
                start_time="14:00",
                rationale="contract fixture",
                is_rerouted=rerouted,
                confidence=0.61,
                confidence_source="evidence_support_v1",
                confidence_factors={"semantics": "not a calibrated probability"},
            )
        ],
        plan_id=plan_id,
    )


class StubService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests = []

    def execute(self, request, **kwargs):
        del kwargs
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        initial = _plan("plan-api", "初始餐厅")
        final = _plan("plan-api", "替补餐厅", rerouted=True)
        return PlanResult(
            request=request,
            initial_plan=initial,
            final_plan=final,
            reroute_events=(
                RerouteEvent(
                    failed_step_idx=0,
                    failed_poi_name="初始餐厅",
                    reason="queue",
                    replacement_poi_name="替补餐厅",
                    change_summary_zh="排队过长，只替换一站",
                ),
            ),
            data_profile=_profile(),
            requirements=RequirementNormalizer().normalize(request),
        )


def _ready(status: str = "ready") -> ReadinessResponse:
    return ReadinessResponse(
        status=status,
        data_profile="demo" if status == "ready" else "unknown",
        classification="synthetic" if status == "ready" else "unknown",
        checks={"dataset_manifest": "ok" if status == "ready" else "missing"},
    )


def test_health_and_readiness_contracts_include_request_ids() -> None:
    app = create_app(service=StubService(), readiness_probe=_ready)
    with _client(app) as client:
        health = client.get("/healthz", headers={"X-Request-ID": "test-health"})
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "bj-pal", "version": SERVICE_VERSION}
    assert health.headers["X-Request-ID"] == "test-health"
    assert ready.status_code == 200
    assert ready.json()["data_profile"] == "demo"
    assert ready.headers["X-Request-ID"].startswith("req-")


def test_sync_plan_issues_ephemeral_feedback_capability_and_accepts_two_phases(
    tmp_path,
) -> None:
    feedback_service = PlanFeedbackService(
        PlanFeedbackRepository(tmp_path / "feedback.db")
    )
    app = create_app(
        service=StubService(),
        readiness_probe=_ready,
        feedback_service=feedback_service,
    )
    request_payload = {
        "user_input": "周末带家人逛半天",
        "persona": "family",
        "preferences": {"party_size": 3},
        "area_anchor": "五道营-雍和宫片区",
    }
    with _client(app) as client:
        planned = client.post("/v1/plans", json=request_payload)
        invitation = planned.json()["feedback"]
        feedback_headers = {
            "X-Feedback-Capability": invitation["capability"],
            "Idempotency-Key": "http-decision-1",
        }
        decision = client.post(
            invitation["feedback_url"],
            headers=feedback_headers,
            json={"phase": "decision", "value": "accepted"},
        )
        replay = client.post(
            invitation["feedback_url"],
            headers=feedback_headers,
            json={"phase": "decision", "value": "accepted"},
        )
        invalid = client.post(
            invitation["feedback_url"],
            headers={
                "X-Feedback-Capability": invitation["capability"],
                "Idempotency-Key": "http-invalid",
            },
            json={"phase": "outcome", "value": "abandoned"},
        )
        outcome = client.post(
            invitation["feedback_url"],
            headers={
                "X-Feedback-Capability": invitation["capability"],
                "Idempotency-Key": "http-outcome-1",
            },
            json={
                "phase": "outcome",
                "value": "partially_completed",
                "reason_codes": ["weather_issue"],
            },
        )
        reports = client.get(
            invitation["feedback_url"],
            headers={"X-Feedback-Capability": invitation["capability"]},
        )
        wrong_plan = client.get(
            "/v1/plans/plan-other/feedback",
            headers={"X-Feedback-Capability": invitation["capability"]},
        )
        summary = client.get("/v1/feedback-summary")

    assert planned.status_code == 200
    assert invitation["classification"] == "self_reported_unverified"
    assert invitation["capability"].startswith("fbcap-")
    assert invitation["capability"] not in json.dumps(
        planned.json()["execution"], sort_keys=True
    )
    assert decision.status_code == replay.status_code == 201
    assert decision.json() == replay.json()
    assert invalid.status_code == 422
    assert outcome.status_code == 201
    assert [item["phase"] for item in reports.json()["reports"]] == [
        "decision",
        "outcome",
    ]
    assert wrong_plan.status_code == 404
    assert summary.json()["decision_acceptance_rate"] is None
    assert summary.json()["outcome_completion_rate"] is None


def test_trial_http_flow_binds_consent_tenant_participant_plan_and_snapshot(
    tmp_path,
) -> None:
    feedback_service = PlanFeedbackService(
        PlanFeedbackRepository(tmp_path / "feedback.db")
    )
    planning = StubService()
    alpha_manage_token = "alpha-trial-manage-token-0123456789abcdef"
    alpha_read_token = "alpha-trial-reader-token-0123456789abcdef"
    beta_manage_token = "beta-trial-manage-token-0123456789-abcdef"
    credentials = (
        _credential(
            token=alpha_manage_token,
            principal_id="alpha-trial-manager",
            tenant_id="alpha",
            scopes=frozenset({TRIALS_MANAGE, TRIALS_READ}),
        ),
        _credential(
            token=alpha_read_token,
            principal_id="alpha-trial-reader",
            tenant_id="alpha",
            scopes=frozenset({TRIALS_READ}),
        ),
        _credential(
            token=beta_manage_token,
            principal_id="beta-trial-manager",
            tenant_id="beta",
            scopes=frozenset({TRIALS_MANAGE, TRIALS_READ}),
        ),
    )
    app = create_app(
        service=planning,
        readiness_probe=_ready,
        feedback_service=feedback_service,
        control_credentials=credentials,
    )
    alpha_manage = {"Authorization": f"Bearer {alpha_manage_token}"}
    alpha_read = {"Authorization": f"Bearer {alpha_read_token}"}
    beta_manage = {"Authorization": f"Bearer {beta_manage_token}"}
    request_payload = {
        "user_input": "周末带家人逛半天",
        "persona": "family",
        "preferences": {"party_size": 3},
        "area_anchor": "五道营-雍和宫片区",
    }

    with TestClient(app) as client:
        forbidden_create = client.post(
            "/v1/trials",
            headers=alpha_read,
            json={},
        )
        created = client.post(
            "/v1/trials",
            headers=alpha_manage,
            json={"duration_days": 7, "retention_days": 30},
        )
        trial = created.json()
        trial_id = trial["trial_id"]
        beta_cross_tenant = client.post(
            f"/v1/trials/{trial_id}/enrollment-invitations",
            headers=beta_manage,
            json={},
        )
        enrollment = client.post(
            f"/v1/trials/{trial_id}/enrollment-invitations",
            headers=alpha_manage,
            json={},
        )
        enrollment_payload = enrollment.json()
        wrong_consent = client.post(
            enrollment_payload["enroll_url"],
            headers={
                "X-Trial-Enrollment-Capability": enrollment_payload["capability"]
            },
            json={
                "consent_notice_sha256": "0" * 64,
                "consent_attested": True,
            },
        )
        enrolled = client.post(
            enrollment_payload["enroll_url"],
            headers={
                "X-Trial-Enrollment-Capability": enrollment_payload["capability"]
            },
            json={
                "consent_notice_sha256": trial["consent_notice_sha256"],
                "consent_attested": True,
            },
        )
        participant = enrolled.json()
        replay_enrollment = client.post(
            enrollment_payload["enroll_url"],
            headers={
                "X-Trial-Enrollment-Capability": enrollment_payload["capability"]
            },
            json={
                "consent_notice_sha256": trial["consent_notice_sha256"],
                "consent_attested": True,
            },
        )
        planned = client.post(
            "/v1/plans",
            headers={
                "X-Trial-Participant-Capability": participant["capability"]
            },
            json=request_payload,
        )
        feedback_invitation = planned.json()["feedback"]
        decision = client.post(
            feedback_invitation["feedback_url"],
            headers={
                "X-Feedback-Capability": feedback_invitation["capability"],
                "Idempotency-Key": "trial-http-decision",
            },
            json={"phase": "decision", "value": "accepted"},
        )
        summary = client.get(
            f"/v1/trials/{trial_id}/summary",
            headers=alpha_read,
        )
        cross_tenant_summary = client.get(
            f"/v1/trials/{trial_id}/summary",
            headers=beta_manage,
        )
        closed = client.post(
            f"/v1/trials/{trial_id}/close",
            headers=alpha_manage,
        )
        request_count_before_closed_plan = len(planning.requests)
        closed_plan = client.post(
            "/v1/plans",
            headers={
                "X-Trial-Participant-Capability": participant["capability"]
            },
            json=request_payload,
        )

    assert forbidden_create.status_code == 403
    assert created.status_code == 201
    assert beta_cross_tenant.status_code == 404
    assert wrong_consent.status_code == 422
    assert enrolled.status_code == 201
    assert replay_enrollment.status_code == 409
    assert planned.status_code == 200
    assert feedback_invitation["version"] == "feedback_invitation_v2"
    assert feedback_invitation["trial_id"] == trial_id
    assert feedback_invitation["participant_id"] == participant["participant_id"]
    assert decision.status_code == 201
    assert decision.json()["version"] == "plan_feedback_report_v2"
    assert summary.status_code == 200
    assert summary.json()["phase_participant_counts"] == {
        "decision": 1,
        "outcome": 0,
    }
    assert summary.json()["decision_acceptance_rate"] is None
    assert cross_tenant_summary.status_code == 404
    assert closed.status_code == 200
    assert closed.json()["version"] == "trial_evidence_snapshot_v1"
    assert closed_plan.status_code == 409
    assert closed_plan.json()["error"]["code"] == "trial_closed"
    assert len(planning.requests) == request_count_before_closed_plan
    assert summary.json()["value_counts"] == {}
    assert summary.json()["reason_counts"] == {}


def test_job_control_plane_auth_is_fail_closed_and_does_not_expose_token() -> None:
    unconfigured = create_app(
        service=StubService(),
        readiness_probe=_ready,
        control_token="too-short",
    )
    configured = create_app(
        service=StubService(),
        readiness_probe=_ready,
        control_token=CONTROL_TOKEN,
    )

    with TestClient(unconfigured) as client:
        unavailable = client.get("/v1/planning-jobs")
        public_health = client.get("/healthz")
    with TestClient(configured) as client:
        missing = client.get("/v1/planning-jobs")
        wrong = client.get(
            "/v1/planning-jobs",
            headers={"Authorization": "Bearer wrong-control-token-0123456789"},
        )
        missing_continuation = client.post(
            "/v1/clarifications/clar-00000000000000000000000000000000/planning-job",
            json={"option_id": "use_text_value"},
        )
    with _client(configured) as client:
        allowed = client.get("/v1/planning-jobs")

    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "control_plane_not_configured"
    assert public_health.status_code == 200
    assert missing.status_code == wrong.status_code == 401
    assert missing_continuation.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert missing.json()["error"]["code"] == "control_plane_unauthorized"
    assert allowed.status_code == 200
    assert CONTROL_TOKEN not in unavailable.text + missing.text + wrong.text + allowed.text


def test_identity_scopes_and_priority_caps_are_enforced_per_route(tmp_path) -> None:
    submit_token = "submit-token-0123456789-abcdef-tenant-alpha"
    read_token = "reader-token-0123456789-abcdef-tenant-alpha"
    control_token = "operator-token-0123456789-abcdef-tenant-alpha"
    credentials = (
        _credential(
            token=submit_token,
            principal_id="alpha-submitter",
            tenant_id="tenant-alpha",
            scopes=frozenset({JOBS_SUBMIT}),
            max_priority=3,
        ),
        _credential(
            token=read_token,
            principal_id="alpha-reader",
            tenant_id="tenant-alpha",
            scopes=frozenset({JOBS_READ}),
            max_priority=0,
        ),
        _credential(
            token=control_token,
            principal_id="alpha-operator",
            tenant_id="tenant-alpha",
            scopes=frozenset({JOBS_READ, JOBS_CONTROL, JOBS_REPLAY}),
            max_priority=9,
        ),
    )
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubService(),
    )
    app = create_app(
        service=StubService(),
        readiness_probe=_ready,
        job_service=jobs,
        control_credentials=credentials,
    )

    with TestClient(app) as client:
        reader_list = client.get(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        reader_submit = client.post(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {read_token}"},
            json={"user_input": "下午出去玩"},
        )
        capped = client.post(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {submit_token}"},
            json={"user_input": "下午出去玩", "priority": 4},
        )
        submitted = client.post(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {submit_token}"},
            json={"user_input": "下午出去玩", "priority": 3},
        )
        submitter_list = client.get(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {submit_token}"},
        )
        operator_read = client.get(
            f"/v1/planning-jobs/{submitted.json()['job_id']}",
            headers={"Authorization": f"Bearer {control_token}"},
        )

    assert reader_list.status_code == 200
    assert reader_submit.status_code == submitter_list.status_code == 403
    assert reader_submit.json()["error"]["code"] == "control_plane_forbidden"
    assert capped.status_code == 403
    assert capped.json()["error"]["code"] == "priority_forbidden"
    assert submitted.status_code == 202
    assert submitted.json()["tenant_id"] == "tenant-alpha"
    assert submitted.json()["submitted_by"] == "alpha-submitter"
    assert submitted.json()["priority"] == 3
    assert operator_read.status_code == 200
    serialized = reader_submit.text + capped.text + submitted.text
    assert submit_token not in serialized and read_token not in serialized


def test_tenant_isolation_covers_idempotency_jobs_events_controls_and_continuations(
    tmp_path,
) -> None:
    alpha_token = "alpha-admin-token-0123456789-abcdef-0001"
    beta_token = "beta-admin-token-0123456789-abcdef-00002"
    alpha_low_token = "alpha-low-token-0123456789-abcdef-000003"
    credentials = (
        _credential(
            token=alpha_token,
            principal_id="alpha-admin",
            tenant_id="tenant-alpha",
        ),
        _credential(
            token=beta_token,
            principal_id="beta-admin",
            tenant_id="tenant-beta",
        ),
        _credential(
            token=alpha_low_token,
            principal_id="alpha-low-priority",
            tenant_id="tenant-alpha",
            scopes=frozenset({JOBS_SUBMIT}),
            max_priority=1,
        ),
    )
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubService(),
    )
    clarification_service = ClarificationContinuationService(
        repository=ClarificationRepository(tmp_path / "clarifications.db")
    )
    app = create_app(
        service=StubService(),
        readiness_probe=_ready,
        job_service=jobs,
        clarification_service=clarification_service,
        control_credentials=credentials,
    )
    alpha_headers = {
        "Authorization": f"Bearer {alpha_token}",
        "Idempotency-Key": "same-tenant-local-key",
    }
    beta_headers = {
        "Authorization": f"Bearer {beta_token}",
        "Idempotency-Key": "same-tenant-local-key",
    }
    payload = {"user_input": "下午出去玩", "priority": 2}
    ambiguous = {
        "user_input": "下午三点，两个人在三里屯玩三小时",
        "preferences": {
            "party_size": 4,
            "target_start": "15:00",
            "duration_hours": 3,
        },
        "priority": 3,
    }

    with TestClient(app) as client:
        alpha = client.post("/v1/planning-jobs", headers=alpha_headers, json=payload)
        beta = client.post("/v1/planning-jobs", headers=beta_headers, json=payload)
        alpha_list = client.get(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {alpha_token}"},
        )
        beta_list = client.get(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {beta_token}"},
        )
        foreign_get = client.get(
            f"/v1/planning-jobs/{alpha.json()['job_id']}",
            headers={"Authorization": f"Bearer {beta_token}"},
        )
        foreign_events = client.get(
            f"/v1/planning-jobs/{alpha.json()['job_id']}/events",
            headers={"Authorization": f"Bearer {beta_token}"},
        )
        foreign_cancel = client.post(
            f"/v1/planning-jobs/{alpha.json()['job_id']}/cancel",
            headers={"Authorization": f"Bearer {beta_token}"},
            json={"reason_code": "operator_requested"},
        )
        foreign_replay = client.post(
            f"/v1/planning-jobs/{alpha.json()['job_id']}/replay",
            headers={
                "Authorization": f"Bearer {beta_token}",
                "Idempotency-Key": "foreign-replay-key",
            },
        )
        clarification = client.post(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {alpha_token}"},
            json=ambiguous,
        )
        continue_url = clarification.json()["error"]["details"]["continuation"][
            "continue_url"
        ]
        foreign_continue = client.post(
            continue_url,
            headers={"Authorization": f"Bearer {beta_token}"},
            json={"option_id": "use_text_value"},
        )
        pending_after_foreign = clarification_service.get(continue_url.split("/")[-2])
        capped_continue = client.post(
            continue_url,
            headers={"Authorization": f"Bearer {alpha_low_token}"},
            json={"option_id": "use_text_value"},
        )
        pending_after_cap = clarification_service.get(continue_url.split("/")[-2])
        owner_continue = client.post(
            continue_url,
            headers={"Authorization": f"Bearer {alpha_token}"},
            json={"option_id": "use_text_value"},
        )

    assert alpha.status_code == beta.status_code == 202
    assert alpha.json()["job_id"] != beta.json()["job_id"]
    assert alpha.json()["tenant_id"] == "tenant-alpha"
    assert beta.json()["tenant_id"] == "tenant-beta"
    assert [item["job_id"] for item in alpha_list.json()["jobs"]] == [
        alpha.json()["job_id"]
    ]
    assert [item["job_id"] for item in beta_list.json()["jobs"]] == [
        beta.json()["job_id"]
    ]
    assert {
        foreign_get.status_code,
        foreign_events.status_code,
        foreign_cancel.status_code,
        foreign_replay.status_code,
        foreign_continue.status_code,
    } == {404}
    assert pending_after_foreign is not None and pending_after_foreign.status == "pending"
    assert capped_continue.status_code == 403
    assert capped_continue.json()["error"]["code"] == "priority_forbidden"
    assert pending_after_cap is not None and pending_after_cap.status == "pending"
    session = clarification_service.get(continue_url.split("/")[-2])
    assert session is not None and session.status == "completed"
    assert owner_continue.status_code == 202
    assert owner_continue.json()["tenant_id"] == "tenant-alpha"
    assert owner_continue.json()["submitted_by"] == "alpha-admin"


def test_hashed_environment_registry_loads_and_malformed_registry_fails_closed(
    monkeypatch,
    tmp_path,
) -> None:
    token = "registry-token-0123456789-abcdef-reader-01"
    registry = {
        "principals": [
            {
                "principal_id": "registry-reader",
                "tenant_id": "tenant-registry",
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "scopes": [JOBS_READ],
                "max_priority": 0,
            }
        ]
    }
    monkeypatch.delenv("BJ_PAL_CONTROL_TOKEN", raising=False)
    monkeypatch.setenv("BJ_PAL_CONTROL_PRINCIPALS_JSON", json.dumps(registry))
    configured = create_app(
        service=StubService(),
        readiness_probe=_ready,
        job_service=PlanningJobService(
            repository=PlanningJobRepository(tmp_path / "configured.db"),
            planning_service=StubService(),
        ),
    )
    with TestClient(configured) as client:
        allowed = client.get(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
    monkeypatch.setenv("BJ_PAL_CONTROL_PRINCIPALS_JSON", "{invalid-json")
    malformed = create_app(
        service=StubService(),
        readiness_probe=_ready,
        job_service=PlanningJobService(
            repository=PlanningJobRepository(tmp_path / "malformed.db"),
            planning_service=StubService(),
        ),
    )
    with TestClient(malformed) as client:
        unavailable = client.get(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert allowed.status_code == 200
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "control_plane_not_configured"
    assert token not in allowed.text + unavailable.text


def test_tenant_admission_limits_return_429_and_expose_scoped_append_only_audit(
    tmp_path,
) -> None:
    alpha_token = "alpha-admission-token-0123456789-abcdef-0001"
    beta_token = "beta-admission-token-0123456789-abcdef-00002"
    credentials = (
        _credential(
            token=alpha_token,
            principal_id="alpha-admission-admin",
            tenant_id="tenant-alpha",
            tenant_active_job_limit=1,
            tenant_submission_limit_per_minute=2,
        ),
        _credential(
            token=beta_token,
            principal_id="beta-admission-admin",
            tenant_id="tenant-beta",
            tenant_active_job_limit=1,
            tenant_submission_limit_per_minute=2,
        ),
    )
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "admission-http.db"),
        planning_service=StubService(),
    )
    app = create_app(
        service=StubService(),
        readiness_probe=_ready,
        job_service=jobs,
        control_credentials=credentials,
    )
    alpha_auth = {"Authorization": f"Bearer {alpha_token}"}
    beta_auth = {"Authorization": f"Bearer {beta_token}"}

    with TestClient(app) as client:
        first = client.post(
            "/v1/planning-jobs",
            headers={**alpha_auth, "Idempotency-Key": "alpha-admission-first"},
            json={"user_input": "第一个任务"},
        )
        reused = client.post(
            "/v1/planning-jobs",
            headers={**alpha_auth, "Idempotency-Key": "alpha-admission-first"},
            json={"user_input": "第一个任务"},
        )
        active_rejected = client.post(
            "/v1/planning-jobs",
            headers={**alpha_auth, "Idempotency-Key": "alpha-admission-second"},
            json={"user_input": "第二个任务"},
        )
        beta = client.post(
            "/v1/planning-jobs",
            headers={**beta_auth, "Idempotency-Key": "beta-admission-first"},
            json={"user_input": "乙租户任务"},
        )
        cancelled_first = client.post(
            f"/v1/planning-jobs/{first.json()['job_id']}/cancel",
            headers=alpha_auth,
            json={"reason_code": "operator_requested"},
        )
        second = client.post(
            "/v1/planning-jobs",
            headers={**alpha_auth, "Idempotency-Key": "alpha-admission-second"},
            json={"user_input": "第二个任务"},
        )
        client.post(
            f"/v1/planning-jobs/{second.json()['job_id']}/cancel",
            headers=alpha_auth,
            json={"reason_code": "operator_requested"},
        )
        rate_rejected = client.post(
            "/v1/planning-jobs",
            headers={**alpha_auth, "Idempotency-Key": "alpha-admission-third"},
            json={"user_input": "第三个任务"},
        )
        alpha_audit = client.get(
            "/v1/planning-admission-events",
            headers=alpha_auth,
        )
        beta_audit = client.get(
            "/v1/planning-admission-events",
            headers=beta_auth,
        )

    assert first.status_code == reused.status_code == beta.status_code == 202
    assert reused.json()["job_id"] == first.json()["job_id"]
    assert active_rejected.status_code == 429
    assert active_rejected.json()["error"]["code"] == (
        "tenant_active_job_limit_exceeded"
    )
    assert "retry-after" not in active_rejected.headers
    assert cancelled_first.status_code == 200
    assert second.status_code == 202
    assert rate_rejected.status_code == 429
    assert rate_rejected.json()["error"]["code"] == (
        "tenant_submission_rate_exceeded"
    )
    assert int(rate_rejected.headers["retry-after"]) >= 1
    assert [event["decision"] for event in alpha_audit.json()["events"]] == [
        "admitted",
        "idempotent_reuse",
        "rejected",
        "admitted",
        "rejected",
    ]
    assert [event["decision"] for event in beta_audit.json()["events"]] == [
        "admitted"
    ]
    serialized = active_rejected.text + rate_rejected.text + alpha_audit.text
    assert alpha_token not in serialized and beta_token not in serialized


def test_side_effect_operation_requires_separate_approval_and_returns_receipt(
    tmp_path,
) -> None:
    requester_token = "operation-requester-token-0123456789-abcdef-001"
    approver_token = "operation-approver-token-0123456789-abcdef-002"
    outsider_token = "operation-outsider-token-0123456789-abcdef-0003"
    credentials = (
        _credential(
            token=requester_token,
            principal_id="agent-requester",
            tenant_id="tenant-alpha",
            scopes=frozenset(
                {OPERATIONS_REQUEST, OPERATIONS_READ, OPERATIONS_APPROVE}
            ),
        ),
        _credential(
            token=approver_token,
            principal_id="human-approver",
            tenant_id="tenant-alpha",
            scopes=frozenset({OPERATIONS_APPROVE, OPERATIONS_READ}),
        ),
        _credential(
            token=outsider_token,
            principal_id="beta-approver",
            tenant_id="tenant-beta",
            scopes=frozenset({OPERATIONS_APPROVE, OPERATIONS_READ}),
        ),
    )
    operation_service = SideEffectOperationService(
        repository=SideEffectOperationRepository(tmp_path / "operations-http.db")
    )
    app = create_app(
        service=StubService(),
        readiness_probe=_ready,
        operation_service=operation_service,
        control_credentials=credentials,
    )
    requester_auth = {"Authorization": f"Bearer {requester_token}"}
    approver_auth = {"Authorization": f"Bearer {approver_token}"}
    outsider_auth = {"Authorization": f"Bearer {outsider_token}"}
    operation_payload = _sandbox_operation_payload()

    with TestClient(app) as client:
        requested = client.post(
            "/v1/operations",
            headers={
                **requester_auth,
                "Idempotency-Key": "operation-http-key",
                "X-Request-ID": "operation-http-request",
            },
            json=operation_payload,
        )
        reused = client.post(
            "/v1/operations",
            headers={
                **requester_auth,
                "Idempotency-Key": "operation-http-key",
                "X-Request-ID": "operation-http-reuse",
            },
            json=operation_payload,
        )
        operation_id = requested.json()["operation_id"]
        approval_sha256 = requested.json()["approval_sha256"]
        self_approval = client.post(
            f"/v1/operations/{operation_id}/approve",
            headers=requester_auth,
            json={"expected_approval_sha256": approval_sha256},
        )
        foreign_approval = client.post(
            f"/v1/operations/{operation_id}/approve",
            headers=outsider_auth,
            json={"expected_approval_sha256": approval_sha256},
        )
        tampered_approval = client.post(
            f"/v1/operations/{operation_id}/approve",
            headers=approver_auth,
            json={"expected_approval_sha256": "0" * 64},
        )
        approved = client.post(
            f"/v1/operations/{operation_id}/approve",
            headers=approver_auth,
            json={"expected_approval_sha256": approval_sha256},
        )
        forbidden_request = client.post(
            "/v1/operations",
            headers={
                **approver_auth,
                "Idempotency-Key": "approver-cannot-request",
            },
            json=operation_payload,
        )
        foreign_read = client.get(
            f"/v1/operations/{operation_id}",
            headers=outsider_auth,
        )

    completed = operation_service.run_once(worker_id="sandbox-http-worker")
    with TestClient(app) as client:
        restored = client.get(
            f"/v1/operations/{operation_id}",
            headers=requester_auth,
        )
        events = client.get(
            f"/v1/operations/{operation_id}/events",
            headers=requester_auth,
        )

    assert requested.status_code == reused.status_code == 202
    assert reused.json()["operation_id"] == operation_id
    assert self_approval.status_code == 403
    assert self_approval.json()["error"]["code"] == (
        "operation_self_approval_forbidden"
    )
    assert foreign_approval.status_code == foreign_read.status_code == 404
    assert tampered_approval.status_code == 409
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert forbidden_request.status_code == 403
    assert completed is not None and completed.status == "succeeded"
    assert restored.status_code == 200
    assert restored.json()["receipt"]["sandbox"] is True
    assert restored.json()["receipt"]["request_sha256"] == (
        requested.json()["request_sha256"]
    )
    assert restored.json()["receipt_sha256"]
    assert [event["event_type"] for event in events.json()["events"]] == [
        "requested",
        "request_reused",
        "approved",
        "execution_started",
        "execution_succeeded",
    ]
    assert requester_token not in restored.text + events.text
    assert approver_token not in restored.text + events.text


def test_uncertain_operation_reconciliation_requires_scope_and_tenant(
    tmp_path,
) -> None:
    requester_token = "reconcile-requester-token-0123456789-abcdef-001"
    approver_token = "reconcile-approver-token-0123456789-abcdef-002"
    reconciler_token = "reconcile-worker-token-0123456789-abcdef-00003"
    outsider_token = "reconcile-outsider-token-0123456789-abcdef-0004"
    credentials = (
        _credential(
            token=requester_token,
            principal_id="agent-requester",
            tenant_id="tenant-alpha",
            scopes=frozenset({OPERATIONS_REQUEST, OPERATIONS_READ}),
        ),
        _credential(
            token=approver_token,
            principal_id="human-approver",
            tenant_id="tenant-alpha",
            scopes=frozenset({OPERATIONS_APPROVE, OPERATIONS_READ}),
        ),
        _credential(
            token=reconciler_token,
            principal_id="status-reconciler",
            tenant_id="tenant-alpha",
            scopes=frozenset({OPERATIONS_RECONCILE, OPERATIONS_READ}),
        ),
        _credential(
            token=outsider_token,
            principal_id="beta-reconciler",
            tenant_id="tenant-beta",
            scopes=frozenset({OPERATIONS_RECONCILE, OPERATIONS_READ}),
        ),
    )
    operation_service = SideEffectOperationService(
        repository=SideEffectOperationRepository(tmp_path / "reconcile-http.db"),
        provider=DeterministicSandboxBookingProvider(
            outcome="uncertain",
            lookup_outcome="confirmed",
        ),
    )
    app = create_app(
        service=StubService(),
        readiness_probe=_ready,
        operation_service=operation_service,
        control_credentials=credentials,
    )
    requester_auth = {"Authorization": f"Bearer {requester_token}"}
    approver_auth = {"Authorization": f"Bearer {approver_token}"}
    reconciler_auth = {"Authorization": f"Bearer {reconciler_token}"}
    outsider_auth = {"Authorization": f"Bearer {outsider_token}"}

    with TestClient(app) as client:
        requested = client.post(
            "/v1/operations",
            headers={
                **requester_auth,
                "Idempotency-Key": "operation-reconcile-http-key",
            },
            json=_sandbox_operation_payload(),
        )
        operation_id = requested.json()["operation_id"]
        client.post(
            f"/v1/operations/{operation_id}/approve",
            headers=approver_auth,
            json={
                "expected_approval_sha256": requested.json()["approval_sha256"]
            },
        )
    uncertain = operation_service.run_once(worker_id="uncertain-http-worker")
    assert uncertain is not None and uncertain.status == "uncertain"

    with TestClient(app) as client:
        missing_scope = client.post(
            f"/v1/operations/{operation_id}/reconcile",
            headers=approver_auth,
        )
        foreign = client.post(
            f"/v1/operations/{operation_id}/reconcile",
            headers=outsider_auth,
        )
        resolved = client.post(
            f"/v1/operations/{operation_id}/reconcile",
            headers=reconciler_auth,
        )
        evidence = client.get(
            f"/v1/operations/{operation_id}/reconciliations",
            headers=requester_auth,
        )
        repeated = client.post(
            f"/v1/operations/{operation_id}/reconcile",
            headers=reconciler_auth,
        )

    assert missing_scope.status_code == 403
    assert foreign.status_code == 404
    assert resolved.status_code == 200 and resolved.json()["status"] == "succeeded"
    assert resolved.json()["receipt"]["outcome"] == "confirmed"
    assert repeated.status_code == 409
    reconciliation = evidence.json()["reconciliations"][0]
    assert reconciliation["actor_id"] == "status-reconciler"
    assert reconciliation["outcome"] == "confirmed"
    assert reconciliation["receipt_sha256"] == resolved.json()["receipt_sha256"]
    assert requester_token not in evidence.text
    assert reconciler_token not in evidence.text


def test_inconsistent_admission_policy_within_one_tenant_fails_closed(tmp_path) -> None:
    first_token = "tenant-policy-first-0123456789-abcdef-0001"
    second_token = "tenant-policy-second-0123456789-abcdef-002"
    app = create_app(
        service=StubService(),
        readiness_probe=_ready,
        job_service=PlanningJobService(
            repository=PlanningJobRepository(tmp_path / "inconsistent-policy.db"),
            planning_service=StubService(),
        ),
        control_credentials=(
            _credential(
                token=first_token,
                principal_id="tenant-first",
                tenant_id="tenant-shared",
                tenant_active_job_limit=1,
                tenant_submission_limit_per_minute=10,
            ),
            _credential(
                token=second_token,
                principal_id="tenant-second",
                tenant_id="tenant-shared",
                tenant_active_job_limit=2,
                tenant_submission_limit_per_minute=10,
            ),
        ),
    )
    with TestClient(app) as client:
        response = client.get(
            "/v1/planning-jobs",
            headers={"Authorization": f"Bearer {first_token}"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "control_plane_not_configured"


def test_readiness_returns_503_without_runtime_data() -> None:
    app = create_app(service=StubService(), readiness_probe=lambda: _ready("not_ready"))
    with _client(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_plan_endpoint_maps_http_input_to_canonical_service() -> None:
    service = StubService()
    app = create_app(service=service, readiness_probe=_ready)
    with _client(app) as client:
        response = client.post(
            "/v1/plans",
            headers={"X-Request-ID": "test-plan"},
            json={
                "user_input": "  带娃吃饭，不吃辣  ",
                "persona": "family",
                "preferences": {
                    "party_size": 3,
                    "has_child": True,
                    "child_age": 5,
                    "diet_flags": ["no_spicy", "no_spicy"],
                    "target_start": "14:00",
                    "duration_hours": 4,
                },
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert response.headers["X-Request-ID"] == "test-plan"
    assert payload["request"]["user_input"] == "带娃吃饭，不吃辣"
    assert payload["request"]["preferences"]["diet_flags"] == ["no_spicy"]
    assert set(payload["request"]["provided_fields"]) >= {
        "user_input",
        "persona",
        "preferences",
        "preferences.party_size",
    }
    assert payload["requirements"]["status"] == "proceed_with_assumptions"
    assert payload["requirements"]["assumptions"][0]["code"] == "default_area_anchor"
    assert payload["final_plan"]["steps"][0]["poi_name"] == "替补餐厅"
    assert payload["final_plan"]["steps"][0]["confidence_source"] == "evidence_support_v1"
    assert payload["final_plan"]["route_context"] == {}
    assert payload["final_plan"]["schedule_context"] == {}
    assert payload["reroute_events"][0]["route_refresh"] == {}
    assert payload["reroute_events"][0]["schedule_refresh"] == {}
    assert payload["data_profile"]["classification"] == "synthetic"
    assert service.requests[0].preferences.raw_input == "带娃吃饭，不吃辣"


def test_requirement_clarification_is_structured_and_not_queued(tmp_path) -> None:
    jobs = PlanningJobService(repository=PlanningJobRepository(tmp_path / "jobs.db"))
    app = create_app(readiness_probe=_ready, job_service=jobs)

    ambiguous = {"user_input": "还是上次那个地方，下午安排一下"}
    with _client(app) as client:
        sync_response = client.post(
            "/v1/plans",
            headers={"X-Request-ID": "req-clarify-sync"},
            json=ambiguous,
        )
        job_response = client.post(
            "/v1/planning-jobs",
            headers={"X-Request-ID": "req-clarify-job"},
            json=ambiguous,
        )
        listed = client.get("/v1/planning-jobs")

    assert sync_response.status_code == job_response.status_code == 409
    for response, request_id in (
        (sync_response, "req-clarify-sync"),
        (job_response, "req-clarify-job"),
    ):
        error = response.json()["error"]
        assert error["code"] == "clarification_required"
        assert error["request_id"] == request_id
        decision = error["details"]["requirements"]
        assert decision["status"] == "clarification_required"
        assert decision["unresolved"][0]["code"] == "unresolved_location_reference"
        assert 2 <= len(decision["questions"][0]["options"]) <= 3
    assert listed.json()["jobs"] == []


def test_natural_language_constraints_reach_planner_and_http_artifact() -> None:
    captured = {}

    def planner(**kwargs):
        captured.update(kwargs)
        return Plan(
            persona=kwargs["persona"],
            area_anchor=kwargs["area_anchor"],
            steps=[
                Step(
                    step_index=1,
                    poi_name="测试点",
                    start_time=kwargs["prefs"].target_start,
                )
            ],
        )

    service = PlanningService(
        planner=planner,
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
    )
    app = create_app(service=service, readiness_probe=_ready)

    with _client(app) as client:
        response = client.post(
            "/v1/plans",
            json={
                "user_input": "周六下午三点，两个人在三里屯玩三小时，人均预算100元，不吃辣"
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["request"]["area_anchor"] == "三里屯片区"
    assert payload["request"]["preferences"] == {
        "party_size": 2,
        "has_child": False,
        "child_age": None,
        "diet_flags": ["no_spicy"],
        "walk_radius_km": 1.5,
        "budget_per_person": 100.0,
        "target_start": "15:00",
        "duration_hours": 3.0,
    }
    assert payload["final_plan"]["steps"][0]["start_time"] == "15:00"
    assert payload["constraints"]["version"] == "constraint_ledger_v1"
    assert payload["constraints"]["conflicts"] == []
    assert captured["prefs"].party_size == 2


def test_constraint_conflict_returns_409_before_planner_or_job_queue(tmp_path) -> None:
    calls = []
    service = PlanningService(
        planner=lambda **kwargs: calls.append(kwargs),
        prober=lambda plan, **kwargs: (plan, []),
    )
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=service,
    )
    app = create_app(service=service, readiness_probe=_ready, job_service=jobs)
    payload = {
        "user_input": "下午三点，两个人在三里屯玩三小时",
        "preferences": {
            "party_size": 4,
            "target_start": "15:00",
            "duration_hours": 3,
        },
    }

    with _client(app) as client:
        sync_response = client.post("/v1/plans", json=payload)
        job_response = client.post("/v1/planning-jobs", json=payload)
        listed = client.get("/v1/planning-jobs")

    assert sync_response.status_code == job_response.status_code == 409
    for response in (sync_response, job_response):
        details = response.json()["error"]["details"]
        assert details["requirements"]["unresolved"][0]["code"] == "constraint_conflict"
        assert details["constraints"]["conflicts"][0] == {
            "field": "preferences.party_size",
            "structured_value": 4,
            "text_value": 2,
            "evidence": "两个人",
            "reason": "自然语言与调用方显式字段不一致，不能静默选择其一。",
        }
    assert calls == []
    assert listed.json()["jobs"] == []


def test_sync_clarification_continuation_is_resumable_and_result_idempotent(
    tmp_path,
) -> None:
    planner_calls = []

    def planner(**kwargs):
        planner_calls.append(kwargs)
        return Plan(
            persona=kwargs["persona"],
            area_anchor=kwargs["area_anchor"],
            steps=[
                Step(
                    step_index=1,
                    poi_name="续接测试点",
                    start_time=kwargs["prefs"].target_start,
                )
            ],
        )

    service = PlanningService(
        planner=planner,
        prober=lambda plan, **kwargs: (plan, []),
        profile_loader=_profile,
        plan_recorder=lambda plan: None,
    )
    continuation_service = ClarificationContinuationService(
        repository=ClarificationRepository(tmp_path / "clarifications.db")
    )
    app = create_app(
        service=service,
        readiness_probe=_ready,
        clarification_service=continuation_service,
    )
    request_payload = {
        "user_input": "下午三点，两个人在三里屯玩三小时",
        "preferences": {
            "party_size": 4,
            "target_start": "15:00",
            "duration_hours": 3,
        },
    }

    with _client(app) as client:
        initial = client.post("/v1/plans", json=request_payload)
        continuation = initial.json()["error"]["details"]["continuation"]
        continued = client.post(
            continuation["continue_url"],
            json={"option_id": "use_text_value"},
        )
        repeated = client.post(
            continuation["continue_url"],
            json={"option_id": "use_text_value"},
        )
        conflicting = client.post(
            continuation["continue_url"],
            json={"option_id": "use_structured_value"},
        )

    assert initial.status_code == 409
    assert continuation["version"] == "clarification_continuation_v1"
    assert continuation["delivery"] == "sync"
    assert [item["option_id"] for item in continuation["options"]] == [
        "use_text_value",
        "use_structured_value",
    ]
    assert continued.status_code == repeated.status_code == 200
    assert continued.json()["request"]["preferences"]["party_size"] == 2
    assert continued.json()["request"]["resolutions"][0]["option_id"] == "use_text_value"
    resolved_entry = next(
        item
        for item in continued.json()["constraints"]["entries"]
        if item["field"] == "preferences.party_size"
    )
    assert resolved_entry["source"] == "user_clarification"
    assert resolved_entry["outcome"] == "resolved"
    assert repeated.json()["final_plan"]["plan_id"] == continued.json()["final_plan"]["plan_id"]
    assert len(planner_calls) == 1
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "clarification_resolution_conflict"


def test_multi_step_clarification_replays_the_same_next_continuation(tmp_path) -> None:
    planner_calls = []
    service = PlanningService(
        planner=lambda **kwargs: planner_calls.append(kwargs),
        prober=lambda plan, **kwargs: (plan, []),
    )
    continuation_service = ClarificationContinuationService(
        repository=ClarificationRepository(tmp_path / "clarifications.db")
    )
    app = create_app(
        service=service,
        readiness_probe=_ready,
        clarification_service=continuation_service,
    )
    request_payload = {
        "user_input": "下午三点，两个人在三里屯玩三小时",
        "preferences": {
            "party_size": 4,
            "target_start": "16:00",
            "duration_hours": 3,
        },
    }

    with _client(app) as client:
        initial = client.post("/v1/plans", json=request_payload)
        first = initial.json()["error"]["details"]["continuation"]
        next_response = client.post(
            first["continue_url"],
            json={"option_id": "use_text_value"},
        )
        repeated = client.post(
            first["continue_url"],
            json={"option_id": "use_text_value"},
        )

    assert initial.status_code == next_response.status_code == repeated.status_code == 409
    next_details = next_response.json()["error"]["details"]
    repeated_details = repeated.json()["error"]["details"]
    assert next_details == repeated_details
    assert next_details["requirements"]["unresolved"][0]["field"] == (
        "preferences.target_start"
    )
    assert next_details["continuation"]["continuation_id"] != first["continuation_id"]
    assert planner_calls == []


def test_clarification_continuation_maps_not_found_expired_and_invalid_option(
    tmp_path,
) -> None:
    repository = ClarificationRepository(tmp_path / "clarifications.db")
    continuation_service = ClarificationContinuationService(repository=repository)
    app = create_app(
        service=PlanningService(),
        readiness_probe=_ready,
        clarification_service=continuation_service,
    )
    request_payload = {
        "user_input": "下午三点，两个人在三里屯玩三小时",
        "preferences": {
            "party_size": 4,
            "target_start": "15:00",
            "duration_hours": 3,
        },
    }

    with _client(app) as client:
        missing = client.post(
            "/v1/clarifications/clar-00000000000000000000000000000000/plan",
            json={"option_id": "use_text_value"},
        )
        initial = client.post("/v1/plans", json=request_payload)
        continuation = initial.json()["error"]["details"]["continuation"]
        invalid = client.post(
            continuation["continue_url"],
            json={"option_id": "not_an_offered_option"},
        )
        with repository._connect() as connection:
            connection.execute(
                "UPDATE clarification_sessions SET expires_at = ? WHERE continuation_id = ?",
                ("2000-01-01T00:00:00.000Z", continuation["continuation_id"]),
            )
        expired = client.post(
            continuation["continue_url"],
            json={"option_id": "use_text_value"},
        )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "clarification_not_found"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_clarification_resolution"
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "clarification_expired"


def test_corrupt_clarification_artifact_returns_sanitized_500(tmp_path) -> None:
    repository = ClarificationRepository(tmp_path / "clarifications.db")
    app = create_app(
        service=PlanningService(),
        readiness_probe=_ready,
        clarification_service=ClarificationContinuationService(repository=repository),
    )
    request_payload = {
        "user_input": "下午三点，两个人在三里屯玩三小时",
        "preferences": {
            "party_size": 4,
            "target_start": "15:00",
            "duration_hours": 3,
        },
    }
    with _client(app) as client:
        initial = client.post("/v1/plans", json=request_payload)
        continuation = initial.json()["error"]["details"]["continuation"]
        with repository._connect() as connection:
            connection.execute(
                "UPDATE clarification_sessions SET options_json = ? WHERE continuation_id = ?",
                ("not-json-secret-store-detail", continuation["continuation_id"]),
            )
        corrupt = client.post(
            continuation["continue_url"],
            json={"option_id": "use_text_value"},
        )

    assert corrupt.status_code == 500
    assert corrupt.json()["error"]["code"] == "invalid_clarification_artifact"
    assert "secret-store-detail" not in corrupt.text


def test_job_clarification_continuation_enqueues_exactly_one_job(tmp_path) -> None:
    backend = StubService()
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=backend,
    )
    continuation_service = ClarificationContinuationService(
        repository=ClarificationRepository(tmp_path / "clarifications.db")
    )
    app = create_app(
        service=backend,
        readiness_probe=_ready,
        job_service=jobs,
        clarification_service=continuation_service,
    )
    request_payload = {
        "user_input": "下午三点，两个人在三里屯玩三小时",
        "preferences": {
            "party_size": 4,
            "target_start": "15:00",
            "duration_hours": 3,
        },
        "deadline_seconds": 45,
        "priority": 7,
    }

    with _client(app) as client:
        initial = client.post("/v1/planning-jobs", json=request_payload)
        continuation = initial.json()["error"]["details"]["continuation"]
        continued = client.post(
            continuation["continue_url"],
            json={"option_id": "use_structured_value"},
        )
        repeated = client.post(
            continuation["continue_url"],
            json={"option_id": "use_structured_value"},
        )
        listed = client.get("/v1/planning-jobs")

    assert initial.status_code == 409
    assert continuation["delivery"] == "job"
    assert continued.status_code == repeated.status_code == 202
    assert continued.json()["job_id"] == repeated.json()["job_id"]
    assert continued.json()["deadline_seconds"] == 45
    assert continued.json()["priority"] == 7
    assert len(listed.json()["jobs"]) == 1
    assert listed.json()["jobs"][0]["priority"] == 7
    queued = jobs.get(continued.json()["job_id"])
    assert queued is not None and queued.priority == 7
    application_request = PlanRequest.from_dict(queued.request_payload)
    assert application_request.preferences.party_size == 4
    assert application_request.resolutions[0].option_id == "use_structured_value"


def test_v53_artifact_without_requirement_fields_remains_readable() -> None:
    request = PlanCreateRequest(user_input="下午出去玩").to_application_request()
    payload = StubService().execute(request).to_dict()
    payload.pop("requirements")
    payload.pop("constraints")
    payload["request"].pop("provided_fields")

    restored = PlanCreateResponse.model_validate(payload)

    assert restored.requirements is None
    assert restored.constraints is None
    assert restored.request.provided_fields == []


def test_invalid_input_is_rejected_before_service_execution() -> None:
    service = StubService()
    app = create_app(service=service, readiness_probe=_ready)
    with _client(app) as client:
        response = client.post(
            "/v1/plans",
            json={
                "user_input": "test",
                "preferences": {"party_size": 0, "target_start": "25:90"},
                "unknown_field": True,
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["error"]["request_id"].startswith("req-")
    assert service.requests == []


def test_backend_errors_are_sanitized() -> None:
    service = StubService(error=RuntimeError("secret-key-value"))
    app = create_app(service=service, readiness_probe=_ready)
    with _client(app) as client:
        response = client.post(
            "/v1/plans",
            headers={"X-Request-ID": "test-failure"},
            json={"user_input": "下午出去玩"},
        )
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "planning_unavailable",
            "message": "The planning service is temporarily unavailable.",
            "request_id": "test-failure",
        }
    }
    assert "secret-key-value" not in response.text


def test_invalid_model_output_returns_privacy_minimized_502() -> None:
    snapshot = ModelOutputContractSnapshot.create(
        status="rejected",
        attempt_count=2,
        repair_attempted=True,
        candidate_count=12,
        issue_codes=("candidate_id_not_allowed", "schema_extra_field"),
    )
    service = StubService(error=ModelOutputContractError(snapshot))
    app = create_app(service=service, readiness_probe=_ready)
    with _client(app) as client:
        response = client.post(
            "/v1/plans",
            headers={"X-Request-ID": "test-model-output-failure"},
            json={"user_input": "PRIVATE-MODEL-OUTPUT-MARKER"},
        )

    assert response.status_code == 502
    payload = response.json()["error"]
    assert payload["code"] == "invalid_model_output"
    assert payload["request_id"] == "test-model-output-failure"
    assert payload["details"] == snapshot.to_dict()
    assert "PRIVATE-MODEL-OUTPUT-MARKER" not in json.dumps(payload["details"])


def test_model_output_response_context_rejects_tampered_integrity() -> None:
    snapshot = ModelOutputContractSnapshot.create(
        status="accepted",
        attempt_count=1,
        repair_attempted=False,
        candidate_count=12,
    ).to_dict()
    snapshot["candidate_count"] = 13

    with pytest.raises(ValueError, match="integrity mismatch"):
        ModelOutputContextResponse.model_validate(snapshot)


def test_openapi_exposes_versioned_contracts() -> None:
    app = create_app(service=StubService(), readiness_probe=_ready)
    schema = app.openapi()
    assert schema["info"]["version"] == SERVICE_VERSION
    assert {
        "/healthz",
        "/readyz",
        "/v1/plans",
        "/v1/clarifications/{continuation_id}/plan",
        "/v1/planning-jobs",
        "/v1/planning-admission-events",
        "/v1/clarifications/{continuation_id}/planning-job",
        "/v1/planning-jobs/{job_id}",
        "/v1/planning-jobs/{job_id}/cancel",
        "/v1/planning-jobs/{job_id}/replay",
        "/v1/planning-jobs/{job_id}/events",
        "/v1/planning-jobs/{job_id}/events/stream",
        "/v1/operations",
        "/v1/operations/{operation_id}",
        "/v1/operations/{operation_id}/approve",
        "/v1/operations/{operation_id}/deny",
        "/v1/operations/{operation_id}/events",
        "/v1/operations/{operation_id}/reconcile",
        "/v1/operations/{operation_id}/reconciliations",
    } <= set(schema["paths"])
    plan_operation = schema["paths"]["/v1/plans"]["post"]
    assert plan_operation["requestBody"]["required"] is True
    assert "200" in plan_operation["responses"]
    assert "409" in plan_operation["responses"]
    plan_schema = plan_operation["requestBody"]["content"]["application/json"]["schema"]
    job_schema = schema["paths"]["/v1/planning-jobs"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    assert plan_schema["$ref"].endswith("/PlanCreateRequest")
    assert job_schema["$ref"].endswith("/PlanningJobSubmitRequest")
    assert "429" in schema["paths"]["/v1/planning-jobs"]["post"]["responses"]
    assert "429" in schema["paths"][
        "/v1/clarifications/{continuation_id}/planning-job"
    ]["post"]["responses"]
    assert "429" in schema["paths"][
        "/v1/planning-jobs/{job_id}/replay"
    ]["post"]["responses"]
    assert schema["components"]["securitySchemes"]["BJPalControlBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/v1/planning-jobs"]["get"]["security"] == [
        {"BJPalControlBearer": []}
    ]
    assert schema["paths"]["/v1/planning-admission-events"]["get"]["security"] == [
        {"BJPalControlBearer": []}
    ]
    assert schema["paths"]["/v1/operations"]["post"]["security"] == [
        {"BJPalControlBearer": []}
    ]
    assert schema["paths"][
        "/v1/operations/{operation_id}/approve"
    ]["post"]["security"] == [{"BJPalControlBearer": []}]
    assert schema["paths"][
        "/v1/operations/{operation_id}/reconcile"
    ]["post"]["security"] == [{"BJPalControlBearer": []}]
    assert schema["paths"][
        "/v1/clarifications/{continuation_id}/planning-job"
    ]["post"]["security"] == [{"BJPalControlBearer": []}]
    assert "security" not in schema["paths"][
        "/v1/clarifications/{continuation_id}/plan"
    ]["post"]
    assert "security" not in schema["paths"]["/healthz"]["get"]


def test_default_api_runs_public_offline_planning(monkeypatch) -> None:
    monkeypatch.setenv("BJ_PAL_LLM", "mock")
    app = create_app()
    with _client(app) as client:
        response = client.post(
            "/v1/plans",
            json={
                "user_input": "周末下午带娃在五道营附近玩四小时，不吃辣",
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
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["final_plan"]["steps"]) >= 3
    assert payload["data_profile"]["name"] == "demo"
    assert payload["data_profile"]["public_reproducible"] is True
    assert {item["domain"] for item in payload["final_plan"]["data_provenance"]} == {
        "poi",
        "ugc",
        "route",
        "weather",
    }
    assert all(item["bookable"] is False for item in payload["final_plan"]["data_provenance"])
    weather = next(
        item for item in payload["final_plan"]["data_provenance"] if item["domain"] == "weather"
    )
    assert weather["classification"] == "synthetic"
    assert payload["final_plan"]["weather_context"]["classification"] == "synthetic"
    assert payload["final_plan"]["route_context"]["version"] == "route_refresh_v1"
    assert payload["final_plan"]["route_context"]["scope"] == "full_plan"
    assert payload["final_plan"]["schedule_context"]["version"] == "schedule_reconcile_v1"
    assert payload["final_plan"]["schedule_context"]["overrun_minutes"] == 0
    execution = payload["execution"]
    assert execution["status"] == "succeeded"
    assert execution["correlation_id"] == response.headers["X-Request-ID"]
    assert execution["operation_counts"]["llm_call_count"] >= 1
    assert execution["token_usage"]["completeness"] == "unavailable"
    assert len(execution["artifact_sha256"]) == 64


def test_http_schema_can_read_pre_v57_persisted_result_without_observation() -> None:
    request = PlanCreateRequest(user_input="下午出去玩").to_application_request()
    legacy_payload = StubService().execute(request).to_dict()
    legacy_payload.pop("execution")

    restored = PlanCreateResponse.model_validate(legacy_payload)
    assert restored.execution is None


def test_durable_job_submit_worker_and_artifact_round_trip(tmp_path) -> None:
    backend = StubService()
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=backend,
    )
    app = create_app(service=backend, readiness_probe=_ready, job_service=jobs)
    with _client(app) as client:
        submitted = client.post(
            "/v1/planning-jobs",
            headers={"X-Request-ID": "req-job", "Idempotency-Key": "job-key-1"},
            json={"user_input": "下午出去玩", "deadline_seconds": 30, "priority": 7},
        )
        assert submitted.status_code == 202, submitted.text
        queued = submitted.json()
        assert queued["status"] == "queued"
        assert queued["request_id"] == "req-job"
        assert queued["deadline_seconds"] == 30
        assert queued["priority"] == 7
        assert queued["deadline_at"] is not None
        assert queued["result"] is None

        repeated = client.post(
            "/v1/planning-jobs",
            headers={"Idempotency-Key": "job-key-1"},
            json={"user_input": "下午出去玩", "deadline_seconds": 30, "priority": 7},
        )
        assert repeated.json()["job_id"] == queued["job_id"]
        changed_deadline = client.post(
            "/v1/planning-jobs",
            headers={"Idempotency-Key": "job-key-1"},
            json={"user_input": "下午出去玩", "deadline_seconds": 31, "priority": 7},
        )
        assert changed_deadline.status_code == 409
        changed_priority = client.post(
            "/v1/planning-jobs",
            headers={"Idempotency-Key": "job-key-1"},
            json={"user_input": "下午出去玩", "deadline_seconds": 30, "priority": 6},
        )
        assert changed_priority.status_code == 409
        listed = client.get("/v1/planning-jobs", params={"status": "queued"})
        assert listed.json()["jobs"][0]["priority"] == 7

        completed = jobs.run_once(worker_id="test-worker")
        assert completed is not None and completed.status == "succeeded"
        fetched = client.get(f"/v1/planning-jobs/{queued['job_id']}")
        events = client.get(f"/v1/planning-jobs/{queued['job_id']}/events")

    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()
    assert payload["status"] == "succeeded"
    assert payload["artifact_id"].startswith("artifact-")
    assert len(payload["artifact_sha256"]) == 64
    assert payload["result"]["final_plan"]["plan_id"] == "plan-api"
    assert payload["links"]["events"].endswith("/events")
    assert payload["links"]["event_stream"].endswith("/events/stream")
    assert events.status_code == 200, events.text
    event_payload = events.json()
    assert [event["event_type"] for event in event_payload["events"]] == [
        "submitted",
        "claimed",
        "succeeded",
    ]
    assert event_payload["events"][1]["worker_id"] == "test-worker"
    assert event_payload["events"][0]["payload"]["deadline_seconds"] == 30
    assert event_payload["events"][0]["payload"]["priority"] == 7
    assert event_payload["events"][1]["payload"]["scheduling_policy"] == (
        "tenant_fair_priority_aging_v2"
    )
    assert event_payload["events"][1]["payload"]["base_priority"] == 7
    cursor = event_payload["events"][0]["event_id"]
    with _client(app) as replay_client:
        replayed = replay_client.get(
            f"/v1/planning-jobs/{queued['job_id']}/events",
            params={"after_event_id": cursor, "limit": 1},
        )
    assert replayed.status_code == 200, replayed.text
    assert [event["event_type"] for event in replayed.json()["events"]] == ["claimed"]
    assert replayed.json()["next_after_event_id"] > cursor


def test_job_deadline_policy_is_separate_validated_and_exposed_as_terminal_state(
    tmp_path,
) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    jobs = PlanningJobService(repository=repository, planning_service=StubService())
    app = create_app(service=StubService(), readiness_probe=_ready, job_service=jobs)
    with _client(app) as client:
        invalid_low = client.post(
            "/v1/planning-jobs",
            json={"user_input": "下午出去玩", "deadline_seconds": 0},
        )
        invalid_high = client.post(
            "/v1/planning-jobs",
            json={"user_input": "下午出去玩", "deadline_seconds": 86401},
        )
        invalid_priority_low = client.post(
            "/v1/planning-jobs",
            json={"user_input": "下午出去玩", "priority": -1},
        )
        invalid_priority_high = client.post(
            "/v1/planning-jobs",
            json={"user_input": "下午出去玩", "priority": 10},
        )
        sync_rejects_policy = client.post(
            "/v1/plans",
            json={"user_input": "下午出去玩", "deadline_seconds": 30},
        )
        submitted = client.post(
            "/v1/planning-jobs",
            headers={"Idempotency-Key": "http-timeout-source"},
            json={"user_input": "下午出去玩", "deadline_seconds": 30},
        )
        job_id = submitted.json()["job_id"]
        with repository._connect() as connection:
            connection.execute(
                "UPDATE planning_jobs SET deadline_at = ? WHERE job_id = ?",
                ("2000-01-01T00:00:00.000Z", job_id),
            )
        assert repository.claim_next(worker_id="deadline-reaper") is None
        state = client.get(f"/v1/planning-jobs/{job_id}")
        timed_out_jobs = client.get("/v1/planning-jobs", params={"status": "timed_out"})
        events = client.get(f"/v1/planning-jobs/{job_id}/events")
        stream = client.get(f"/v1/planning-jobs/{job_id}/events/stream")
        replayed = client.post(
            f"/v1/planning-jobs/{job_id}/replay",
            headers={"Idempotency-Key": "http-timeout-replay"},
        )

    assert invalid_low.status_code == invalid_high.status_code == 422
    assert invalid_priority_low.status_code == invalid_priority_high.status_code == 422
    assert sync_rejects_policy.status_code == 422
    assert submitted.status_code == 202
    assert state.json()["status"] == "timed_out"
    assert state.json()["error_code"] == "job_deadline_exceeded"
    assert [item["job_id"] for item in timed_out_jobs.json()["jobs"]] == [job_id]
    assert events.json()["events"][-1]["event_type"] == "timed_out"
    assert "event: timed_out" in stream.text
    assert replayed.status_code == 202
    assert replayed.json()["replayed_from_job_id"] == job_id
    assert replayed.json()["deadline_seconds"] == 30
    assert replayed.json()["priority"] == 0


def test_durable_job_rejects_idempotency_conflict_and_unknown_job(tmp_path) -> None:
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubService(),
    )
    app = create_app(service=StubService(), readiness_probe=_ready, job_service=jobs)
    with _client(app) as client:
        first = client.post(
            "/v1/planning-jobs",
            headers={"Idempotency-Key": "conflict-key"},
            json={"user_input": "第一个请求"},
        )
        conflict = client.post(
            "/v1/planning-jobs",
            headers={"Idempotency-Key": "conflict-key"},
            json={"user_input": "不同的请求"},
        )
        missing = client.get("/v1/planning-jobs/job-00000000000000000000000000000000")
        missing_events = client.get(
            "/v1/planning-jobs/job-00000000000000000000000000000000/events"
        )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "job_not_found"
    assert missing_events.status_code == 404
    assert missing_events.json()["error"]["code"] == "job_not_found"


def test_sse_stream_projects_durable_events_and_resumes_from_last_event_id(tmp_path) -> None:
    backend = StubService()
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=backend,
    )
    submitted = jobs.submit(
        request=PlanCreateRequest(user_input="下午出去玩").to_application_request(),
        request_id="req-sse",
    )
    completed = jobs.run_once(worker_id="worker-sse")
    assert completed is not None and completed.status == "succeeded"
    persisted_events = jobs.events(submitted.job_id)
    claimed_event_id = persisted_events[1].event_id

    app = create_app(service=backend, readiness_probe=_ready, job_service=jobs)
    with _client(app) as client:
        full = client.get(
            f"/v1/planning-jobs/{submitted.job_id}/events/stream",
            headers={"X-Request-ID": "sse-full"},
        )
        resumed = client.get(
            f"/v1/planning-jobs/{submitted.job_id}/events/stream",
            headers={"Last-Event-ID": str(claimed_event_id)},
        )
        missing = client.get(
            "/v1/planning-jobs/job-00000000000000000000000000000000/events/stream"
        )

    assert full.status_code == 200
    assert full.headers["content-type"].startswith("text/event-stream")
    assert full.headers["cache-control"] == "no-cache"
    assert full.headers["X-Request-ID"] == "sse-full"
    assert [
        line.removeprefix("event: ")
        for line in full.text.splitlines()
        if line.startswith("event: ")
    ] == ["submitted", "claimed", "succeeded"]
    assert f"id: {claimed_event_id}" in full.text
    assert [
        line.removeprefix("event: ")
        for line in resumed.text.splitlines()
        if line.startswith("event: ")
    ] == ["succeeded"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "job_not_found"


def test_sse_query_cursor_overrides_header_and_validation_stays_structured(tmp_path) -> None:
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubService(),
    )
    submitted = jobs.submit(
        request=PlanCreateRequest(user_input="下午出去玩").to_application_request(),
        request_id="req-sse-cursor",
    )
    jobs.run_once(worker_id="worker-sse-cursor")
    events = jobs.events(submitted.job_id)
    app = create_app(service=StubService(), readiness_probe=_ready, job_service=jobs)

    with _client(app) as client:
        query_wins = client.get(
            f"/v1/planning-jobs/{submitted.job_id}/events/stream",
            params={"after_event_id": events[0].event_id},
            headers={"Last-Event-ID": str(events[-1].event_id)},
        )
        invalid = client.get(
            f"/v1/planning-jobs/{submitted.job_id}/events/stream",
            headers={"Last-Event-ID": "-1"},
        )

    assert [
        line.removeprefix("event: ")
        for line in query_wins.text.splitlines()
        if line.startswith("event: ")
    ] == ["claimed", "succeeded"]
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"


def test_dead_letter_state_and_event_are_exposed_without_internal_error_text(tmp_path) -> None:
    backend = StubService(error=RuntimeError("secret-provider-token"))
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=backend,
        max_attempts=1,
        retry_base_seconds=0,
    )
    app = create_app(service=backend, readiness_probe=_ready, job_service=jobs)
    with _client(app) as client:
        submitted = client.post(
            "/v1/planning-jobs",
            json={"user_input": "下午出去玩"},
        )
        job_id = submitted.json()["job_id"]
        completed = jobs.run_once(worker_id="worker-dead-letter")
        state = client.get(f"/v1/planning-jobs/{job_id}")
        events = client.get(f"/v1/planning-jobs/{job_id}/events")
        stream = client.get(f"/v1/planning-jobs/{job_id}/events/stream")

    assert completed is not None and completed.status == "dead_lettered"
    assert state.status_code == 200
    assert state.json()["status"] == "dead_lettered"
    assert state.json()["attempt"] == state.json()["max_attempts"] == 1
    assert state.json()["error_code"] == "planning_execution_failed"
    assert "secret-provider-token" not in state.text
    assert events.json()["events"][-1]["event_type"] == "dead_lettered"
    assert "event: dead_lettered" in stream.text
    assert "secret-provider-token" not in events.text + stream.text


def test_sse_open_job_returns_bounded_transport_timeout_not_fake_domain_event(tmp_path) -> None:
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubService(),
    )
    submitted = jobs.submit(
        request=PlanCreateRequest(user_input="下午出去玩").to_application_request(),
        request_id="req-open-stream",
    )
    submitted_event_id = jobs.events(submitted.job_id)[0].event_id
    app = create_app(service=StubService(), readiness_probe=_ready, job_service=jobs)
    with _client(app) as client:
        response = client.get(
            f"/v1/planning-jobs/{submitted.job_id}/events/stream",
            params={
                "after_event_id": submitted_event_id,
                "stream_seconds": 0.01,
                "poll_interval_ms": 10,
            },
        )

    assert response.status_code == 200
    assert response.text == f": stream-timeout cursor={submitted_event_id}\n\n"
    assert "event:" not in response.text


def test_job_list_and_queued_cancellation_are_exposed_as_durable_contracts(tmp_path) -> None:
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubService(),
    )
    app = create_app(service=StubService(), readiness_probe=_ready, job_service=jobs)
    with _client(app) as client:
        submitted = client.post(
            "/v1/planning-jobs",
            json={"user_input": "下午出去玩"},
        )
        job_id = submitted.json()["job_id"]
        queued = client.get("/v1/planning-jobs", params={"status": "queued"})
        cancelled = client.post(
            f"/v1/planning-jobs/{job_id}/cancel",
            json={"reason_code": "user_requested"},
        )
        repeated = client.post(
            f"/v1/planning-jobs/{job_id}/cancel",
            json={"reason_code": "user_requested"},
        )
        cancelled_list = client.get(
            "/v1/planning-jobs",
            params={"status": "cancelled"},
        )
        stream = client.get(f"/v1/planning-jobs/{job_id}/events/stream")
        bad_cursor = client.get(
            "/v1/planning-jobs",
            params={"after_job_id": "job-00000000000000000000000000000000"},
        )

    assert queued.status_code == 200
    assert [item["job_id"] for item in queued.json()["jobs"]] == [job_id]
    assert "result" not in queued.json()["jobs"][0]
    assert queued.json()["next_after_job_id"] == job_id
    assert cancelled.status_code == repeated.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_requested_at"] is not None
    assert cancelled.json()["cancelled_at"] is not None
    assert cancelled.json()["cancel_reason_code"] == "user_requested"
    assert [item["job_id"] for item in cancelled_list.json()["jobs"]] == [job_id]
    assert "event: cancelled" in stream.text
    assert bad_cursor.status_code == 404
    assert bad_cursor.json()["error"]["code"] == "job_cursor_not_found"


def test_dead_letter_can_be_listed_and_idempotently_replayed_over_http(tmp_path) -> None:
    backend = StubService(error=RuntimeError("secret-provider-token"))
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=backend,
        max_attempts=1,
        retry_base_seconds=0,
    )
    app = create_app(service=backend, readiness_probe=_ready, job_service=jobs)
    with _client(app) as client:
        submitted = client.post(
            "/v1/planning-jobs",
            json={"user_input": "下午出去玩"},
        )
        source_job_id = submitted.json()["job_id"]
        terminal = jobs.run_once(worker_id="worker-replay-source")
        dead_letters = client.get(
            "/v1/planning-jobs",
            params={"status": "dead_lettered"},
        )
        terminal_cancel = client.post(
            f"/v1/planning-jobs/{source_job_id}/cancel",
            json={"reason_code": "operator_requested"},
        )
        missing_key = client.post(f"/v1/planning-jobs/{source_job_id}/replay")
        replayed = client.post(
            f"/v1/planning-jobs/{source_job_id}/replay",
            headers={"Idempotency-Key": "manual-replay-1", "X-Request-ID": "req-replay"},
        )
        repeated = client.post(
            f"/v1/planning-jobs/{source_job_id}/replay",
            headers={"Idempotency-Key": "manual-replay-1"},
        )
        replay_conflict = client.post(
            f"/v1/planning-jobs/{replayed.json()['job_id']}/replay",
            headers={"Idempotency-Key": "manual-replay-2"},
        )
        source_events = client.get(f"/v1/planning-jobs/{source_job_id}/events")

    assert terminal is not None and terminal.status == "dead_lettered"
    assert [item["job_id"] for item in dead_letters.json()["jobs"]] == [source_job_id]
    assert terminal_cancel.status_code == 409
    assert terminal_cancel.json()["error"]["code"] == "invalid_job_transition"
    assert missing_key.status_code == 422
    assert missing_key.json()["error"]["code"] == "invalid_request"
    assert replayed.status_code == repeated.status_code == 202
    assert replayed.json()["job_id"] == repeated.json()["job_id"]
    assert replayed.json()["status"] == "queued"
    assert replayed.json()["request_id"] == "req-replay"
    assert replayed.json()["replayed_from_job_id"] == source_job_id
    assert replay_conflict.status_code == 409
    assert replay_conflict.json()["error"]["code"] == "invalid_job_transition"
    assert source_events.json()["events"][-1]["event_type"] == "replay_requested"
    assert "secret-provider-token" not in dead_letters.text + source_events.text


def test_job_store_sqlite_errors_are_mapped_and_stream_reconnects(tmp_path) -> None:
    jobs = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubService(),
    )
    submitted = jobs.submit(
        request=PlanCreateRequest(user_input="下午出去玩").to_application_request(),
        request_id="req-store-error",
    )
    original_get = jobs.get
    get_calls = 0

    def fail_after_initial_lookup(job_id: str, **kwargs):
        nonlocal get_calls
        get_calls += 1
        if get_calls > 1:
            raise sqlite3.OperationalError("secret-store-detail")
        return original_get(job_id, **kwargs)

    jobs.get = fail_after_initial_lookup  # type: ignore[method-assign]
    app = create_app(service=StubService(), readiness_probe=_ready, job_service=jobs)
    with _client(app) as client:
        stream = client.get(
            f"/v1/planning-jobs/{submitted.job_id}/events/stream",
            params={"stream_seconds": 0.01, "poll_interval_ms": 10},
        )

    assert stream.status_code == 200
    assert "event: submitted" in stream.text
    assert ": stream-error cursor=" in stream.text
    assert "secret-store-detail" not in stream.text
