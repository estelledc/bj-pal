from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.types import Plan, UserPreferences  # noqa: E402
from application import (  # noqa: E402
    ModelOutputContractError,
    ModelOutputContractSnapshot,
    PlanRequest,
    PlanResult,
    PlanningDeadlineExceeded,
    RequirementNormalizer,
)
from data_profile import DataProfile  # noqa: E402
from jobs import (  # noqa: E402
    IdempotencyConflict,
    InvalidJobTransition,
    JobNotFound,
    PlanningJobRepository,
    PlanningJobService,
    TenantAdmissionRejected,
)
from jobs import repository as job_repository_module  # noqa: E402


def _request(text: str = "下午出去玩") -> PlanRequest:
    return PlanRequest(
        user_input=text,
        preferences=UserPreferences(persona="family", raw_input=text),
    )


def _result(request: PlanRequest) -> PlanResult:
    plan = Plan(persona="family", area_anchor=request.area_anchor, steps=[], plan_id="plan-job")
    return PlanResult(
        request=request,
        initial_plan=plan,
        final_plan=plan,
        reroute_events=(),
        data_profile=DataProfile(
            name="demo",
            classification="synthetic",
            public_reproducible=True,
            sources={},
            counts={},
            limitations=("not live",),
        ),
        requirements=RequirementNormalizer().normalize(request),
    )


class StubPlanningService:
    def __init__(self, error: Exception | None = None, delay_seconds: float = 0) -> None:
        self.error = error
        self.delay_seconds = delay_seconds
        self.requests: list[PlanRequest] = []

    def execute(self, request: PlanRequest, **kwargs) -> PlanResult:
        del kwargs
        self.requests.append(request)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.error:
            raise self.error
        return _result(request)


class BlockingPlanningService:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, request: PlanRequest, **kwargs) -> PlanResult:
        del kwargs
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("blocking test timed out")
        return _result(request)


class DeadlineBoundaryPlanningService:
    def __init__(self, clock: list[datetime], expired_at: datetime) -> None:
        self.clock = clock
        self.expired_at = expired_at

    def execute(self, request: PlanRequest, **kwargs) -> PlanResult:
        callbacks = kwargs["callbacks"]
        self.clock[0] = self.expired_at
        assert callbacks.should_cancel() is False
        if callbacks.should_timeout():
            raise PlanningDeadlineExceeded("deadline observed by test backend")
        return _result(request)


def test_submit_is_idempotent_only_for_the_same_request(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    payload = _request().to_dict()
    first = repository.submit(
        request_id="req-1", request_payload=payload, idempotency_key="same-key"
    )
    repeated = repository.submit(
        request_id="req-2", request_payload=payload, idempotency_key="same-key"
    )

    assert repeated.job_id == first.job_id
    assert repeated.request_id == "req-1"
    events = repository.list_events(first.job_id)
    assert [event.event_type for event in events] == ["submitted"]
    assert events[0].payload == {
        "deadline_at": first.deadline_at,
        "deadline_seconds": 900,
        "idempotency_key_present": True,
        "max_attempts": 3,
        "priority": 0,
        "request_sha256": first.request_sha256,
        "submitted_by": "system",
        "tenant_id": "default",
    }
    with pytest.raises(IdempotencyConflict):
        repository.submit(
            request_id="req-3",
            request_payload=_request("不同请求").to_dict(),
            idempotency_key="same-key",
        )
    with pytest.raises(IdempotencyConflict):
        repository.submit(
            request_id="req-4",
            request_payload=payload,
            idempotency_key="same-key",
            deadline_seconds=30,
        )
    with pytest.raises(IdempotencyConflict):
        repository.submit(
            request_id="req-5",
            request_payload=payload,
            idempotency_key="same-key",
            priority=1,
        )
    with pytest.raises(ValueError, match="priority"):
        repository.submit(
            request_id="req-bool-priority",
            request_payload=payload,
            priority=True,
        )
    with pytest.raises(ValueError, match="priority"):
        repository.submit(
            request_id="req-float-priority",
            request_payload=payload,
            priority=1.5,
        )


def test_priority_scheduler_claims_highest_effective_priority_and_records_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    lower = repository.submit(
        request_id="req-lower",
        request_payload=_request("普通请求").to_dict(),
        priority=0,
    )
    clock[0] += timedelta(seconds=10)
    higher = repository.submit(
        request_id="req-higher",
        request_payload=_request("较高优先级请求").to_dict(),
        priority=5,
    )
    clock[0] += timedelta(seconds=1)

    claimed = repository.claim_next(worker_id="priority-worker", lease_seconds=30)

    assert claimed is not None and claimed.job_id == higher.job_id
    assert repository.get(lower.job_id).status == "queued"
    claim_event = repository.list_events(higher.job_id)[-1]
    assert claim_event.event_type == "claimed"
    assert claim_event.payload == {
        "base_priority": 5,
        "effective_priority": 5,
        "eligible_at": higher.available_at,
        "lease_expires_at": "2026-01-01T00:00:41.000Z",
        "max_attempts": 3,
        "priority_policy": "priority_aging_v1",
        "priority_aging_seconds": 60,
        "queue_wait_ms": 1000,
        "scheduling_policy": "tenant_fair_priority_aging_v2",
        "tenant_fairness_policy": "least_recently_served_tenant_v1",
        "tenant_id": "default",
        "tenant_last_claimed_event_id_before": 0,
    }


def test_tenant_scope_namespaces_idempotency_and_hides_foreign_jobs(
    tmp_path: Path,
) -> None:
    repository = PlanningJobRepository(tmp_path / "tenant-jobs.db")
    payload = _request().to_dict()
    alpha = repository.submit(
        request_id="req-alpha",
        request_payload=payload,
        tenant_id="tenant-alpha",
        submitted_by="alpha-submitter",
        idempotency_key="shared-key",
    )
    beta = repository.submit(
        request_id="req-beta",
        request_payload=payload,
        tenant_id="tenant-beta",
        submitted_by="beta-submitter",
        idempotency_key="shared-key",
    )

    assert alpha.job_id != beta.job_id
    assert (alpha.tenant_id, alpha.submitted_by) == ("tenant-alpha", "alpha-submitter")
    assert (beta.tenant_id, beta.submitted_by) == ("tenant-beta", "beta-submitter")
    assert repository.get(alpha.job_id, tenant_id="tenant-beta") is None
    assert [job.job_id for job in repository.list_jobs(tenant_id="tenant-alpha")] == [
        alpha.job_id
    ]
    with pytest.raises(JobNotFound):
        repository.list_jobs(
            tenant_id="tenant-alpha",
            after_job_id=beta.job_id,
        )
    with pytest.raises(JobNotFound):
        repository.list_events(alpha.job_id, tenant_id="tenant-beta")
    with pytest.raises(JobNotFound):
        repository.request_cancel(
            job_id=alpha.job_id,
            reason_code="operator_requested",
            tenant_id="tenant-beta",
        )
    with pytest.raises(JobNotFound):
        repository.replay(
            job_id=alpha.job_id,
            request_id="req-foreign-replay",
            idempotency_key="foreign-replay",
            tenant_id="tenant-beta",
            submitted_by="beta-operator",
        )
    with pytest.raises(IdempotencyConflict):
        repository.submit(
            request_id="req-alpha-conflict",
            request_payload=_request("不同请求").to_dict(),
            tenant_id="tenant-alpha",
            submitted_by="alpha-submitter",
            idempotency_key="shared-key",
        )


def test_tenant_admission_active_limit_is_atomic_auditable_and_namespaced(
    tmp_path: Path,
) -> None:
    repository = PlanningJobRepository(tmp_path / "admission.db")
    payload = _request().to_dict()
    first = repository.submit(
        request_id="req-alpha-first",
        request_payload=payload,
        tenant_id="tenant-alpha",
        submitted_by="alpha-admin",
        idempotency_key="alpha-first",
        tenant_active_job_limit=1,
        tenant_submission_limit_per_minute=10,
    )
    reused = repository.submit(
        request_id="req-alpha-reuse",
        request_payload=payload,
        tenant_id="tenant-alpha",
        submitted_by="alpha-admin",
        idempotency_key="alpha-first",
        tenant_active_job_limit=1,
        tenant_submission_limit_per_minute=10,
    )
    with pytest.raises(TenantAdmissionRejected) as rejected:
        repository.submit(
            request_id="req-alpha-rejected",
            request_payload=_request("另一个任务").to_dict(),
            tenant_id="tenant-alpha",
            submitted_by="alpha-admin",
            idempotency_key="alpha-second",
            tenant_active_job_limit=1,
            tenant_submission_limit_per_minute=10,
        )
    beta = repository.submit(
        request_id="req-beta-first",
        request_payload=payload,
        tenant_id="tenant-beta",
        submitted_by="beta-admin",
        idempotency_key="beta-first",
        tenant_active_job_limit=1,
        tenant_submission_limit_per_minute=10,
    )

    assert reused.job_id == first.job_id
    assert beta.tenant_id == "tenant-beta"
    assert rejected.value.code == "tenant_active_job_limit_exceeded"
    assert rejected.value.active_jobs == rejected.value.active_job_limit == 1
    alpha_events = repository.list_admission_events(tenant_id="tenant-alpha")
    assert [event.decision for event in alpha_events] == [
        "admitted",
        "idempotent_reuse",
        "rejected",
    ]
    assert alpha_events[-1].reason_code == "tenant_active_job_limit_exceeded"
    assert alpha_events[-1].job_id is None
    assert repository.list_admission_events(
        tenant_id="tenant-alpha",
        after_event_id=alpha_events[0].event_id,
        limit=1,
    ) == (alpha_events[1],)
    with pytest.raises(ValueError, match="non-negative"):
        repository.list_admission_events(
            tenant_id="tenant-alpha",
            after_event_id=-1,
        )
    with pytest.raises(ValueError, match="between 1 and 1000"):
        repository.list_admission_events(
            tenant_id="tenant-alpha",
            limit=1001,
        )
    assert [event.decision for event in repository.list_admission_events(
        tenant_id="tenant-beta"
    )] == ["admitted"]
    with sqlite3.connect(repository.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE planning_job_admission_events SET decision = 'admitted'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM planning_job_admission_events")


def test_tenant_submission_sliding_window_rejects_new_work_but_not_idempotent_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 1, 3, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])
    repository = PlanningJobRepository(tmp_path / "rate.db")
    payload = _request().to_dict()
    first = repository.submit(
        request_id="rate-first",
        request_payload=payload,
        idempotency_key="rate-first",
        tenant_active_job_limit=100,
        tenant_submission_limit_per_minute=2,
    )
    repository.submit(
        request_id="rate-second",
        request_payload=_request("第二个任务").to_dict(),
        idempotency_key="rate-second",
        tenant_active_job_limit=100,
        tenant_submission_limit_per_minute=2,
    )
    reused = repository.submit(
        request_id="rate-reuse",
        request_payload=payload,
        idempotency_key="rate-first",
        tenant_active_job_limit=100,
        tenant_submission_limit_per_minute=2,
    )
    with pytest.raises(TenantAdmissionRejected) as rejected:
        repository.submit(
            request_id="rate-rejected",
            request_payload=_request("第三个任务").to_dict(),
            idempotency_key="rate-third",
            tenant_active_job_limit=100,
            tenant_submission_limit_per_minute=2,
        )

    assert reused.job_id == first.job_id
    assert rejected.value.code == "tenant_submission_rate_exceeded"
    assert rejected.value.retry_after_seconds == 60
    clock[0] += timedelta(seconds=60)
    admitted_after_window = repository.submit(
        request_id="rate-after-window",
        request_payload=_request("窗口后的任务").to_dict(),
        idempotency_key="rate-after-window",
        tenant_active_job_limit=100,
        tenant_submission_limit_per_minute=2,
    )
    assert admitted_after_window.status == "queued"
    assert [event.decision for event in repository.list_admission_events(
        tenant_id="default"
    )] == [
        "admitted",
        "admitted",
        "idempotent_reuse",
        "rejected",
        "admitted",
    ]


def test_tenant_active_limit_serializes_concurrent_submissions(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "admission-concurrent.db")
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def submit(label: str) -> None:
        barrier.wait(timeout=5)
        try:
            repository.submit(
                request_id=f"concurrent-{label}",
                request_payload=_request(label).to_dict(),
                tenant_id="tenant-concurrent",
                submitted_by="concurrent-admin",
                idempotency_key=f"concurrent-{label}",
                tenant_active_job_limit=1,
                tenant_submission_limit_per_minute=10,
            )
            outcome = "admitted"
        except TenantAdmissionRejected as exc:
            outcome = exc.code
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=submit, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["admitted", "tenant_active_job_limit_exceeded"]
    events = repository.list_admission_events(tenant_id="tenant-concurrent")
    assert sorted(event.decision for event in events) == ["admitted", "rejected"]


def test_scheduler_rotates_least_recently_served_tenant_within_priority_band(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 1, 4, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])
    repository = PlanningJobRepository(tmp_path / "tenant-fair.db")
    alpha_first = repository.submit(
        request_id="fair-alpha-first",
        request_payload=_request("甲一").to_dict(),
        tenant_id="tenant-alpha",
        submitted_by="alpha-admin",
        priority=5,
    )
    clock[0] += timedelta(milliseconds=1)
    alpha_second = repository.submit(
        request_id="fair-alpha-second",
        request_payload=_request("甲二").to_dict(),
        tenant_id="tenant-alpha",
        submitted_by="alpha-admin",
        priority=5,
    )
    clock[0] += timedelta(milliseconds=1)
    beta_first = repository.submit(
        request_id="fair-beta-first",
        request_payload=_request("乙一").to_dict(),
        tenant_id="tenant-beta",
        submitted_by="beta-admin",
        priority=5,
    )
    clock[0] += timedelta(milliseconds=1)

    first = repository.claim_next(worker_id="fair-worker-1", lease_seconds=30)
    reopened = PlanningJobRepository(repository.path)
    second = reopened.claim_next(worker_id="fair-worker-2", lease_seconds=30)
    third = reopened.claim_next(worker_id="fair-worker-3", lease_seconds=30)

    assert [first.job_id, second.job_id, third.job_id] == [
        alpha_first.job_id,
        beta_first.job_id,
        alpha_second.job_id,
    ]
    beta_claim = reopened.list_events(beta_first.job_id)[-1]
    assert beta_claim.payload["scheduling_policy"] == (
        "tenant_fair_priority_aging_v2"
    )
    assert beta_claim.payload["tenant_fairness_policy"] == (
        "least_recently_served_tenant_v1"
    )
    assert beta_claim.payload["tenant_last_claimed_event_id_before"] == 0


def test_priority_aging_prevents_starvation_before_the_default_deadline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 1, 2, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    oldest = repository.submit(
        request_id="req-oldest",
        request_payload=_request("等待中的低优先级请求").to_dict(),
        priority=0,
    )
    clock[0] += timedelta(seconds=540)
    newest = repository.submit(
        request_id="req-newest",
        request_payload=_request("刚到达的高优先级请求").to_dict(),
        priority=9,
    )
    clock[0] += timedelta(seconds=1)

    claimed = repository.claim_next(worker_id="aging-worker", lease_seconds=30)

    assert claimed is not None and claimed.job_id == oldest.job_id
    assert repository.get(newest.job_id).status == "queued"
    claim_event = repository.list_events(oldest.job_id)[-1]
    assert claim_event.payload["base_priority"] == 0
    assert claim_event.payload["effective_priority"] == 9
    assert claim_event.payload["eligible_at"] == oldest.available_at
    assert claim_event.payload["queue_wait_ms"] == 541_000


def test_retry_backoff_is_ineligible_and_does_not_accrue_priority_age(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 1, 3, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    delayed = repository.submit(
        request_id="req-delayed",
        request_payload=_request("需要退避重试").to_dict(),
        priority=9,
    )
    claimed = repository.claim_next(worker_id="retry-worker", lease_seconds=30)
    assert claimed is not None and claimed.job_id == delayed.job_id
    clock[0] += timedelta(seconds=1)
    retried = repository.retry_or_dead_letter(
        job_id=delayed.job_id,
        worker_id="retry-worker",
        error_code="temporary",
        error_message="Temporary failure.",
        backoff_seconds=300,
    )
    assert retried.available_at == "2026-01-03T00:05:01.000Z"
    clock[0] = datetime(2026, 1, 3, 0, 1, 40, tzinfo=timezone.utc)
    ready = repository.submit(
        request_id="req-ready",
        request_payload=_request("已可执行的普通请求").to_dict(),
        priority=0,
    )
    clock[0] = datetime(2026, 1, 3, 0, 3, 20, tzinfo=timezone.utc)

    next_claim = repository.claim_next(worker_id="ready-worker", lease_seconds=30)

    assert next_claim is not None and next_claim.job_id == ready.job_id
    assert repository.get(delayed.job_id).status == "queued"
    claim_event = repository.list_events(ready.job_id)[-1]
    assert claim_event.payload["effective_priority"] == 1
    assert claim_event.payload["queue_wait_ms"] == 100_000


def test_worker_lease_and_artifact_are_persisted(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    created = repository.submit(request_id="req-1", request_payload=_request().to_dict())
    claimed = repository.claim_next(worker_id="worker-a", lease_seconds=30)

    assert claimed is not None
    assert claimed.job_id == created.job_id
    assert claimed.status == "running"
    assert claimed.attempt == 1
    assert repository.claim_next(worker_id="worker-b") is None

    result_payload = {"ok": True, "city": "北京"}
    finished = repository.succeed(
        job_id=claimed.job_id,
        worker_id="worker-a",
        result_payload=result_payload,
    )
    canonical = json.dumps(
        result_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert finished.status == "succeeded"
    assert finished.artifact_id == f"artifact-{created.job_id.removeprefix('job-')}"
    assert finished.artifact_sha256 == hashlib.sha256(canonical.encode()).hexdigest()
    assert finished.result_payload == result_payload
    assert finished.lease_owner is None
    events = repository.list_events(created.job_id)
    assert [event.event_type for event in events] == ["submitted", "claimed", "succeeded"]
    assert [event.event_id for event in events] == sorted(event.event_id for event in events)
    assert events[1].attempt == 1
    assert events[1].worker_id == "worker-a"
    assert events[2].payload["artifact_sha256"] == finished.artifact_sha256
    assert repository.list_events(
        created.job_id, after_event_id=events[1].event_id
    ) == (events[2],)


def test_expired_running_job_can_be_reclaimed(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    created = repository.submit(request_id="req-1", request_payload=_request().to_dict())
    repository.claim_next(worker_id="dead-worker", lease_seconds=30)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE planning_jobs SET lease_expires_at = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00.000Z", created.job_id),
        )

    recovered = repository.claim_next(worker_id="recovery-worker", lease_seconds=30)
    assert recovered is not None
    assert recovered.job_id == created.job_id
    assert recovered.attempt == 2
    assert recovered.lease_owner == "recovery-worker"
    assert [event.event_type for event in repository.list_events(created.job_id)] == [
        "submitted",
        "claimed",
        "lease_reclaimed",
    ]
    with pytest.raises(RuntimeError, match="expired, or owned"):
        repository.succeed(
            job_id=created.job_id,
            worker_id="dead-worker",
            result_payload={"stale": True},
        )
    finished = repository.succeed(
        job_id=created.job_id,
        worker_id="recovery-worker",
        result_payload={"recovered": True},
    )
    assert finished.status == "succeeded"


def test_lease_is_reclaimable_at_the_exact_expiration_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 1, 4, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    created = repository.submit(
        request_id="req-exact-lease-expiry",
        request_payload=_request().to_dict(),
    )
    repository.claim_next(worker_id="old-worker", lease_seconds=30)
    clock[0] += timedelta(seconds=30)

    reclaimed = repository.claim_next(worker_id="new-worker", lease_seconds=30)

    assert reclaimed is not None and reclaimed.job_id == created.job_id
    assert reclaimed.attempt == 2
    event = repository.list_events(created.job_id)[-1]
    assert event.event_type == "lease_reclaimed"
    assert event.payload["eligible_at"] == "2026-01-04T00:00:30.000Z"
    assert event.payload["queue_wait_ms"] == 0


def test_job_service_runs_canonical_request_and_retries_sanitized_failure(tmp_path: Path) -> None:
    success_backend = StubPlanningService()
    success_service = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "success.db"),
        planning_service=success_backend,
    )
    submitted = success_service.submit(request=_request(), request_id="req-success")
    completed = success_service.run_once(worker_id="worker-success")

    assert completed is not None
    assert completed.job_id == submitted.job_id
    assert completed.status == "succeeded"
    assert completed.result_payload["data_profile"]["classification"] == "synthetic"
    assert success_backend.requests[0].preferences.raw_input == "下午出去玩"

    failure_service = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "failure.db"),
        planning_service=StubPlanningService(RuntimeError("secret-provider-token")),
        max_attempts=3,
        retry_base_seconds=0,
        retry_max_seconds=0,
    )
    failure_service.submit(request=_request(), request_id="req-failure")
    first = failure_service.run_once(worker_id="worker-failure-1")
    second = failure_service.run_once(worker_id="worker-failure-2")
    failed = failure_service.run_once(worker_id="worker-failure-3")

    assert first is not None and first.status == "queued"
    assert second is not None and second.status == "queued"
    assert failed is not None and failed.status == "dead_lettered"
    assert failed.attempt == failed.max_attempts == 3
    assert failed.error_code == "planning_execution_failed"
    assert failed.error_message == "Planning execution failed."
    assert "secret-provider-token" not in failed.error_message
    failure_events = failure_service.events(failed.job_id)
    assert [event.event_type for event in failure_events] == [
        "submitted",
        "claimed",
        "retry_scheduled",
        "claimed",
        "retry_scheduled",
        "claimed",
        "dead_lettered",
    ]
    assert failure_events[-1].payload["error_code"] == "planning_execution_failed"
    assert failure_events[-1].payload["max_attempts"] == 3
    assert "secret-provider-token" not in json.dumps(failure_events[-1].payload)


def test_heartbeat_extends_lease_and_is_persisted(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    created = repository.submit(request_id="req-1", request_payload=_request().to_dict())
    claimed = repository.claim_next(worker_id="worker-a", lease_seconds=1)
    assert claimed is not None
    heartbeat = repository.heartbeat(
        job_id=created.job_id,
        worker_id="worker-a",
        lease_seconds=30,
    )
    assert heartbeat.lease_expires_at > claimed.lease_expires_at
    assert repository.claim_next(worker_id="worker-b", lease_seconds=1) is None
    with pytest.raises(RuntimeError, match="owned by another worker"):
        repository.heartbeat(
            job_id=created.job_id,
            worker_id="worker-b",
            lease_seconds=30,
        )
    assert [event.event_type for event in repository.list_events(created.job_id)] == [
        "submitted",
        "claimed",
        "heartbeat",
    ]


def test_service_maintains_heartbeat_during_slow_execution(tmp_path: Path) -> None:
    service = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubPlanningService(delay_seconds=0.12),
    )
    submitted = service.submit(request=_request(), request_id="req-slow")
    completed = service.run_once(
        worker_id="worker-slow",
        lease_seconds=1,
        heartbeat_interval_seconds=0.02,
    )
    assert completed is not None and completed.status == "succeeded"
    event_types = [event.event_type for event in service.events(submitted.job_id)]
    assert event_types[0:2] == ["submitted", "claimed"]
    assert "heartbeat" in event_types
    assert event_types[-1] == "succeeded"


def test_invalid_heartbeat_configuration_does_not_claim_job(tmp_path: Path) -> None:
    service = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubPlanningService(),
    )
    submitted = service.submit(request=_request(), request_id="req-heartbeat-config")
    with pytest.raises(ValueError, match="shorter than the lease"):
        service.run_once(
            worker_id="worker-invalid-config",
            lease_seconds=1,
            heartbeat_interval_seconds=1,
        )
    persisted = service.get(submitted.job_id)
    assert persisted is not None and persisted.status == "queued" and persisted.attempt == 0


def test_invalid_persisted_request_is_non_retryable(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    service = PlanningJobService(
        repository=repository,
        planning_service=StubPlanningService(),
        max_attempts=3,
        retry_base_seconds=0,
    )
    submitted = service.submit(request=_request(), request_id="req-invalid")
    with repository._connect() as connection:
        connection.execute(
            "UPDATE planning_jobs SET request_json = ? WHERE job_id = ?",
            ('{"user_input":""}', submitted.job_id),
        )
    failed = service.run_once(worker_id="worker-invalid")
    assert failed is not None and failed.status == "failed"
    assert failed.attempt == 1
    assert failed.error_code == "invalid_persisted_request"
    assert [event.event_type for event in service.events(submitted.job_id)] == [
        "submitted",
        "claimed",
        "failed",
    ]


def test_persisted_ambiguous_request_is_non_retryable(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    request = PlanRequest(user_input="还是上次那个地方，下午安排一下")
    submitted = repository.submit(
        request_id="req-clarification",
        request_payload=request.to_dict(),
    )
    service = PlanningJobService(repository=repository)

    failed = service.run_once(worker_id="worker-clarification")

    assert failed is not None and failed.status == "failed"
    assert failed.error_code == "clarification_required"
    assert failed.error_message == "The persisted planning request needs clarification."
    assert [event.event_type for event in service.events(submitted.job_id)] == [
        "submitted",
        "claimed",
        "failed",
    ]


def test_invalid_model_output_is_terminal_and_not_retried(tmp_path: Path) -> None:
    snapshot = ModelOutputContractSnapshot.create(
        status="rejected",
        attempt_count=2,
        repair_attempted=True,
        candidate_count=8,
        issue_codes=("candidate_id_not_allowed",),
    )
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    service = PlanningJobService(
        repository=repository,
        planning_service=StubPlanningService(ModelOutputContractError(snapshot)),
        max_attempts=3,
        retry_base_seconds=0,
    )
    submitted = service.submit(
        request=_request(),
        request_id="req-invalid-model-output",
    )

    failed = service.run_once(worker_id="worker-invalid-model-output")

    assert failed is not None and failed.status == "failed"
    assert failed.attempt == 1
    assert failed.error_code == "invalid_model_output"
    assert failed.error_message == "The model could not produce a valid grounded plan."
    assert [event.event_type for event in service.events(submitted.job_id)] == [
        "submitted",
        "claimed",
        "failed",
    ]


def test_submit_persists_text_derived_constraints_without_inventing_provenance(
    tmp_path: Path,
) -> None:
    service = PlanningJobService(repository=PlanningJobRepository(tmp_path / "jobs.db"))
    text = "周六下午三点，两个人在三里屯玩三小时，人均预算100元，不吃辣"

    submitted = service.submit(
        request=PlanRequest(user_input=text),
        request_id="req-normalized-constraints",
    )
    restored = PlanRequest.from_dict(submitted.request_payload)

    assert restored.area_anchor == "三里屯片区"
    assert restored.preferences.party_size == 2
    assert restored.preferences.target_start == "15:00"
    assert restored.preferences.duration_hours == 3
    assert restored.preferences.budget_per_person == 100
    assert restored.preferences.diet_flags == ["no_spicy"]
    assert restored.provided_fields == frozenset({"user_input"})
    repeated = service.preflight.normalize(restored)
    assert repeated.request.to_dict() == restored.to_dict()
    assert repeated.constraints.conflicts == ()


def test_value_error_from_planning_execution_uses_retry_policy(tmp_path: Path) -> None:
    service = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=StubPlanningService(ValueError("transient-provider-value")),
        max_attempts=2,
        retry_base_seconds=0,
    )
    submitted = service.submit(request=_request(), request_id="req-runtime-value-error")

    first = service.run_once(worker_id="worker-value-1")
    second = service.run_once(worker_id="worker-value-2")

    assert first is not None and first.status == "queued"
    assert second is not None and second.status == "dead_lettered"
    assert second.error_code == "planning_execution_failed"
    assert "transient-provider-value" not in second.error_message
    assert [event.event_type for event in service.events(submitted.job_id)] == [
        "submitted",
        "claimed",
        "retry_scheduled",
        "claimed",
        "dead_lettered",
    ]


def test_queued_cancellation_is_immediate_idempotent_and_not_claimable(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    submitted = repository.submit(request_id="req-cancel", request_payload=_request().to_dict())

    cancelled = repository.request_cancel(
        job_id=submitted.job_id,
        reason_code="user_requested",
    )
    repeated = repository.request_cancel(
        job_id=submitted.job_id,
        reason_code="user_requested",
    )

    assert cancelled.status == repeated.status == "cancelled"
    assert cancelled.cancel_requested_at is not None
    assert cancelled.cancelled_at is not None
    assert cancelled.cancel_reason_code == "user_requested"
    assert cancelled.attempt == 0
    assert repository.claim_next(worker_id="worker-after-cancel") is None
    assert [event.event_type for event in repository.list_events(submitted.job_id)] == [
        "submitted",
        "cancelled",
    ]


def test_running_cancellation_is_durable_and_wins_over_completed_result(tmp_path: Path) -> None:
    backend = BlockingPlanningService()
    service = PlanningJobService(
        repository=PlanningJobRepository(tmp_path / "jobs.db"),
        planning_service=backend,
    )
    submitted = service.submit(request=_request(), request_id="req-running-cancel")
    completed: list = []
    errors: list[Exception] = []

    def run_worker() -> None:
        try:
            completed.append(
                service.run_once(
                    worker_id="worker-cancel",
                    lease_seconds=5,
                    heartbeat_interval_seconds=1,
                )
            )
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_worker)
    worker.start()
    assert backend.started.wait(timeout=5)
    requested = service.cancel(job_id=submitted.job_id, reason_code="superseded")
    assert requested.status == "running"
    assert requested.cancel_requested_at is not None
    repeated = service.cancel(job_id=submitted.job_id, reason_code="superseded")
    assert repeated.status == "running"
    assert repeated.cancel_requested_at == requested.cancel_requested_at
    backend.release.set()
    worker.join(timeout=5)

    assert not errors
    assert len(completed) == 1
    assert completed[0] is not None and completed[0].status == "cancelled"
    assert completed[0].result_payload is None
    assert completed[0].artifact_id is None
    assert [event.event_type for event in service.events(submitted.job_id)] == [
        "submitted",
        "claimed",
        "cancel_requested",
        "cancelled",
    ]


def test_expired_cancel_request_is_finalized_instead_of_reclaimed(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    submitted = repository.submit(request_id="req-expired-cancel", request_payload=_request().to_dict())
    repository.claim_next(worker_id="worker-lost", lease_seconds=30)
    repository.request_cancel(job_id=submitted.job_id, reason_code="operator_requested")
    with repository._connect() as connection:
        connection.execute(
            "UPDATE planning_jobs SET lease_expires_at = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00.000Z", submitted.job_id),
        )

    assert repository.claim_next(worker_id="worker-reaper") is None
    cancelled = repository.get(submitted.job_id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert [event.event_type for event in repository.list_events(submitted.job_id)] == [
        "submitted",
        "claimed",
        "cancel_requested",
        "cancelled",
    ]


def test_queued_deadline_is_durable_not_claimed_and_replay_gets_fresh_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    submitted = repository.submit(
        request_id="req-timeout",
        request_payload=_request().to_dict(),
        deadline_seconds=10,
    )

    assert submitted.deadline_seconds == 10
    assert submitted.deadline_at == "2026-01-01T00:00:10.000Z"
    summary = repository.list_jobs()[0]
    assert (summary.deadline_seconds, summary.deadline_at) == (
        submitted.deadline_seconds,
        submitted.deadline_at,
    )

    clock[0] = datetime(2026, 1, 1, 0, 0, 11, tzinfo=timezone.utc)
    assert repository.claim_next(worker_id="worker-too-late") is None
    terminal = repository.get(submitted.job_id)
    assert terminal is not None and terminal.status == "timed_out"
    assert terminal.attempt == 0
    assert terminal.error_code == "job_deadline_exceeded"
    assert terminal.error_message == "Planning job exceeded its durable deadline."
    assert [event.event_type for event in repository.list_events(submitted.job_id)] == [
        "submitted",
        "timed_out",
    ]

    clock[0] = datetime(2026, 1, 1, 0, 0, 20, tzinfo=timezone.utc)
    replayed = repository.replay(
        job_id=submitted.job_id,
        request_id="req-timeout-replay",
        idempotency_key="timeout-replay-key",
    )
    assert replayed.status == "queued"
    assert replayed.deadline_seconds == 10
    assert replayed.deadline_at == "2026-01-01T00:00:30.000Z"
    assert replayed.replayed_from_job_id == submitted.job_id


def test_cancel_and_deadline_race_is_resolved_by_first_durable_signal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])

    cancel_first = PlanningJobRepository(tmp_path / "cancel-first.db")
    cancellable = cancel_first.submit(
        request_id="req-cancel-first",
        request_payload=_request().to_dict(),
        deadline_seconds=10,
    )
    cancel_first.claim_next(worker_id="worker-cancel-first", lease_seconds=30)
    clock[0] = datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
    requested = cancel_first.request_cancel(
        job_id=cancellable.job_id,
        reason_code="user_requested",
    )
    assert requested.status == "running"
    clock[0] = datetime(2026, 1, 1, 0, 0, 11, tzinfo=timezone.utc)
    cancelled = cancel_first.heartbeat(
        job_id=cancellable.job_id,
        worker_id="worker-cancel-first",
        lease_seconds=30,
    )
    assert cancelled.status == "cancelled"
    assert [event.event_type for event in cancel_first.list_events(cancellable.job_id)] == [
        "submitted",
        "claimed",
        "cancel_requested",
        "cancelled",
    ]

    clock[0] = datetime(2026, 1, 2, tzinfo=timezone.utc)
    deadline_first = PlanningJobRepository(tmp_path / "deadline-first.db")
    expiring = deadline_first.submit(
        request_id="req-deadline-first",
        request_payload=_request().to_dict(),
        deadline_seconds=10,
    )
    deadline_first.claim_next(worker_id="worker-deadline-first", lease_seconds=30)
    clock[0] = datetime(2026, 1, 2, 0, 0, 11, tzinfo=timezone.utc)
    timed_out = deadline_first.request_cancel(
        job_id=expiring.job_id,
        reason_code="user_requested",
    )
    assert timed_out.status == "timed_out"
    assert timed_out.cancel_requested_at is None
    assert [event.event_type for event in deadline_first.list_events(expiring.job_id)] == [
        "submitted",
        "claimed",
        "timed_out",
    ]


def test_deadline_wins_over_late_success_and_job_service_observes_safe_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [datetime(2026, 1, 3, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    service = PlanningJobService(
        repository=repository,
        planning_service=DeadlineBoundaryPlanningService(
            clock,
            datetime(2026, 1, 3, 0, 0, 6, tzinfo=timezone.utc),
        ),
        default_deadline_seconds=5,
    )
    submitted = service.submit(request=_request(), request_id="req-boundary-timeout")

    terminal = service.run_once(
        worker_id="worker-boundary-timeout",
        lease_seconds=30,
        heartbeat_interval_seconds=10,
    )

    assert terminal is not None and terminal.status == "timed_out"
    assert terminal.result_payload is None
    assert terminal.artifact_id is None
    assert [event.event_type for event in service.events(submitted.job_id)] == [
        "submitted",
        "claimed",
        "timed_out",
    ]


def test_timeout_state_and_event_roll_back_together(tmp_path: Path, monkeypatch) -> None:
    clock = [datetime(2026, 1, 4, tzinfo=timezone.utc)]
    monkeypatch.setattr(job_repository_module, "_utc_now", lambda: clock[0])
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    submitted = repository.submit(
        request_id="req-timeout-atomic",
        request_payload=_request().to_dict(),
        deadline_seconds=1,
    )
    with repository._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_timeout_event
            BEFORE INSERT ON planning_job_events
            WHEN NEW.event_type = 'timed_out'
            BEGIN
                SELECT RAISE(ABORT, 'forced timeout event failure');
            END
            """
        )
    clock[0] = datetime(2026, 1, 4, 0, 0, 2, tzinfo=timezone.utc)

    with pytest.raises(sqlite3.IntegrityError, match="forced timeout event failure"):
        repository.claim_next(worker_id="worker-timeout-atomic")
    persisted = repository.get(submitted.job_id)
    assert persisted is not None and persisted.status == "queued"
    assert [event.event_type for event in repository.list_events(submitted.job_id)] == [
        "submitted"
    ]


def test_dead_letter_replay_is_new_idempotent_job_with_atomic_lineage(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    source = repository.submit(
        request_id="req-replay-source",
        request_payload=_request().to_dict(),
        max_attempts=1,
        priority=7,
    )
    repository.claim_next(worker_id="worker-fail", lease_seconds=30)
    terminal = repository.retry_or_dead_letter(
        job_id=source.job_id,
        worker_id="worker-fail",
        error_code="temporary",
        error_message="Temporary failure.",
        backoff_seconds=0,
    )
    assert terminal.status == "dead_lettered"

    replayed = repository.replay(
        job_id=source.job_id,
        request_id="req-replay",
        idempotency_key="replay-key",
    )
    repeated = repository.replay(
        job_id=source.job_id,
        request_id="req-replay-repeat",
        idempotency_key="replay-key",
    )

    assert replayed.job_id == repeated.job_id
    assert replayed.job_id != source.job_id
    assert replayed.status == "queued"
    assert replayed.attempt == 0
    assert replayed.max_attempts == 1
    assert replayed.priority == source.priority == 7
    assert replayed.replayed_from_job_id == source.job_id
    assert replayed.request_payload == source.request_payload
    assert [event.event_type for event in repository.list_events(source.job_id)][-1] == (
        "replay_requested"
    )
    replay_events = repository.list_events(replayed.job_id)
    assert [event.event_type for event in replay_events] == ["submitted"]
    assert replay_events[0].payload["replayed_from_job_id"] == source.job_id
    assert [job.job_id for job in repository.list_jobs(status="dead_lettered")] == [
        source.job_id
    ]
    first_page = repository.list_jobs(limit=1)
    second_page = repository.list_jobs(after_job_id=first_page[0].job_id, limit=1)
    assert [job.job_id for job in first_page + second_page] == [source.job_id, replayed.job_id]
    with pytest.raises(IdempotencyConflict):
        repository.submit(
            request_id="req-wrong-namespace",
            request_payload=_request().to_dict(),
            idempotency_key="replay-key",
        )


def test_replay_obeys_tenant_admission_and_idempotent_retry_bypasses_it(
    tmp_path: Path,
) -> None:
    repository = PlanningJobRepository(tmp_path / "replay-admission.db")
    source = repository.submit(
        request_id="req-replay-admission-source",
        request_payload=_request().to_dict(),
        tenant_id="tenant-replay",
        submitted_by="replay-admin",
        max_attempts=1,
    )
    repository.claim_next(worker_id="replay-failure-worker", lease_seconds=30)
    terminal = repository.retry_or_dead_letter(
        job_id=source.job_id,
        worker_id="replay-failure-worker",
        error_code="temporary",
        error_message="Temporary failure.",
        backoff_seconds=0,
    )
    assert terminal.status == "dead_lettered"
    blocker = repository.submit(
        request_id="req-replay-admission-blocker",
        request_payload=_request("占用租户执行槽").to_dict(),
        tenant_id="tenant-replay",
        submitted_by="replay-admin",
    )

    with pytest.raises(TenantAdmissionRejected) as rejected:
        repository.replay(
            job_id=source.job_id,
            request_id="req-replay-admission-rejected",
            idempotency_key="replay-admission-key",
            tenant_id="tenant-replay",
            submitted_by="replay-admin",
            tenant_active_job_limit=1,
            tenant_submission_limit_per_minute=10,
        )
    assert rejected.value.code == "tenant_active_job_limit_exceeded"

    repository.request_cancel(
        job_id=blocker.job_id,
        reason_code="operator_requested",
        tenant_id="tenant-replay",
    )
    replayed = repository.replay(
        job_id=source.job_id,
        request_id="req-replay-admission-admitted",
        idempotency_key="replay-admission-key",
        tenant_id="tenant-replay",
        submitted_by="replay-admin",
        tenant_active_job_limit=1,
        tenant_submission_limit_per_minute=10,
    )
    reused = repository.replay(
        job_id=source.job_id,
        request_id="req-replay-admission-reused",
        idempotency_key="replay-admission-key",
        tenant_id="tenant-replay",
        submitted_by="replay-admin",
        tenant_active_job_limit=1,
        tenant_submission_limit_per_minute=10,
    )

    assert reused.job_id == replayed.job_id
    replay_events = [
        event
        for event in repository.list_admission_events(tenant_id="tenant-replay")
        if event.operation == "replay"
    ]
    assert [event.decision for event in replay_events] == [
        "rejected",
        "admitted",
        "idempotent_reuse",
    ]
    assert replay_events[0].reason_code == "tenant_active_job_limit_exceeded"
    assert replay_events[0].job_id is None
    assert replay_events[1].job_id == replayed.job_id
    assert replay_events[2].job_id == replayed.job_id


def test_replay_rejects_non_failure_status_and_rolls_back_if_lineage_event_fails(
    tmp_path: Path,
) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    queued = repository.submit(
        request_id="req-queued",
        request_payload=_request().to_dict(),
        max_attempts=1,
    )
    with pytest.raises(InvalidJobTransition, match="cannot replay"):
        repository.replay(
            job_id=queued.job_id,
            request_id="req-invalid-replay",
            idempotency_key="invalid-replay-key",
        )

    repository.claim_next(worker_id="worker-fail", lease_seconds=30)
    repository.retry_or_dead_letter(
        job_id=queued.job_id,
        worker_id="worker-fail",
        error_code="failed",
        error_message="Failed.",
        backoff_seconds=0,
    )
    with repository._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_replay_lineage
            BEFORE INSERT ON planning_job_events
            WHEN NEW.event_type = 'replay_requested'
            BEGIN
                SELECT RAISE(ABORT, 'forced replay event failure');
            END
            """
        )
    before_ids = [job.job_id for job in repository.list_jobs()]
    with pytest.raises(sqlite3.IntegrityError, match="forced replay event failure"):
        repository.replay(
            job_id=queued.job_id,
            request_id="req-atomic-replay",
            idempotency_key="atomic-replay-key",
        )
    assert [job.job_id for job in repository.list_jobs()] == before_ids


def test_cancellation_state_and_event_roll_back_together(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    submitted = repository.submit(request_id="req-cancel-atomic", request_payload=_request().to_dict())
    with repository._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_cancel_event
            BEFORE INSERT ON planning_job_events
            WHEN NEW.event_type = 'cancelled'
            BEGIN
                SELECT RAISE(ABORT, 'forced cancel event failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced cancel event failure"):
        repository.request_cancel(
            job_id=submitted.job_id,
            reason_code="user_requested",
        )
    persisted = repository.get(submitted.job_id)
    assert persisted is not None and persisted.status == "queued"
    assert persisted.cancel_requested_at is None
    assert [event.event_type for event in repository.list_events(submitted.job_id)] == [
        "submitted"
    ]


def test_retry_backoff_and_expired_final_attempt_are_fail_closed(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    delayed = repository.submit(
        request_id="req-delay",
        request_payload=_request().to_dict(),
        max_attempts=2,
    )
    repository.claim_next(worker_id="worker-delay", lease_seconds=30)
    queued = repository.retry_or_dead_letter(
        job_id=delayed.job_id,
        worker_id="worker-delay",
        error_code="temporary",
        error_message="Temporary failure.",
        backoff_seconds=60,
    )
    assert queued.status == "queued"
    assert repository.claim_next(worker_id="too-early", lease_seconds=30) is None

    exhausted = repository.submit(
        request_id="req-expired",
        request_payload=_request("最终一次").to_dict(),
        max_attempts=1,
    )
    repository.claim_next(worker_id="dead-worker", lease_seconds=30)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE planning_jobs SET lease_expires_at = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00.000Z", exhausted.job_id),
        )
    assert repository.claim_next(worker_id="reaper", lease_seconds=30) is None
    persisted = repository.get(exhausted.job_id)
    assert persisted is not None and persisted.status == "dead_lettered"
    assert persisted.error_code == "lease_expired_attempts_exhausted"
    assert repository.list_events(exhausted.job_id)[-1].event_type == "dead_lettered"


def test_concurrent_workers_claim_a_job_once(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    created = repository.submit(request_id="req-race", request_payload=_request().to_dict())
    barrier = threading.Barrier(2)
    results = []
    errors: list[Exception] = []

    def claim(worker_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            results.append(
                repository.claim_next(worker_id=worker_id, lease_seconds=30)
            )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=claim, args=("worker-a",)),
        threading.Thread(target=claim, args=("worker-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].job_id == created.job_id
    assert [event.event_type for event in repository.list_events(created.job_id)] == [
        "submitted",
        "claimed",
    ]


def test_concurrent_workers_preserve_priority_order_across_claim_transactions(
    tmp_path: Path,
) -> None:
    repository = PlanningJobRepository(tmp_path / "priority-race.db")
    lower = repository.submit(
        request_id="req-priority-race-lower",
        request_payload=_request("普通任务").to_dict(),
        priority=0,
    )
    higher = repository.submit(
        request_id="req-priority-race-higher",
        request_payload=_request("紧急任务").to_dict(),
        priority=9,
    )
    barrier = threading.Barrier(2)
    results = []
    errors: list[Exception] = []

    def claim(worker_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            results.append(repository.claim_next(worker_id=worker_id, lease_seconds=30))
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=claim, args=("priority-worker-a",)),
        threading.Thread(target=claim, args=("priority-worker-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert {result.job_id for result in results if result is not None} == {
        lower.job_id,
        higher.job_id,
    }
    higher_claim = repository.list_events(higher.job_id)[-1]
    lower_claim = repository.list_events(lower.job_id)[-1]
    assert higher_claim.event_type == lower_claim.event_type == "claimed"
    assert higher_claim.event_id < lower_claim.event_id


def test_retry_state_and_event_roll_back_together(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    created = repository.submit(request_id="req-atomic", request_payload=_request().to_dict())
    repository.claim_next(worker_id="worker-atomic", lease_seconds=30)
    with repository._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_retry_event
            BEFORE INSERT ON planning_job_events
            WHEN NEW.event_type = 'retry_scheduled'
            BEGIN
                SELECT RAISE(ABORT, 'forced retry event failure');
            END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="forced retry event failure"):
        repository.retry_or_dead_letter(
            job_id=created.job_id,
            worker_id="worker-atomic",
            error_code="temporary",
            error_message="Temporary failure.",
            backoff_seconds=0,
        )
    persisted = repository.get(created.job_id)
    assert persisted is not None and persisted.status == "running"
    assert persisted.lease_owner == "worker-atomic"
    assert [event.event_type for event in repository.list_events(created.job_id)] == [
        "submitted",
        "claimed",
    ]


def test_legacy_job_schema_migrates_without_losing_state_or_events(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE planning_jobs (
                job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
                request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                idempotency_key TEXT UNIQUE, attempt INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                lease_owner TEXT, lease_expires_at TEXT, artifact_id TEXT,
                artifact_sha256 TEXT, result_json TEXT, error_code TEXT, error_message TEXT
            );
            CREATE TABLE planning_job_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(
                    event_type IN ('submitted','claimed','lease_reclaimed','succeeded','failed')
                ),
                attempt INTEGER NOT NULL, worker_id TEXT, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES planning_jobs(job_id)
            );
            """
        )
        payload = json.dumps(_request().to_dict(), ensure_ascii=False)
        connection.execute(
            """
            INSERT INTO planning_jobs(
                job_id, request_id, status, request_json, request_sha256,
                attempt, created_at, updated_at
            ) VALUES ('job-legacy', 'req-legacy', 'queued', ?, 'sha', 0, ?, ?)
            """,
            (payload, "2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z"),
        )
        connection.execute(
            """
            INSERT INTO planning_job_events(
                job_id, event_type, attempt, worker_id, payload_json, created_at
            ) VALUES ('job-legacy', 'submitted', 0, NULL, '{}', ?)
            """,
            ("2026-01-01T00:00:00.000Z",),
        )

    repository = PlanningJobRepository(database)
    migrated = repository.get("job-legacy")
    assert migrated is not None
    assert migrated.max_attempts == 3
    assert migrated.priority == 0
    assert migrated.deadline_seconds == 900
    assert migrated.deadline_at is None
    assert migrated.available_at == migrated.created_at
    assert [event.event_type for event in repository.list_events("job-legacy")] == ["submitted"]
    claimed = repository.claim_next(worker_id="worker-migrated", lease_seconds=30)
    assert claimed is not None and claimed.job_id == "job-legacy"
    repository.heartbeat(
        job_id="job-legacy",
        worker_id="worker-migrated",
        lease_seconds=30,
    )


def test_v47_dead_letter_schema_migrates_and_remains_replayable(tmp_path: Path) -> None:
    database = tmp_path / "v47.db"
    payload = json.dumps(_request().to_dict(), ensure_ascii=False)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE planning_jobs (
                job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(
                    status IN ('queued','running','succeeded','failed','dead_lettered')
                ),
                request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                idempotency_key TEXT UNIQUE, attempt INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                available_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                lease_owner TEXT, lease_expires_at TEXT, artifact_id TEXT,
                artifact_sha256 TEXT, result_json TEXT, error_code TEXT, error_message TEXT
            );
            CREATE TABLE planning_job_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN (
                    'submitted','claimed','heartbeat','retry_scheduled',
                    'lease_reclaimed','succeeded','failed','dead_lettered'
                )),
                attempt INTEGER NOT NULL, worker_id TEXT, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES planning_jobs(job_id)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO planning_jobs(
                job_id, request_id, status, request_json, request_sha256,
                attempt, max_attempts, available_at, created_at, updated_at,
                error_code, error_message
            ) VALUES ('job-v47-dead', 'req-v47', 'dead_lettered', ?, 'sha', 1, 1, ?, ?, ?,
                      'planning_execution_failed', 'Planning execution failed.')
            """,
            (
                payload,
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO planning_job_events(
                job_id, event_type, attempt, worker_id, payload_json, created_at
            ) VALUES ('job-v47-dead', 'dead_lettered', 1, 'worker-v47', '{}', ?)
            """,
            ("2026-01-01T00:00:00.000Z",),
        )

    repository = PlanningJobRepository(database)
    migrated = repository.get("job-v47-dead")
    assert migrated is not None and migrated.status == "dead_lettered"
    assert migrated.priority == 0
    assert migrated.deadline_seconds == 900
    assert migrated.deadline_at is None
    assert migrated.cancel_requested_at is None
    assert migrated.replayed_from_job_id is None
    replayed = repository.replay(
        job_id=migrated.job_id,
        request_id="req-v48-replay",
        idempotency_key="v47-replay-key",
    )
    assert replayed.status == "queued"
    assert replayed.priority == 0
    assert replayed.replayed_from_job_id == migrated.job_id
    assert replayed.deadline_seconds == 900
    assert replayed.deadline_at is not None


def test_v48_control_schema_migrates_without_rewriting_terminal_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v48.db"
    payload = json.dumps(_request().to_dict(), ensure_ascii=False)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE planning_jobs (
                job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'queued','running','succeeded','failed','dead_lettered','cancelled'
                )),
                request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                idempotency_key TEXT UNIQUE, attempt INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                available_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                cancel_requested_at TEXT, cancelled_at TEXT, cancel_reason_code TEXT,
                replayed_from_job_id TEXT, lease_owner TEXT, lease_expires_at TEXT,
                artifact_id TEXT, artifact_sha256 TEXT, result_json TEXT,
                error_code TEXT, error_message TEXT,
                FOREIGN KEY(replayed_from_job_id) REFERENCES planning_jobs(job_id)
            );
            CREATE TABLE planning_job_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN (
                    'submitted','claimed','heartbeat','retry_scheduled','lease_reclaimed',
                    'cancel_requested','cancelled','replay_requested',
                    'succeeded','failed','dead_lettered'
                )),
                attempt INTEGER NOT NULL, worker_id TEXT, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES planning_jobs(job_id)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO planning_jobs(
                job_id, request_id, status, request_json, request_sha256,
                attempt, max_attempts, available_at, created_at, updated_at,
                cancel_requested_at, cancelled_at, cancel_reason_code
            ) VALUES ('job-v48-cancel', 'req-v48', 'cancelled', ?, 'sha',
                      0, 3, ?, ?, ?, ?, ?, 'user_requested')
            """,
            (
                payload,
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:01.000Z",
                "2026-01-01T00:00:01.000Z",
                "2026-01-01T00:00:01.000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO planning_job_events(
                job_id, event_type, attempt, worker_id, payload_json, created_at
            ) VALUES ('job-v48-cancel', 'cancelled', 0, NULL,
                      '{"reason_code":"user_requested"}', ?)
            """,
            ("2026-01-01T00:00:01.000Z",),
        )

    repository = PlanningJobRepository(database)
    migrated = repository.get("job-v48-cancel")
    assert migrated is not None and migrated.status == "cancelled"
    assert migrated.priority == 0
    assert migrated.deadline_seconds == 900
    assert migrated.deadline_at is None
    assert migrated.cancel_reason_code == "user_requested"
    assert [event.event_type for event in repository.list_events(migrated.job_id)] == [
        "cancelled"
    ]
    with repository._connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v57_current_schema_adds_priority_without_rewriting_rows_or_events(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v57.db"
    schema_without_priority = job_repository_module.SCHEMA.split(
        "CREATE TABLE IF NOT EXISTS planning_job_admission_events"
    )[0].replace(
        "    tenant_id TEXT NOT NULL,\n",
        "",
    ).replace(
        "    submitted_by TEXT NOT NULL,\n",
        "",
    ).replace(
        "    priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 9),\n",
        "",
    ).replace(
        "    idempotency_key TEXT,\n",
        "    idempotency_key TEXT UNIQUE,\n",
    ).replace(
        "CREATE INDEX IF NOT EXISTS idx_planning_jobs_schedule\n"
        "ON planning_jobs(status, priority, available_at, lease_expires_at, created_at);\n",
        "",
    ).replace(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_planning_jobs_tenant_idempotency\n"
        "ON planning_jobs(tenant_id, idempotency_key)\n"
        "WHERE idempotency_key IS NOT NULL;\n",
        "",
    )
    payload = json.dumps(_request().to_dict(), ensure_ascii=False)
    with sqlite3.connect(database) as connection:
        connection.executescript(schema_without_priority)
        connection.execute(
            """
            INSERT INTO planning_jobs(
                job_id, request_id, status, request_json, request_sha256,
                attempt, max_attempts, deadline_seconds, deadline_at, available_at,
                created_at, updated_at
            ) VALUES ('job-v57', 'req-v57', 'queued', ?, 'sha',
                      0, 3, 900, ?, ?, ?, ?)
            """,
            (
                payload,
                "2026-01-01T00:15:00.000Z",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO planning_job_events(
                job_id, event_type, attempt, worker_id, payload_json, created_at
            ) VALUES ('job-v57', 'submitted', 0, NULL, '{}', ?)
            """,
            ("2026-01-01T00:00:00.000Z",),
        )

    repository = PlanningJobRepository(database)
    migrated = repository.get("job-v57")

    assert migrated is not None and migrated.priority == 0
    assert migrated.tenant_id == "default"
    assert migrated.submitted_by == "legacy-migration"
    events = repository.list_events("job-v57")
    assert len(events) == 1 and events[0].event_id == 1
    with repository._connect() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(planning_jobs)")
        }
        assert {"priority", "tenant_id", "submitted_by"} <= columns
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v58_schema_migrates_identity_and_tenant_idempotency_without_losing_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v58.db"
    schema_v58 = job_repository_module.SCHEMA.split(
        "CREATE TABLE IF NOT EXISTS planning_job_admission_events"
    )[0].replace(
        "    tenant_id TEXT NOT NULL,\n",
        "",
    ).replace(
        "    submitted_by TEXT NOT NULL,\n",
        "",
    ).replace(
        "    idempotency_key TEXT,\n",
        "    idempotency_key TEXT UNIQUE,\n",
    ).replace(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_planning_jobs_tenant_idempotency\n"
        "ON planning_jobs(tenant_id, idempotency_key)\n"
        "WHERE idempotency_key IS NOT NULL;\n",
        "",
    )
    payload = json.dumps(_request().to_dict(), ensure_ascii=False)
    with sqlite3.connect(database) as connection:
        connection.executescript(schema_v58)
        connection.execute(
            """
            INSERT INTO planning_jobs(
                job_id, request_id, status, request_json, request_sha256,
                idempotency_key, attempt, max_attempts, priority,
                deadline_seconds, deadline_at, available_at, created_at, updated_at
            ) VALUES ('job-v58', 'req-v58', 'queued', ?, 'sha', 'tenant-local-key',
                      0, 3, 7, 900, ?, ?, ?, ?)
            """,
            (
                payload,
                "2026-01-01T00:15:00.000Z",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO planning_job_events(
                job_id, event_type, attempt, worker_id, payload_json, created_at
            ) VALUES ('job-v58', 'submitted', 0, NULL, '{}', ?)
            """,
            ("2026-01-01T00:00:00.000Z",),
        )

    repository = PlanningJobRepository(database)
    migrated = repository.get("job-v58")
    other_tenant = repository.submit(
        request_id="req-v59-other-tenant",
        request_payload=_request().to_dict(),
        tenant_id="tenant-beta",
        submitted_by="beta-submitter",
        idempotency_key="tenant-local-key",
        priority=7,
    )

    assert migrated is not None
    assert (migrated.priority, migrated.tenant_id, migrated.submitted_by) == (
        7,
        "default",
        "legacy-migration",
    )
    assert repository.list_events(migrated.job_id)[0].event_id == 1
    assert other_tenant.job_id != migrated.job_id
    with repository._connect() as connection:
        indexes = {
            row["name"] for row in connection.execute("PRAGMA index_list(planning_jobs)")
        }
        assert "idx_planning_jobs_tenant_idempotency" in indexes
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v59_schema_adds_admission_and_tenant_scheduler_state_without_rewriting_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v59.db"
    schema_v59 = job_repository_module.SCHEMA.split(
        "CREATE TABLE IF NOT EXISTS planning_job_admission_events"
    )[0]
    payload = json.dumps(_request().to_dict(), ensure_ascii=False)
    with sqlite3.connect(database) as connection:
        connection.executescript(schema_v59)
        connection.execute(
            """
            INSERT INTO planning_jobs(
                job_id, request_id, tenant_id, submitted_by, status,
                request_json, request_sha256, attempt, max_attempts, priority,
                deadline_seconds, deadline_at, available_at, created_at, updated_at
            ) VALUES ('job-v59', 'req-v59', 'tenant-alpha', 'alpha-admin',
                      'queued', ?, 'sha', 0, 3, 4, 900, ?, ?, ?, ?)
            """,
            (
                payload,
                "2026-01-01T00:15:00.000Z",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO planning_job_events(
                job_id, event_type, attempt, worker_id, payload_json, created_at
            ) VALUES ('job-v59', 'submitted', 0, NULL, '{}', ?)
            """,
            ("2026-01-01T00:00:00.000Z",),
        )

    repository = PlanningJobRepository(database)
    migrated = repository.get("job-v59")

    assert migrated is not None
    assert (migrated.tenant_id, migrated.submitted_by, migrated.priority) == (
        "tenant-alpha",
        "alpha-admin",
        4,
    )
    assert repository.list_events(migrated.job_id)[0].event_id == 1
    with repository._connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "planning_job_admission_events",
            "planning_tenant_scheduler_state",
        } <= tables
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_job_events_are_append_only(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    created = repository.submit(request_id="req-1", request_payload=_request().to_dict())
    event = repository.list_events(created.job_id)[0]

    with repository._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE planning_job_events SET event_type = 'failed' WHERE event_id = ?",
                (event.event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM planning_job_events WHERE event_id = ?",
                (event.event_id,),
            )


def test_job_event_replay_validates_cursor_and_limit(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    created = repository.submit(request_id="req-1", request_payload=_request().to_dict())

    with pytest.raises(ValueError, match="non-negative"):
        repository.list_events(created.job_id, after_event_id=-1)
    with pytest.raises(ValueError, match="between 1 and 1000"):
        repository.list_events(created.job_id, limit=1001)
