from __future__ import annotations

import hashlib
import json
from pathlib import Path

from http_api.app import create_app
from http_api.routes.jobs import build_jobs_router
from http_api.routes.outcomes import build_outcomes_router
from http_api.routes.operations import build_operations_router
from http_api.routes.planning import PlanningRouteSupport, build_planning_router
from http_api.routes.system import build_system_router


ROOT = Path(__file__).resolve().parents[1]
FULL_OPENAPI_SHA256_WITHOUT_VERSION = (
    "e4f37b31d9464e7fa3af5d6c25e0322a2ce3ec13b077f502a06d4b3cedfd675d"
)
OPERATION_ROUTES = (
    ("POST", "/v1/operations", "request_side_effect_operation"),
    ("GET", "/v1/operations/{operation_id}", "get_side_effect_operation"),
    ("POST", "/v1/operations/{operation_id}/approve", "approve_side_effect_operation"),
    ("POST", "/v1/operations/{operation_id}/deny", "deny_side_effect_operation"),
    ("GET", "/v1/operations/{operation_id}/events", "get_side_effect_operation_events"),
    ("POST", "/v1/operations/{operation_id}/reconcile", "reconcile_side_effect_operation"),
    (
        "GET",
        "/v1/operations/{operation_id}/reconciliations",
        "get_side_effect_operation_reconciliations",
    ),
)
SYSTEM_ROUTES = (
    ("GET", "/healthz", "healthz"),
    ("GET", "/readyz", "readyz"),
    ("GET", "/v1/trace-export-status", "get_trace_export_status"),
    ("GET", "/v1/operational-alerts", "get_operational_alerts"),
)
OUTCOME_ROUTES = (
    ("POST", "/v1/trials", "create_trial"),
    ("GET", "/v1/trials/{trial_id}/notice", "get_trial_notice"),
    (
        "POST",
        "/v1/trials/{trial_id}/enrollment-invitations",
        "issue_trial_enrollment",
    ),
    ("POST", "/v1/trials/{trial_id}/participants", "enroll_trial_participant"),
    ("POST", "/v1/trials/{trial_id}/withdraw", "withdraw_trial_participant"),
    ("GET", "/v1/trials/{trial_id}/summary", "get_trial_summary"),
    ("POST", "/v1/trials/{trial_id}/close", "close_trial"),
    ("POST", "/v1/plans/{plan_id}/feedback", "submit_plan_feedback"),
    ("GET", "/v1/plans/{plan_id}/feedback", "get_plan_feedback"),
    ("GET", "/v1/feedback-summary", "get_feedback_summary"),
)
PLANNING_ROUTES = (
    ("POST", "/v1/plans", "create_plan"),
    ("POST", "/v1/clarifications/{continuation_id}/plan", "continue_plan"),
)
JOB_ROUTES = (
    ("POST", "/v1/planning-jobs", "submit_planning_job"),
    (
        "POST",
        "/v1/clarifications/{continuation_id}/planning-job",
        "continue_planning_job",
    ),
    ("GET", "/v1/planning-jobs", "list_planning_jobs"),
    ("GET", "/v1/planning-job-health", "get_planning_job_health"),
    ("GET", "/v1/planning-admission-events", "list_planning_admission_events"),
    ("POST", "/v1/planning-jobs/{job_id}/cancel", "cancel_planning_job"),
    ("POST", "/v1/planning-jobs/{job_id}/replay", "replay_planning_job"),
    ("GET", "/v1/planning-jobs/{job_id}", "get_planning_job"),
    ("GET", "/v1/planning-jobs/{job_id}/diagnosis", "diagnose_planning_job"),
    ("GET", "/v1/planning-jobs/{job_id}/events", "get_planning_job_events"),
    (
        "GET",
        "/v1/planning-jobs/{job_id}/events/stream",
        "stream_planning_job_events",
    ),
)


def _unconfigured_dependency():
    raise AssertionError("route contract inspection must not resolve dependencies")


def _canonical_sha256(value: dict) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _route_contract(router) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (next(iter(route.methods)), route.path, route.name)
        for route in router.routes
    )


def test_operations_are_owned_by_a_domain_router() -> None:
    router = build_operations_router(
        operations=_unconfigured_dependency,
        require_request_auth=_unconfigured_dependency,
        require_read_auth=_unconfigured_dependency,
        require_approve_auth=_unconfigured_dependency,
        require_reconcile_auth=_unconfigured_dependency,
    )

    assert _route_contract(router) == OPERATION_ROUTES

    composition_source = (ROOT / "src" / "http_api" / "app.py").read_text(
        encoding="utf-8"
    )
    assert '@application.post(\n        "/v1/operations"' not in composition_source
    assert "build_operations_router(" in composition_source


def test_system_and_outcome_routes_are_owned_by_domain_routers() -> None:
    system_router = build_system_router(
        readiness_probe=_unconfigured_dependency,
        jobs=_unconfigured_dependency,
        require_read_auth=_unconfigured_dependency,
    )
    outcome_router = build_outcomes_router(
        feedback=_unconfigured_dependency,
        require_trial_manage_auth=_unconfigured_dependency,
        require_trial_read_auth=_unconfigured_dependency,
    )

    assert _route_contract(system_router) == SYSTEM_ROUTES
    assert _route_contract(outcome_router) == OUTCOME_ROUTES

    composition_source = (ROOT / "src" / "http_api" / "app.py").read_text(
        encoding="utf-8"
    )
    assert '@application.get(\n        "/healthz"' not in composition_source
    assert '@application.post(\n        "/v1/trials"' not in composition_source
    assert "build_system_router(" in composition_source
    assert "build_outcomes_router(" in composition_source


def test_planning_and_job_routes_are_owned_by_domain_routers() -> None:
    support = PlanningRouteSupport(
        feedback=_unconfigured_dependency,
        clarifications=_unconfigured_dependency,
        public_demo=False,
    )
    planning_router = build_planning_router(
        planning_service=_unconfigured_dependency,
        support=support,
        public_demo=False,
    )
    jobs_router = build_jobs_router(
        jobs=_unconfigured_dependency,
        clarifications=support.clarifications,
        issue_clarification=support.issue_clarification,
        clarification_error_response=support.clarification_error_response,
        require_submit_auth=_unconfigured_dependency,
        require_read_auth=_unconfigured_dependency,
        require_control_auth=_unconfigured_dependency,
        require_replay_auth=_unconfigured_dependency,
    )

    assert _route_contract(planning_router) == PLANNING_ROUTES
    assert _route_contract(jobs_router) == JOB_ROUTES

    composition_source = (ROOT / "src" / "http_api" / "app.py").read_text(
        encoding="utf-8"
    )
    assert "@application.get(" not in composition_source
    assert "@application.post(" not in composition_source
    assert len(composition_source.splitlines()) <= 400


def test_full_openapi_contract_is_unchanged_by_router_extraction() -> None:
    document = create_app().openapi()
    document["info"].pop("version", None)

    assert _canonical_sha256(document) == FULL_OPENAPI_SHA256_WITHOUT_VERSION


def test_public_demo_keeps_only_the_bounded_planning_contract() -> None:
    assert set(create_app(public_demo=True).openapi()["paths"]) == {
        "/healthz",
        "/readyz",
        "/v1/plans",
    }
