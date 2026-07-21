"""Fail-closed SQLite to PostgreSQL durable-job migration.

The migration is intentionally offline.  It holds a SQLite ``BEGIN IMMEDIATE``
transaction while a single PostgreSQL transaction copies and verifies the
domain.  SQLite is never deleted or rewritten.  The operator must stop API and
worker processes before apply; that attestation is recorded as a boundary, not
treated as independently proven quiescence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from storage.verified_copy import canonical_json, canonical_sha256, file_sha256

from .postgres_repository import (
    POSTGRES_SCHEMA_PATTERN,
    POSTGRES_WRITE_LOCK_KEY,
    PostgresPlanningJobRepository,
)


MIGRATION_ID = "planning_jobs_sqlite_to_postgres_v1"
JOB_STORE_LAYOUT_VERSION = "planning_job_store_v1"
MIGRATION_TABLE = "planning_job_store_migrations"


@dataclass(frozen=True)
class JobStoreTable:
    name: str
    logical_columns: tuple[str, ...]
    source_columns: tuple[str, ...]
    destination_columns: tuple[str, ...]
    order_columns: tuple[str, ...]


PLANNING_JOB_COLUMNS = (
    "job_id",
    "request_id",
    "tenant_id",
    "submitted_by",
    "status",
    "request_json",
    "request_sha256",
    "idempotency_key",
    "attempt",
    "max_attempts",
    "priority",
    "deadline_seconds",
    "deadline_at",
    "available_at",
    "created_at",
    "updated_at",
    "cancel_requested_at",
    "cancelled_at",
    "cancel_reason_code",
    "replayed_from_job_id",
    "lease_owner",
    "lease_expires_at",
    "artifact_id",
    "artifact_sha256",
    "result_json",
    "error_code",
    "error_message",
)
JOB_EVENT_COLUMNS = (
    "event_id",
    "job_id",
    "event_type",
    "attempt",
    "worker_id",
    "payload_json",
    "created_at",
)
ADMISSION_EVENT_COLUMNS = (
    "event_id",
    "policy_version",
    "tenant_id",
    "submitted_by",
    "request_id",
    "operation",
    "decision",
    "reason_code",
    "job_id",
    "idempotency_key_present",
    "active_jobs_before",
    "recent_submissions_before",
    "active_job_limit",
    "submission_limit_per_minute",
    "submission_window_seconds",
    "retry_after_seconds",
    "created_at",
)
SCHEDULER_STATE_COLUMNS = (
    "tenant_id",
    "last_claimed_event_id",
    "claim_count",
    "updated_at",
)


JOB_TABLES = (
    JobStoreTable(
        name="planning_jobs",
        logical_columns=("job_sequence",) + PLANNING_JOB_COLUMNS,
        source_columns=("rowid AS job_sequence",) + PLANNING_JOB_COLUMNS,
        destination_columns=("rowid",) + PLANNING_JOB_COLUMNS,
        order_columns=("job_sequence",),
    ),
    JobStoreTable(
        name="planning_job_events",
        logical_columns=JOB_EVENT_COLUMNS,
        source_columns=JOB_EVENT_COLUMNS,
        destination_columns=JOB_EVENT_COLUMNS,
        order_columns=("event_id",),
    ),
    JobStoreTable(
        name="planning_job_admission_events",
        logical_columns=ADMISSION_EVENT_COLUMNS,
        source_columns=ADMISSION_EVENT_COLUMNS,
        destination_columns=ADMISSION_EVENT_COLUMNS,
        order_columns=("event_id",),
    ),
    JobStoreTable(
        name="planning_tenant_scheduler_state",
        logical_columns=SCHEDULER_STATE_COLUMNS,
        source_columns=SCHEDULER_STATE_COLUMNS,
        destination_columns=SCHEDULER_STATE_COLUMNS,
        order_columns=("tenant_id",),
    ),
)


class JobStoreMigrationError(RuntimeError):
    """The migration cannot prove that applying or reverting is safe."""


def _row_values(row: Any, columns: tuple[str, ...]) -> list[Any]:
    return [row[column] for column in columns]


def _count_and_logical_digest(
    cursor: Any, columns: tuple[str, ...]
) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    while True:
        rows = cursor.fetchmany(1000)
        if not rows:
            break
        count += len(rows)
        for row in rows:
            digest.update(canonical_json(_row_values(row, columns)).encode("utf-8"))
            digest.update(b"\n")
    return count, digest.hexdigest()


def _query_for(table: JobStoreTable) -> str:
    selected = ", ".join(table.source_columns)
    return (
        f"SELECT {selected} FROM {table.name} "
        f"ORDER BY {', '.join(table.order_columns)}"
    )


def _sqlite_table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _validate_sqlite_source(connection: sqlite3.Connection) -> str:
    missing = {table.name for table in JOB_TABLES} - _sqlite_table_names(connection)
    if missing:
        raise JobStoreMigrationError(
            f"SQLite job store is missing current tables: {sorted(missing)}"
        )
    for table in JOB_TABLES:
        available = {
            str(row["name"])
            for row in connection.execute(
                f"PRAGMA table_info({table.name})"
            ).fetchall()
        }
        required = {
            expression.split(" AS ", maxsplit=1)[0]
            for expression in table.source_columns
            if not expression.startswith("rowid AS ")
        }
        if missing_columns := required - available:
            raise JobStoreMigrationError(
                f"SQLite {table.name} is missing current columns: "
                f"{sorted(missing_columns)}"
            )
    mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if mode == "wal":
        raise JobStoreMigrationError(
            "WAL source requires an explicit checkpointed backup before migration"
        )
    integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    if integrity != "ok":
        raise JobStoreMigrationError(f"SQLite quick_check failed: {integrity}")
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise JobStoreMigrationError("SQLite foreign_key_check failed")
    return mode


def _snapshot(connection: Any) -> dict[str, dict[str, Any]]:
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for table in JOB_TABLES:
        count, digest = _count_and_logical_digest(
            connection.execute(_query_for(table)), table.logical_columns
        )
        counts[table.name] = count
        digests[table.name] = digest
    return {"counts": counts, "digests": digests}


def inspect_sqlite_job_store(source: Path) -> dict[str, Any]:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    before_sha = file_sha256(source)
    with sqlite3.connect(source) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        journal_mode = _validate_sqlite_source(connection)
        current = _snapshot(connection)
        running_jobs = int(
            connection.execute(
                "SELECT COUNT(*) FROM planning_jobs WHERE status='running'"
            ).fetchone()[0]
        )
        connection.rollback()
    if file_sha256(source) != before_sha:
        raise JobStoreMigrationError("SQLite source changed during inspection")
    return {
        "layout_version": JOB_STORE_LAYOUT_VERSION,
        "source_name": source.name,
        "source_file_sha256": before_sha,
        "source_journal_mode": journal_mode,
        "running_jobs": running_jobs,
        **current,
    }


def _set_search_path(connection: Any, schema: str) -> None:
    from psycopg import sql

    connection.execute(
        sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema))
    )


def _postgres_schema_exists(connection: Any, schema: str) -> bool:
    row = connection.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s) AS present",
        (schema,),
    ).fetchone()
    return bool(row["present"])


def _postgres_domain_tables(connection: Any, schema: str) -> set[str]:
    return {
        str(row["table_name"])
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema=%s AND table_type='BASE TABLE'
            """,
            (schema,),
        ).fetchall()
    }


def _target_snapshot(connection: Any, schema: str) -> dict[str, Any]:
    if not _postgres_schema_exists(connection, schema):
        return {
            "schema_exists": False,
            "counts": {},
            "digests": {},
            "empty": True,
        }
    tables = _postgres_domain_tables(connection, schema)
    required = {table.name for table in JOB_TABLES}
    if not required <= tables:
        if tables & (required | {MIGRATION_TABLE}):
            raise JobStoreMigrationError(
                "PostgreSQL target has a partial durable-job schema"
            )
        return {
            "schema_exists": True,
            "counts": {},
            "digests": {},
            "empty": True,
        }
    _set_search_path(connection, schema)
    current = _snapshot(connection)
    receipt = _load_receipt(connection) if MIGRATION_TABLE in tables else None
    return {
        "schema_exists": True,
        **current,
        "empty": not any(current["counts"].values()),
        "receipt": receipt,
    }


def inspect_postgres_job_store(dsn: str, *, schema: str) -> dict[str, Any]:
    if not POSTGRES_SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("PostgreSQL schema must be a safe lowercase identifier")
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            dsn,
            row_factory=dict_row,
            connect_timeout=5,
        ) as connection:
            return _target_snapshot(connection, schema)
    except JobStoreMigrationError:
        raise
    except Exception as exc:
        raise JobStoreMigrationError(
            "PostgreSQL target inspection failed"
        ) from exc


def preview_job_store_migration(
    *, source: Path, dsn: str, schema: str
) -> dict[str, Any]:
    source_state = inspect_sqlite_job_store(source)
    target_state = inspect_postgres_job_store(dsn, schema=schema)
    receipt = target_state.get("receipt")
    existing_migration_matches = bool(
        receipt
        and receipt["source_file_sha256"] == source_state["source_file_sha256"]
        and receipt["source_counts"] == source_state["counts"]
        and receipt["source_digests"] == source_state["digests"]
        and receipt["destination_counts"] == target_state["counts"]
        and receipt["destination_digests"] == target_state["digests"]
    )
    preview = {
        "contract_version": MIGRATION_ID,
        "layout_version": JOB_STORE_LAYOUT_VERSION,
        "mode": "dry_run",
        "source_name": source_state["source_name"],
        "source_journal_mode": source_state["source_journal_mode"],
        "source_counts": source_state["counts"],
        "source_digests": source_state["digests"],
        "running_jobs": source_state["running_jobs"],
        "target_backend": "postgres",
        "target_schema": schema,
        "target_schema_exists": target_state["schema_exists"],
        "target_counts": target_state["counts"],
        "target_digests": target_state["digests"],
        "target_empty": target_state["empty"],
        "existing_receipt_present": receipt is not None,
        "existing_migration_matches": existing_migration_matches,
        "ready_to_apply": (
            source_state["running_jobs"] == 0
            and (target_state["empty"] or existing_migration_matches)
        ),
    }
    preview["preview_sha256"] = canonical_sha256(preview)
    return preview


def _receipt_body(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "migration_id": row["migration_id"],
        "layout_version": row["layout_version"],
        "source_name": row["source_name"],
        "source_file_sha256": row["source_file_sha256"],
        "source_counts": json.loads(row["source_counts_json"]),
        "source_digests": json.loads(row["source_digests_json"]),
        "destination_counts": json.loads(row["destination_counts_json"]),
        "destination_digests": json.loads(row["destination_digests_json"]),
        "recorded_at": row["recorded_at"],
    }


def _load_receipt(connection: Any) -> dict[str, Any] | None:
    row = connection.execute(
        f"SELECT * FROM {MIGRATION_TABLE} WHERE migration_id=%s",
        (MIGRATION_ID,),
    ).fetchone()
    if row is None:
        return None
    body = _receipt_body(row)
    if row["receipt_sha256"] != canonical_sha256(body):
        raise JobStoreMigrationError("PostgreSQL migration receipt is invalid")
    return {**body, "receipt_sha256": row["receipt_sha256"]}


def _insert_receipt(
    connection: Any,
    *,
    source_name: str,
    source_file_sha256: str,
    source_snapshot: Mapping[str, Mapping[str, Any]],
    destination_snapshot: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    body = {
        "migration_id": MIGRATION_ID,
        "layout_version": JOB_STORE_LAYOUT_VERSION,
        "source_name": source_name,
        "source_file_sha256": source_file_sha256,
        "source_counts": source_snapshot["counts"],
        "source_digests": source_snapshot["digests"],
        "destination_counts": destination_snapshot["counts"],
        "destination_digests": destination_snapshot["digests"],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_sha = canonical_sha256(body)
    connection.execute(
        f"""
        INSERT INTO {MIGRATION_TABLE}(
            migration_id, layout_version, source_name, source_file_sha256,
            source_counts_json, source_digests_json,
            destination_counts_json, destination_digests_json,
            recorded_at, receipt_sha256
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            body["migration_id"],
            body["layout_version"],
            body["source_name"],
            body["source_file_sha256"],
            canonical_json(body["source_counts"]),
            canonical_json(body["source_digests"]),
            canonical_json(body["destination_counts"]),
            canonical_json(body["destination_digests"]),
            body["recorded_at"],
            receipt_sha,
        ),
    )
    return {**body, "receipt_sha256": receipt_sha}


def _copy_table(
    source_connection: sqlite3.Connection,
    destination_connection: Any,
    table: JobStoreTable,
) -> None:
    from psycopg import sql

    source_cursor = source_connection.execute(_query_for(table))
    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table.name),
        sql.SQL(", ").join(map(sql.Identifier, table.destination_columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in table.destination_columns),
    )
    with destination_connection.cursor() as cursor:
        while True:
            rows = source_cursor.fetchmany(1000)
            if not rows:
                break
            cursor.executemany(
                insert,
                [tuple(row[column] for column in table.logical_columns) for row in rows],
            )


def _reset_postgres_sequences(connection: Any) -> None:
    connection.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('planning_jobs', 'rowid'),
            COALESCE((SELECT MAX(rowid) FROM planning_jobs), 1),
            EXISTS(SELECT 1 FROM planning_jobs)
        )
        """
    )
    for table in ("planning_job_events", "planning_job_admission_events"):
        connection.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table}', 'event_id'),
                COALESCE((SELECT MAX(event_id) FROM {table}), 1),
                EXISTS(SELECT 1 FROM {table})
            )
            """
        )


def migrate_job_store(
    *,
    source: Path,
    dsn: str,
    schema: str,
    apply: bool = False,
    confirm_source_quiesced: bool = False,
) -> dict[str, Any]:
    """Copy a consistent source snapshot or return a payload-minimized preview."""
    if not apply:
        return preview_job_store_migration(source=source, dsn=dsn, schema=schema)
    if not confirm_source_quiesced:
        raise JobStoreMigrationError(
            "apply requires explicit source-quiesced confirmation"
        )
    source = Path(source)
    inspected = inspect_sqlite_job_store(source)
    if inspected["running_jobs"]:
        raise JobStoreMigrationError("running jobs must settle before migration")

    repository = PostgresPlanningJobRepository(dsn, schema=schema)
    before_sha = inspected["source_file_sha256"]
    with sqlite3.connect(source, timeout=5) as source_connection:
        source_connection.row_factory = sqlite3.Row
        source_connection.execute("PRAGMA busy_timeout=5000")
        source_connection.execute("PRAGMA foreign_keys=ON")
        source_connection.execute("BEGIN IMMEDIATE")
        try:
            journal_mode = _validate_sqlite_source(source_connection)
            if journal_mode != inspected["source_journal_mode"]:
                raise JobStoreMigrationError(
                    "SQLite journal mode changed between preflight and apply"
                )
            stable_source = _snapshot(source_connection)
            expected_source = {
                "counts": inspected["counts"],
                "digests": inspected["digests"],
            }
            if stable_source != expected_source or file_sha256(source) != before_sha:
                raise JobStoreMigrationError(
                    "SQLite source changed between preflight and locked copy"
                )
            running_jobs = int(
                source_connection.execute(
                    "SELECT COUNT(*) FROM planning_jobs WHERE status='running'"
                ).fetchone()[0]
            )
            if running_jobs:
                raise JobStoreMigrationError("running jobs must settle before migration")

            with repository._raw_connect() as destination_connection:
                _set_search_path(destination_connection, schema)
                destination_connection.commit()
                destination_connection.execute("BEGIN")
                destination_connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (POSTGRES_WRITE_LOCK_KEY,),
                )
                receipt = _load_receipt(destination_connection)
                current_target = _snapshot(destination_connection)
                if receipt is not None:
                    receipt_source = {
                        "counts": receipt["source_counts"],
                        "digests": receipt["source_digests"],
                    }
                    receipt_destination = {
                        "counts": receipt["destination_counts"],
                        "digests": receipt["destination_digests"],
                    }
                    if (
                        receipt_source != stable_source
                        or receipt_destination != current_target
                        or receipt["source_file_sha256"] != before_sha
                    ):
                        raise JobStoreMigrationError(
                            "existing migration receipt does not match current stores"
                        )
                    return {
                        "contract_version": MIGRATION_ID,
                        "layout_version": JOB_STORE_LAYOUT_VERSION,
                        "mode": "apply",
                        "already_applied": True,
                        "source_name": source.name,
                        "source_counts": stable_source["counts"],
                        "source_digests": stable_source["digests"],
                        "destination_counts": current_target["counts"],
                        "destination_digests": current_target["digests"],
                        "source_modified": False,
                        "receipt_sha256": receipt["receipt_sha256"],
                        "rollback_safe_at_cutover": True,
                    }
                if any(current_target["counts"].values()):
                    raise JobStoreMigrationError(
                        "PostgreSQL target must be empty before first migration"
                    )

                for table in JOB_TABLES:
                    _copy_table(source_connection, destination_connection, table)
                _reset_postgres_sequences(destination_connection)
                destination_snapshot = _snapshot(destination_connection)
                if destination_snapshot != stable_source:
                    raise JobStoreMigrationError(
                        "PostgreSQL counts or logical hashes do not match source"
                    )
                receipt = _insert_receipt(
                    destination_connection,
                    source_name=source.name,
                    source_file_sha256=before_sha,
                    source_snapshot=stable_source,
                    destination_snapshot=destination_snapshot,
                )
                if file_sha256(source) != before_sha:
                    raise JobStoreMigrationError("SQLite source bytes changed during copy")
                destination_connection.commit()
        finally:
            source_connection.rollback()

    if file_sha256(source) != before_sha:
        raise JobStoreMigrationError("SQLite source bytes changed during migration")
    result = {
        "contract_version": MIGRATION_ID,
        "layout_version": JOB_STORE_LAYOUT_VERSION,
        "mode": "apply",
        "already_applied": False,
        "source_name": source.name,
        "source_counts": stable_source["counts"],
        "source_digests": stable_source["digests"],
        "destination_counts": destination_snapshot["counts"],
        "destination_digests": destination_snapshot["digests"],
        "source_modified": False,
        "receipt_sha256": receipt["receipt_sha256"],
        "rollback_safe_at_cutover": True,
    }
    result["migration_sha256"] = canonical_sha256(result)
    return result


def verify_job_store_cutover(
    *, source: Path, dsn: str, schema: str
) -> dict[str, Any]:
    """Verify the receipt and state whether direct config rollback is still safe."""
    if not POSTGRES_SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("PostgreSQL schema must be a safe lowercase identifier")
    source_state = inspect_sqlite_job_store(source)
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            dsn,
            row_factory=dict_row,
            connect_timeout=5,
        ) as connection:
            if not _postgres_schema_exists(connection, schema):
                raise JobStoreMigrationError("PostgreSQL migration schema is missing")
            tables = _postgres_domain_tables(connection, schema)
            required = {table.name for table in JOB_TABLES} | {MIGRATION_TABLE}
            if not required <= tables:
                raise JobStoreMigrationError(
                    "PostgreSQL migration schema is incomplete"
                )
            _set_search_path(connection, schema)
            receipt = _load_receipt(connection)
            if receipt is None:
                raise JobStoreMigrationError("PostgreSQL migration receipt is missing")
            target = _snapshot(connection)
    except JobStoreMigrationError:
        raise
    except Exception as exc:
        raise JobStoreMigrationError(
            "PostgreSQL cutover verification failed"
        ) from exc
    source_matches = (
        source_state["source_file_sha256"] == receipt["source_file_sha256"]
        and source_state["counts"] == receipt["source_counts"]
        and source_state["digests"] == receipt["source_digests"]
    )
    target_matches = (
        target["counts"] == receipt["destination_counts"]
        and target["digests"] == receipt["destination_digests"]
    )
    result = {
        "contract_version": MIGRATION_ID,
        "layout_version": JOB_STORE_LAYOUT_VERSION,
        "mode": "verify_cutover",
        "source_name": source_state["source_name"],
        "target_backend": "postgres",
        "target_schema": schema,
        "receipt_valid": True,
        "receipt_sha256": receipt["receipt_sha256"],
        "source_matches_migration": source_matches,
        "target_matches_migration": target_matches,
        "rollback_safe": source_matches and target_matches,
        "rollback_reason": (
            "stores_unchanged_since_cutover"
            if source_matches and target_matches
            else "store_drift_requires_forward_reconciliation"
        ),
    }
    result["verification_sha256"] = canonical_sha256(result)
    return result
