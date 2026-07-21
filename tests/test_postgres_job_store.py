from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta
import os
import sys
import time
from pathlib import Path
import uuid

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs import (  # noqa: E402
    IdempotencyConflict,
    JobStoreUnavailable,
    TenantAdmissionRejected,
)
from jobs.postgres_repository import PostgresPlanningJobRepository  # noqa: E402
from jobs.service import PlanningJobService  # noqa: E402


POSTGRES_TEST_DSN_ENV = "BJ_PAL_TEST_POSTGRES_DSN"


@pytest.fixture()
def postgres_store():
    dsn = os.environ.get(POSTGRES_TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"{POSTGRES_TEST_DSN_ENV} is not configured")
    schema = f"bjpal_test_{uuid.uuid4().hex}"
    repository = PostgresPlanningJobRepository(dsn, schema=schema)
    try:
        yield repository
    finally:
        import psycopg
        from psycopg import sql

        repository.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )


def _submit(
    repository: PostgresPlanningJobRepository,
    index: int,
    *,
    tenant_id: str = "tenant-a",
    active_limit: int | None = None,
    submission_limit: int | None = None,
):
    return repository.submit(
        request_id=f"req-{index}",
        request_payload={"user_input": f"postgres acceptance {index}"},
        tenant_id=tenant_id,
        submitted_by="integration-test",
        idempotency_key=f"idem-{index}",
        tenant_active_job_limit=active_limit,
        tenant_submission_limit_per_minute=submission_limit,
    )


def _drain_in_process(dsn: str, schema: str, worker_index: int) -> list[str]:
    repository = PostgresPlanningJobRepository(dsn, schema=schema)
    claimed_ids: list[str] = []
    try:
        while True:
            job = repository.claim_next(
                worker_id=f"process-worker-{worker_index}",
                lease_seconds=30,
            )
            if job is None:
                return claimed_ids
            claimed_ids.append(job.job_id)
            repository.succeed(
                job_id=job.job_id,
                worker_id=f"process-worker-{worker_index}",
                result_payload={"worker_process": worker_index},
            )
    finally:
        repository.close()


def test_postgres_lifecycle_idempotency_fencing_and_append_only(postgres_store) -> None:
    repository = postgres_store
    assert PlanningJobService(repository=repository).probe() is True
    job = _submit(repository, 1)
    reused = repository.submit(
        request_id="req-retry",
        request_payload={"user_input": "postgres acceptance 1"},
        tenant_id="tenant-a",
        submitted_by="integration-test",
        idempotency_key="idem-1",
    )
    assert reused.job_id == job.job_id
    with pytest.raises(IdempotencyConflict):
        repository.submit(
            request_id="req-conflict",
            request_payload={"user_input": "different"},
            tenant_id="tenant-a",
            submitted_by="integration-test",
            idempotency_key="idem-1",
        )

    claimed = repository.claim_next(worker_id="worker-old", lease_seconds=1)
    assert claimed is not None and claimed.job_id == job.job_id
    assert repository.claim_next(worker_id="worker-other") is None
    time.sleep(1.05)
    reclaimed = repository.claim_next(worker_id="worker-new", lease_seconds=30)
    assert reclaimed is not None and reclaimed.job_id == job.job_id
    assert reclaimed.attempt == 2
    with pytest.raises(RuntimeError, match="lease"):
        repository.succeed(
            job_id=job.job_id,
            worker_id="worker-old",
            result_payload={"stale": True},
        )
    finished = repository.succeed(
        job_id=job.job_id,
        worker_id="worker-new",
        result_payload={"ok": True},
    )
    assert finished.status == "succeeded"
    assert [event.event_type for event in repository.list_events(job.job_id)] == [
        "submitted",
        "claimed",
        "lease_reclaimed",
        "succeeded",
    ]

    import psycopg
    from psycopg import sql

    with psycopg.connect(repository._dsn) as connection:
        connection.execute(
            sql.SQL("SET search_path TO {}, pg_catalog").format(
                sql.Identifier(repository.schema)
            )
        )
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            connection.execute(
                "UPDATE planning_job_events SET worker_id = 'tampered' WHERE job_id = %s",
                (job.job_id,),
            )


def test_postgres_cross_process_workers_claim_each_job_once(postgres_store) -> None:
    repository = postgres_store
    submitted = [_submit(repository, index) for index in range(12)]

    with ProcessPoolExecutor(max_workers=4) as executor:
        claims = [
            job_id
            for worker_claims in executor.map(
                _drain_in_process,
                [repository._dsn] * 4,
                [repository.schema] * 4,
                range(4),
            )
            for job_id in worker_claims
        ]

    expected = {job.job_id for job in submitted}
    assert len(claims) == len(expected)
    assert set(claims) == expected
    assert len(claims) == len(set(claims))
    assert {repository.get(job_id).status for job_id in expected} == {"succeeded"}
    assert all(
        [event.event_type for event in repository.list_events(job_id)]
        == ["submitted", "claimed", "succeeded"]
        for job_id in expected
    )


def test_postgres_admission_limit_is_global_across_connections(postgres_store) -> None:
    repository = postgres_store

    def attempt(index: int) -> str:
        independent = PostgresPlanningJobRepository(
            repository._dsn,
            schema=repository.schema,
        )
        try:
            _submit(
                independent,
                index,
                tenant_id="limited-tenant",
                active_limit=3,
                submission_limit=100,
            )
            return "admitted"
        except TenantAdmissionRejected as exc:
            assert exc.code == "tenant_active_job_limit_exceeded"
            return "rejected"
        finally:
            independent.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(8)))

    assert outcomes.count("admitted") == 3
    assert outcomes.count("rejected") == 5
    assert len(repository.list_jobs(tenant_id="limited-tenant")) == 3
    events = repository.list_admission_events(
        tenant_id="limited-tenant",
        limit=100,
    )
    assert [event.decision for event in events].count("admitted") == 3
    assert [event.decision for event in events].count("rejected") == 5


def test_postgres_retry_replay_and_workload_evidence_match_service_contract(
    postgres_store,
) -> None:
    repository = postgres_store
    original = repository.submit(
        request_id="req-retry-source",
        request_payload={"user_input": "retry me"},
        tenant_id="tenant-replay",
        submitted_by="integration-test",
        idempotency_key="retry-source",
        max_attempts=1,
    )
    claimed = repository.claim_next(worker_id="worker-fail", lease_seconds=30)
    assert claimed is not None
    failed = repository.retry_or_dead_letter(
        job_id=original.job_id,
        worker_id="worker-fail",
        error_code="synthetic_failure",
        error_message="Synthetic PostgreSQL acceptance failure.",
        backoff_seconds=0,
    )
    assert failed.status == "dead_lettered"
    replayed = repository.replay(
        job_id=original.job_id,
        request_id="req-replayed",
        idempotency_key="replay-once",
        tenant_id="tenant-replay",
        submitted_by="integration-test",
    )
    assert replayed.replayed_from_job_id == original.job_id

    jobs = repository.list_jobs(tenant_id="tenant-replay", limit=10)
    assert [job.job_id for job in jobs] == [original.job_id, replayed.job_id]
    created_at = datetime.fromisoformat(original.created_at.replace("Z", "+00:00"))
    window_start = (created_at - timedelta(seconds=1)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    window_end = (created_at + timedelta(days=1)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    evidence = repository.workload_evidence(
        tenant_id="tenant-replay",
        window_start=window_start,
        window_end=window_end,
    )
    assert [item.job_id for item in evidence] == [original.job_id, replayed.job_id]
    assert [item.status for item in evidence] == ["dead_lettered", "queued"]


def test_postgres_pool_fails_closed_at_capacity_then_recovers(postgres_store) -> None:
    bounded = PostgresPlanningJobRepository(
        postgres_store._dsn,
        schema=postgres_store.schema,
        pool_min_size=1,
        pool_max_size=1,
        pool_timeout_seconds=0.05,
        pool_max_waiting=1,
    )
    try:
        with bounded._connect():
            started = time.monotonic()
            with pytest.raises(
                JobStoreUnavailable,
                match="connection acquisition timed out",
            ) as failure:
                bounded.probe()
            elapsed = time.monotonic() - started

        assert elapsed >= 0.04
        assert bounded._dsn not in str(failure.value)
        assert bounded.probe() is True
        assert bounded.pool_stats()["requests_errors"] >= 1
    finally:
        bounded.close()


def test_postgres_pool_rejects_excess_waiters_without_exposing_dsn(
    postgres_store,
) -> None:
    bounded = PostgresPlanningJobRepository(
        postgres_store._dsn,
        schema=postgres_store.schema,
        pool_min_size=1,
        pool_max_size=1,
        pool_timeout_seconds=0.2,
        pool_max_waiting=1,
    )
    try:
        with bounded._connect():
            with ThreadPoolExecutor(max_workers=1) as executor:
                waiting = executor.submit(bounded.probe)
                deadline = time.monotonic() + 0.2
                while (
                    bounded.pool_stats().get("requests_waiting", 0) < 1
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.001)
                with pytest.raises(
                    JobStoreUnavailable,
                    match="connection queue is full",
                ) as failure:
                    bounded.probe()
                with pytest.raises(
                    JobStoreUnavailable,
                    match="connection acquisition timed out",
                ):
                    waiting.result()
        assert bounded._dsn not in str(failure.value)
    finally:
        bounded.close()


def test_postgres_pool_replaces_terminated_connection(postgres_store) -> None:
    import psycopg

    bounded = PostgresPlanningJobRepository(
        postgres_store._dsn,
        schema=postgres_store.schema,
        pool_min_size=1,
        pool_max_size=1,
        pool_timeout_seconds=1.0,
        pool_max_waiting=1,
    )
    try:
        with bounded._connect() as pooled:
            original_pid = int(
                pooled.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
            )
        with psycopg.connect(postgres_store._dsn, autocommit=True) as control:
            terminated = control.execute(
                "SELECT pg_terminate_backend(%s) AS terminated",
                (original_pid,),
            ).fetchone()[0]
        assert terminated is True

        assert bounded.probe() is True
        with bounded._connect() as pooled:
            replacement_pid = int(
                pooled.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
            )
        assert replacement_pid != original_pid
        assert bounded.pool_stats()["connections_lost"] >= 1
    finally:
        bounded.close()


def test_postgres_pool_close_rejects_new_work_without_leaking_sessions(
    postgres_store,
) -> None:
    import psycopg

    with psycopg.connect(postgres_store._dsn) as control:
        baseline = control.execute(
            """
            SELECT COUNT(*)
            FROM pg_stat_activity
            WHERE application_name = 'bj-pal-job-store'
            """
        ).fetchone()[0]
    bounded = PostgresPlanningJobRepository(
        postgres_store._dsn,
        schema=postgres_store.schema,
        pool_min_size=2,
        pool_max_size=2,
    )
    assert bounded.probe() is True
    bounded.close()
    assert bounded.closed is True
    with pytest.raises(JobStoreUnavailable, match="connection pool is closed"):
        bounded.probe()

    with psycopg.connect(postgres_store._dsn) as control:
        active = control.execute(
            """
            SELECT COUNT(*)
            FROM pg_stat_activity
            WHERE application_name = 'bj-pal-job-store'
            """
        ).fetchone()[0]
    assert active == baseline
