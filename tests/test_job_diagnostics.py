from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs.diagnostics import (  # noqa: E402
    JobDiagnosticEventLimitExceeded,
    JobIncidentDiagnosis,
)
from jobs.models import PlanningJob, PlanningJobEvent  # noqa: E402
from jobs.repository import PlanningJobRepository  # noqa: E402
from jobs.service import PlanningJobService  # noqa: E402


def _job(*, status: str, error_code: str | None = None, attempt: int = 1) -> PlanningJob:
    return PlanningJob(
        job_id="job-" + "a" * 32,
        request_id="req-private-marker",
        tenant_id="tenant-private-marker",
        submitted_by="principal-private-marker",
        status=status,
        request_payload={"user_input": "private-user-input"},
        request_sha256="b" * 64,
        idempotency_key=None,
        attempt=attempt,
        max_attempts=3,
        priority=0,
        deadline_seconds=900,
        deadline_at="2026-07-20T00:15:00.000Z",
        available_at="2026-07-20T00:00:00.000Z",
        created_at="2026-07-20T00:00:00.000Z",
        updated_at="2026-07-20T00:00:03.000Z",
        error_code=error_code,
        error_message="private-provider-error-marker",
    )


def _event(
    event_id: int,
    event_type: str,
    *,
    attempt: int = 1,
    seconds: int | None = None,
    error_code: str | None = None,
) -> PlanningJobEvent:
    payload = {"private": "private-event-marker"}
    if error_code is not None:
        payload["error_code"] = error_code
    return PlanningJobEvent(
        event_id=event_id,
        job_id="job-" + "a" * 32,
        event_type=event_type,
        attempt=attempt,
        worker_id="worker-private-marker",
        payload=payload,
        created_at=f"2026-07-20T00:00:{seconds if seconds is not None else event_id:02d}.000Z",
    )


@pytest.mark.parametrize(
    ("status", "error_code", "event_types", "classification", "action"),
    [
        ("succeeded", None, ("submitted", "claimed", "succeeded"), "completed", "none"),
        (
            "failed",
            "invalid_persisted_request",
            ("submitted", "claimed", "failed"),
            "persisted_request_invalid",
            "inspect_persisted_request_migration",
        ),
        (
            "failed",
            "clarification_required",
            ("submitted", "claimed", "failed"),
            "clarification_required",
            "resubmit_with_clarification",
        ),
        (
            "failed",
            "execution_budget_exceeded",
            ("submitted", "claimed", "failed"),
            "execution_budget_exceeded",
            "reduce_work_or_adjust_server_budget",
        ),
        (
            "failed",
            "invalid_model_output",
            ("submitted", "claimed", "failed"),
            "model_output_rejected",
            "inspect_model_output_contract_cases",
        ),
        (
            "dead_lettered",
            "planning_execution_failed",
            (
                "submitted",
                "claimed",
                "retry_scheduled",
                "claimed",
                "dead_lettered",
            ),
            "runtime_or_dependency_unknown",
            "inspect_dependency_health_before_replay",
        ),
        (
            "dead_lettered",
            "lease_expired_attempts_exhausted",
            ("submitted", "claimed", "lease_reclaimed", "dead_lettered"),
            "worker_lease_exhausted",
            "inspect_worker_health_before_replay",
        ),
        ("cancelled", None, ("submitted", "cancelled"), "cancelled", "none"),
    ],
)
def test_terminal_failure_signatures_are_deterministic_and_privacy_minimized(
    status: str,
    error_code: str | None,
    event_types: tuple[str, ...],
    classification: str,
    action: str,
) -> None:
    events = tuple(
        _event(
            index,
            event_type,
            attempt=1 if index < 4 else 2,
            error_code=(error_code if event_type in {"failed", "dead_lettered"} else None),
        )
        for index, event_type in enumerate(event_types, start=1)
    )

    diagnosis = JobIncidentDiagnosis.create(
        job=_job(status=status, error_code=error_code, attempt=max(item.attempt for item in events)),
        events=events,
    )
    serialized = json.dumps(diagnosis.to_dict(), ensure_ascii=False)

    assert diagnosis.classification == classification
    assert diagnosis.recommended_action == action
    assert diagnosis.verify_integrity()
    assert diagnosis.replay_allowed is (status in {"failed", "dead_lettered", "timed_out"})
    assert "private" not in serialized


def test_deadline_phase_uses_claim_evidence_instead_of_guessing() -> None:
    queued = JobIncidentDiagnosis.create(
        job=_job(status="timed_out", error_code="job_deadline_exceeded", attempt=0),
        events=(
            _event(1, "submitted", attempt=0),
            _event(2, "timed_out", attempt=0, error_code="job_deadline_exceeded"),
        ),
    )
    running = JobIncidentDiagnosis.create(
        job=_job(status="timed_out", error_code="job_deadline_exceeded"),
        events=(
            _event(1, "submitted", attempt=0),
            _event(2, "claimed"),
            _event(3, "heartbeat"),
            _event(4, "timed_out", error_code="job_deadline_exceeded"),
        ),
    )

    assert queued.classification == "queue_deadline_exceeded"
    assert queued.queue_wait_ms is None
    assert running.classification == "execution_deadline_exceeded"
    assert running.queue_wait_ms == 1000.0
    assert running.heartbeat_count == 1
    assert all(item.event_type != "heartbeat" for item in running.significant_events)


def test_active_retry_and_lease_recovery_remain_non_terminal() -> None:
    retry_pending = JobIncidentDiagnosis.create(
        job=_job(status="queued", error_code="planning_execution_failed"),
        events=(
            _event(1, "submitted", attempt=0),
            _event(2, "claimed"),
            _event(3, "retry_scheduled", error_code="planning_execution_failed"),
        ),
    )
    lease_recovery = JobIncidentDiagnosis.create(
        job=_job(status="running", error_code=None, attempt=2),
        events=(
            _event(1, "submitted", attempt=0),
            _event(2, "claimed"),
            _event(3, "lease_reclaimed", attempt=2),
        ),
    )

    assert retry_pending.classification == "retry_pending"
    assert retry_pending.recommended_action == "wait_for_scheduled_retry"
    assert not retry_pending.replay_allowed
    assert lease_recovery.classification == "lease_recovery_in_progress"


def test_lease_evidence_takes_precedence_over_generic_runtime_error() -> None:
    diagnosis = JobIncidentDiagnosis.create(
        job=_job(
            status="dead_lettered",
            error_code="planning_execution_failed",
            attempt=2,
        ),
        events=(
            _event(1, "submitted", attempt=0),
            _event(2, "claimed"),
            _event(3, "lease_reclaimed", attempt=2),
            _event(
                4,
                "dead_lettered",
                attempt=2,
                error_code="planning_execution_failed",
            ),
        ),
    )

    assert diagnosis.classification == "worker_lease_exhausted"
    assert diagnosis.recommended_action == "inspect_worker_health_before_replay"


def test_unknown_error_code_is_not_promoted_to_a_root_cause() -> None:
    diagnosis = JobIncidentDiagnosis.create(
        job=_job(status="failed", error_code="provider-secret-hostname"),
        events=(
            _event(1, "submitted", attempt=0),
            _event(2, "claimed"),
            _event(3, "failed", error_code="provider-secret-hostname"),
        ),
    )

    assert diagnosis.classification == "unclassified_failure"
    assert diagnosis.observed_error_code == "unclassified_error"
    assert "provider-secret-hostname" not in json.dumps(diagnosis.to_dict())


def test_non_string_error_code_is_redacted_instead_of_crashing() -> None:
    events = (
        _event(1, "submitted", attempt=0),
        _event(2, "claimed"),
        replace(_event(3, "failed"), payload={"error_code": {"private": "value"}}),
    )

    diagnosis = JobIncidentDiagnosis.create(
        job=replace(_job(status="failed"), error_code={"private": "value"}),
        events=events,
    )

    assert diagnosis.classification == "unclassified_failure"
    assert diagnosis.observed_error_code == "unclassified_error"
    assert diagnosis.significant_events[-1].error_code == "unclassified_error"
    assert "private" not in json.dumps(diagnosis.to_dict())


def test_diagnosis_integrity_and_event_chain_validation_fail_closed() -> None:
    job = _job(status="succeeded")
    events = (
        _event(1, "submitted", attempt=0),
        _event(2, "claimed"),
        _event(3, "succeeded"),
    )
    diagnosis = JobIncidentDiagnosis.create(job=job, events=events)
    tampered = diagnosis.to_dict()
    tampered["classification"] = "runtime_or_dependency_unknown"

    with pytest.raises(ValueError, match="integrity"):
        JobIncidentDiagnosis.from_dict(tampered)
    with pytest.raises(ValueError, match="strictly increasing"):
        JobIncidentDiagnosis.create(job=job, events=(events[0], replace(events[1], event_id=1)))
    with pytest.raises(ValueError, match="matching terminal event"):
        JobIncidentDiagnosis.create(job=job, events=events[:2])
    with pytest.raises(ValueError, match="status is invalid"):
        JobIncidentDiagnosis.create(job=replace(job, status="private-status"), events=events)
    with pytest.raises(ValueError, match="event type is invalid"):
        JobIncidentDiagnosis.create(
            job=job,
            events=(
                events[0],
                replace(events[1], event_type="private-event"),
                events[2],
            ),
        )


def test_service_reads_the_complete_tenant_scoped_event_chain(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    submitted = repository.submit(
        request_id="req-diagnosis-service",
        request_payload={"user_input": "private-service-input"},
        tenant_id="tenant-alpha",
        submitted_by="diagnostic-test",
        max_attempts=1,
    )
    repository.claim_next(worker_id="worker-diagnosis")
    repository.retry_or_dead_letter(
        job_id=submitted.job_id,
        worker_id="worker-diagnosis",
        error_code="planning_execution_failed",
        error_message="private-service-error",
        backoff_seconds=0,
    )
    service = PlanningJobService(repository=repository)

    diagnosis = service.diagnose(submitted.job_id, tenant_id="tenant-alpha")

    assert diagnosis is not None
    assert diagnosis.classification == "runtime_or_dependency_unknown"
    assert diagnosis.event_count == 3
    assert service.diagnose(submitted.job_id, tenant_id="tenant-beta") is None
    assert "private" not in json.dumps(diagnosis.to_dict())


def test_service_refuses_to_truncate_the_event_chain(monkeypatch, tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    submitted = repository.submit(
        request_id="req-diagnosis-limit",
        request_payload={"user_input": "bounded diagnostic"},
        tenant_id="tenant-alpha",
        submitted_by="diagnostic-test",
        max_attempts=1,
    )
    claimed = repository.claim_next(worker_id="worker-diagnosis")
    assert claimed is not None
    repository.heartbeat(
        job_id=submitted.job_id,
        worker_id="worker-diagnosis",
        lease_seconds=30,
    )
    monkeypatch.setattr("jobs.service.MAX_DIAGNOSTIC_EVENTS", 2)

    with pytest.raises(JobDiagnosticEventLimitExceeded, match="event limit"):
        PlanningJobService(repository=repository).diagnose(
            submitted.job_id,
            tenant_id="tenant-alpha",
        )
