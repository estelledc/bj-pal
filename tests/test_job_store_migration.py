from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs.migration import (  # noqa: E402
    MIGRATION_ID,
    JobStoreMigrationError,
    inspect_postgres_job_store,
    migrate_job_store,
    verify_job_store_cutover,
)
from jobs.postgres_repository import PostgresPlanningJobRepository  # noqa: E402
from jobs.repository import PlanningJobRepository  # noqa: E402
from storage.verified_copy import file_sha256  # noqa: E402


POSTGRES_TEST_DSN_ENV = "BJ_PAL_TEST_POSTGRES_DSN"


@pytest.fixture()
def postgres_target():
    dsn = os.environ.get(POSTGRES_TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"{POSTGRES_TEST_DSN_ENV} is not configured")
    schema = f"bjpal_migration_{uuid.uuid4().hex}"
    try:
        yield dsn, schema
    finally:
        import psycopg
        from psycopg import sql

        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )


def _build_source(path: Path) -> PlanningJobRepository:
    repository = PlanningJobRepository(path)
    succeeded = repository.submit(
        request_id="req-succeeded",
        request_payload={"user_input": "migration succeeded"},
        tenant_id="tenant-a",
        submitted_by="migration-test",
        idempotency_key="idem-succeeded",
        tenant_active_job_limit=10,
        tenant_submission_limit_per_minute=100,
    )
    claimed = repository.claim_next(worker_id="worker-success")
    assert claimed is not None and claimed.job_id == succeeded.job_id
    repository.succeed(
        job_id=succeeded.job_id,
        worker_id="worker-success",
        result_payload={"ok": True},
    )

    failed = repository.submit(
        request_id="req-failed",
        request_payload={"user_input": "migration failed"},
        tenant_id="tenant-b",
        submitted_by="migration-test",
        idempotency_key="idem-failed",
        tenant_active_job_limit=10,
        tenant_submission_limit_per_minute=100,
    )
    claimed = repository.claim_next(worker_id="worker-failed")
    assert claimed is not None and claimed.job_id == failed.job_id
    repository.fail(
        job_id=failed.job_id,
        worker_id="worker-failed",
        error_code="synthetic_failure",
        error_message="bounded test failure",
    )

    repository.submit(
        request_id="req-queued",
        request_payload={"user_input": "migration queued"},
        tenant_id="tenant-a",
        submitted_by="migration-test",
        idempotency_key="idem-queued",
        priority=4,
        tenant_active_job_limit=10,
        tenant_submission_limit_per_minute=100,
    )
    return repository


def test_migration_preserves_logical_state_order_and_receipt(
    tmp_path: Path,
    postgres_target,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "planning-jobs.db"
    source = _build_source(source_path)
    source_sha = file_sha256(source_path)
    source_ids = [item.job_id for item in source.list_jobs(limit=100)]

    preview = migrate_job_store(source=source_path, dsn=dsn, schema=schema)
    assert preview["mode"] == "dry_run"
    assert preview["ready_to_apply"] is True
    assert preview["target_empty"] is True
    assert preview["running_jobs"] == 0

    migrated = migrate_job_store(
        source=source_path,
        dsn=dsn,
        schema=schema,
        apply=True,
        confirm_source_quiesced=True,
    )
    assert migrated["contract_version"] == MIGRATION_ID
    assert migrated["already_applied"] is False
    assert migrated["source_counts"] == migrated["destination_counts"]
    assert migrated["source_digests"] == migrated["destination_digests"]
    assert migrated["source_modified"] is False
    assert file_sha256(source_path) == source_sha

    repeated = migrate_job_store(
        source=source_path,
        dsn=dsn,
        schema=schema,
        apply=True,
        confirm_source_quiesced=True,
    )
    assert repeated["already_applied"] is True
    assert repeated["receipt_sha256"] == migrated["receipt_sha256"]
    repeated_preview = migrate_job_store(
        source=source_path,
        dsn=dsn,
        schema=schema,
    )
    assert repeated_preview["existing_receipt_present"] is True
    assert repeated_preview["existing_migration_matches"] is True
    assert repeated_preview["ready_to_apply"] is True

    cutover = verify_job_store_cutover(
        source=source_path,
        dsn=dsn,
        schema=schema,
    )
    assert cutover["receipt_valid"] is True
    assert cutover["rollback_safe"] is True

    target = PostgresPlanningJobRepository(dsn, schema=schema)
    assert [item.job_id for item in target.list_jobs(limit=100)] == source_ids
    queued = target.claim_next(worker_id="post-cutover-worker")
    assert queued is not None and queued.status == "running"
    target.succeed(
        job_id=queued.job_id,
        worker_id="post-cutover-worker",
        result_payload={"post_cutover": True},
    )
    new_job = target.submit(
        request_id="req-after-cutover",
        request_payload={"user_input": "new target-owned state"},
        tenant_id="tenant-c",
        submitted_by="migration-test",
        idempotency_key="idem-after-cutover",
    )
    assert target.list_jobs(limit=100)[-1].job_id == new_job.job_id
    assert source.get(queued.job_id).status == "queued"

    drifted = verify_job_store_cutover(
        source=source_path,
        dsn=dsn,
        schema=schema,
    )
    assert drifted["target_matches_migration"] is False
    assert drifted["rollback_safe"] is False
    assert drifted["rollback_reason"] == "store_drift_requires_forward_reconciliation"


def test_running_job_fails_before_target_creation(
    tmp_path: Path,
    postgres_target,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "running.db"
    source = PlanningJobRepository(source_path)
    submitted = source.submit(
        request_id="req-running",
        request_payload={"user_input": "running"},
    )
    assert source.claim_next(worker_id="worker").job_id == submitted.job_id

    with pytest.raises(JobStoreMigrationError, match="running jobs"):
        migrate_job_store(
            source=source_path,
            dsn=dsn,
            schema=schema,
            apply=True,
            confirm_source_quiesced=True,
        )
    inspected = inspect_postgres_job_store(dsn, schema=schema)
    assert inspected["schema_exists"] is False


def test_wal_source_requires_checkpointed_backup_workflow(
    tmp_path: Path,
    postgres_target,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "wal.db"
    _build_source(source_path)
    with sqlite3.connect(source_path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"

    with pytest.raises(JobStoreMigrationError, match="checkpointed backup"):
        migrate_job_store(
            source=source_path,
            dsn=dsn,
            schema=schema,
            apply=True,
            confirm_source_quiesced=True,
        )
    inspected = inspect_postgres_job_store(dsn, schema=schema)
    assert inspected["schema_exists"] is False


def test_nonempty_target_fails_without_changing_either_store(
    tmp_path: Path,
    postgres_target,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "source.db"
    _build_source(source_path)
    source_sha = file_sha256(source_path)
    target = PostgresPlanningJobRepository(dsn, schema=schema)
    existing = target.submit(
        request_id="req-existing",
        request_payload={"user_input": "target already owns state"},
    )

    with pytest.raises(JobStoreMigrationError, match="target must be empty"):
        migrate_job_store(
            source=source_path,
            dsn=dsn,
            schema=schema,
            apply=True,
            confirm_source_quiesced=True,
        )
    assert file_sha256(source_path) == source_sha
    assert target.get(existing.job_id) is not None


def test_injected_copy_failure_rolls_back_all_target_rows(
    tmp_path: Path,
    postgres_target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "source.db"
    _build_source(source_path)
    source_sha = file_sha256(source_path)

    import jobs.migration as migration_module

    original_copy = migration_module._copy_table

    def failing_copy(source_connection, destination_connection, table) -> None:
        original_copy(source_connection, destination_connection, table)
        if table.name == "planning_job_events":
            raise JobStoreMigrationError("injected failure after events")

    monkeypatch.setattr(migration_module, "_copy_table", failing_copy)
    with pytest.raises(JobStoreMigrationError, match="injected failure"):
        migrate_job_store(
            source=source_path,
            dsn=dsn,
            schema=schema,
            apply=True,
            confirm_source_quiesced=True,
        )

    target = inspect_postgres_job_store(dsn, schema=schema)
    assert target["empty"] is True
    assert not any(target["counts"].values())
    assert file_sha256(source_path) == source_sha

    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        connection.execute(
            sql.SQL("SET search_path TO {}, pg_catalog").format(
                sql.Identifier(schema)
            )
        )
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM planning_job_store_migrations"
        ).fetchone()
        assert int(row["count"]) == 0


def test_migration_receipt_is_append_only(
    tmp_path: Path,
    postgres_target,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "source.db"
    _build_source(source_path)
    migrate_job_store(
        source=source_path,
        dsn=dsn,
        schema=schema,
        apply=True,
        confirm_source_quiesced=True,
    )

    import psycopg
    from psycopg import sql

    with psycopg.connect(dsn) as connection:
        connection.execute(
            sql.SQL("SET search_path TO {}, pg_catalog").format(
                sql.Identifier(schema)
            )
        )
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            connection.execute(
                "UPDATE planning_job_store_migrations SET source_name='tampered'"
            )


def test_apply_requires_explicit_quiescence_confirmation(
    tmp_path: Path,
    postgres_target,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "source.db"
    _build_source(source_path)
    with pytest.raises(JobStoreMigrationError, match="source-quiesced"):
        migrate_job_store(
            source=source_path,
            dsn=dsn,
            schema=schema,
            apply=True,
        )


def test_locked_copy_rejects_a_concurrent_sqlite_writer(
    tmp_path: Path,
    postgres_target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "source.db"
    _build_source(source_path)
    import jobs.migration as migration_module

    original_copy = migration_module._copy_table
    copy_started = threading.Event()
    allow_copy = threading.Event()

    def paused_copy(source_connection, destination_connection, table) -> None:
        if table.name == "planning_jobs":
            copy_started.set()
            assert allow_copy.wait(timeout=5)
        original_copy(source_connection, destination_connection, table)

    monkeypatch.setattr(migration_module, "_copy_table", paused_copy)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            migrate_job_store,
            source=source_path,
            dsn=dsn,
            schema=schema,
            apply=True,
            confirm_source_quiesced=True,
        )
        assert copy_started.wait(timeout=5)
        try:
            with sqlite3.connect(source_path, timeout=0.1) as concurrent:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    concurrent.execute("BEGIN IMMEDIATE")
        finally:
            allow_copy.set()
        assert future.result(timeout=10)["source_modified"] is False


def test_cli_apply_requires_two_explicit_confirmations(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("BJ_PAL_JOB_POSTGRES_DSN", None)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "migrate_planning_jobs.py"),
            "--source",
            str(tmp_path / "missing.db"),
            "--apply",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--confirm-cutover planning-jobs-sqlite-to-postgres" in result.stderr
    assert "postgresql://" not in result.stdout + result.stderr


def test_cli_connection_failure_does_not_echo_dsn_secret(tmp_path: Path) -> None:
    source_path = tmp_path / "source.db"
    _build_source(source_path)
    environment = dict(os.environ)
    environment["BJ_PAL_JOB_POSTGRES_DSN"] = (
        "postgresql://migration-user:do-not-echo-this@127.0.0.1:1/bj_pal"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "migrate_planning_jobs.py"),
            "--source",
            str(source_path),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "PostgreSQL target inspection failed" in result.stderr
    assert "do-not-echo-this" not in result.stdout + result.stderr


def test_verify_missing_target_is_read_only(
    tmp_path: Path,
    postgres_target,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "source.db"
    _build_source(source_path)
    with pytest.raises(JobStoreMigrationError, match="schema is missing"):
        verify_job_store_cutover(
            source=source_path,
            dsn=dsn,
            schema=schema,
        )
    assert inspect_postgres_job_store(dsn, schema=schema)["schema_exists"] is False


def test_cli_dry_run_apply_and_verify_cutover(
    tmp_path: Path,
    postgres_target,
) -> None:
    dsn, schema = postgres_target
    source_path = tmp_path / "source.db"
    _build_source(source_path)
    environment = {
        **os.environ,
        "BJ_PAL_JOB_POSTGRES_DSN": dsn,
        "BJ_PAL_JOB_POSTGRES_SCHEMA": schema,
    }
    base_command = [
        sys.executable,
        str(ROOT / "scripts" / "migrate_planning_jobs.py"),
        "--source",
        str(source_path),
        "--schema",
        schema,
    ]

    dry_run = subprocess.run(
        base_command,
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(dry_run.stdout)["ready_to_apply"] is True

    applied = subprocess.run(
        [
            *base_command,
            "--apply",
            "--confirm-cutover",
            "planning-jobs-sqlite-to-postgres",
            "--confirm-source-quiesced",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(applied.stdout)["source_modified"] is False

    verified = subprocess.run(
        [*base_command, "--verify-cutover"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(verified.stdout)["rollback_safe"] is True
    combined_output = dry_run.stdout + applied.stdout + verified.stdout
    assert dsn not in combined_output
