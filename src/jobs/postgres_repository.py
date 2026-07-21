"""PostgreSQL durable-job repository with cross-process fencing.

The domain transitions intentionally reuse the SQLite implementation.  This
adapter supplies a compatible transactional connection, PostgreSQL DDL, and a
database-wide advisory lock for the short scheduler/admission transaction.
Planning work itself runs outside that lock, so workers execute concurrently
while claim order and tenant admission remain deterministic.
"""

from __future__ import annotations

import re
from types import TracebackType
from typing import Any

from .repository import JobStoreUnavailable, PlanningJobRepository


POSTGRES_SCHEMA_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
POSTGRES_WRITE_LOCK_KEY = 4_770_216_271_983_143_219
DEFAULT_POSTGRES_POOL_MIN_SIZE = 1
DEFAULT_POSTGRES_POOL_MAX_SIZE = 4
DEFAULT_POSTGRES_POOL_TIMEOUT_SECONDS = 1.0
DEFAULT_POSTGRES_POOL_MAX_WAITING = 8
MAX_POSTGRES_POOL_SIZE = 64
MAX_POSTGRES_POOL_WAITING = 256


POSTGRES_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS planning_jobs (
        rowid BIGSERIAL UNIQUE NOT NULL,
        job_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        submitted_by TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN (
                'queued', 'running', 'succeeded', 'failed',
                'dead_lettered', 'cancelled', 'timed_out'
            )
        ),
        request_json TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        idempotency_key TEXT,
        attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
        priority INTEGER NOT NULL DEFAULT 0 CHECK (priority BETWEEN 0 AND 9),
        deadline_seconds INTEGER NOT NULL DEFAULT 900
            CHECK (deadline_seconds BETWEEN 1 AND 86400),
        deadline_at TEXT,
        available_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        cancel_requested_at TEXT,
        cancelled_at TEXT,
        cancel_reason_code TEXT,
        replayed_from_job_id TEXT REFERENCES planning_jobs(job_id),
        lease_owner TEXT,
        lease_expires_at TEXT,
        artifact_id TEXT,
        artifact_sha256 TEXT,
        result_json TEXT,
        error_code TEXT,
        error_message TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_planning_jobs_claim
    ON planning_jobs(status, available_at, lease_expires_at, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_planning_jobs_schedule
    ON planning_jobs(status, priority, available_at, lease_expires_at, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_planning_jobs_list
    ON planning_jobs(status, rowid)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_planning_jobs_replay_origin
    ON planning_jobs(replayed_from_job_id, created_at, job_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_planning_jobs_tenant_idempotency
    ON planning_jobs(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS planning_job_events (
        event_id BIGSERIAL PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES planning_jobs(job_id),
        event_type TEXT NOT NULL CHECK (
            event_type IN (
                'submitted', 'claimed', 'heartbeat', 'retry_scheduled',
                'lease_reclaimed', 'cancel_requested', 'cancelled',
                'replay_requested', 'timed_out', 'succeeded', 'failed',
                'dead_lettered'
            )
        ),
        attempt INTEGER NOT NULL,
        worker_id TEXT,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_planning_job_events_replay
    ON planning_job_events(job_id, event_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS planning_job_admission_events (
        event_id BIGSERIAL PRIMARY KEY,
        policy_version TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        submitted_by TEXT NOT NULL,
        request_id TEXT NOT NULL,
        operation TEXT NOT NULL CHECK (operation IN ('submit', 'replay')),
        decision TEXT NOT NULL CHECK (
            decision IN ('admitted', 'rejected', 'idempotent_reuse')
        ),
        reason_code TEXT,
        job_id TEXT REFERENCES planning_jobs(job_id),
        idempotency_key_present SMALLINT NOT NULL
            CHECK (idempotency_key_present IN (0, 1)),
        active_jobs_before INTEGER NOT NULL,
        recent_submissions_before INTEGER NOT NULL,
        active_job_limit INTEGER,
        submission_limit_per_minute INTEGER,
        submission_window_seconds INTEGER NOT NULL,
        retry_after_seconds INTEGER,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_planning_job_admission_tenant
    ON planning_job_admission_events(tenant_id, event_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS planning_tenant_scheduler_state (
        tenant_id TEXT PRIMARY KEY,
        last_claimed_event_id BIGINT NOT NULL,
        claim_count INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS planning_job_store_migrations (
        migration_id TEXT PRIMARY KEY,
        layout_version TEXT NOT NULL,
        source_name TEXT NOT NULL,
        source_file_sha256 TEXT NOT NULL,
        source_counts_json TEXT NOT NULL,
        source_digests_json TEXT NOT NULL,
        destination_counts_json TEXT NOT NULL,
        destination_digests_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        receipt_sha256 TEXT NOT NULL
    )
    """,
    """
    CREATE OR REPLACE FUNCTION bj_pal_effective_priority(
        base_priority INTEGER,
        eligible_at TEXT,
        claimed_at TEXT
    ) RETURNS INTEGER
    LANGUAGE SQL
    IMMUTABLE
    STRICT
    AS $$
        SELECT LEAST(
            9,
            base_priority + FLOOR(
                GREATEST(
                    0,
                    EXTRACT(EPOCH FROM (
                        claimed_at::timestamptz - eligible_at::timestamptz
                    ))
                ) / 60
            )::INTEGER
        )
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION bj_pal_reject_event_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'planning job events are append-only';
    END;
    $$
    """,
    "DROP TRIGGER IF EXISTS planning_job_events_no_update ON planning_job_events",
    """
    CREATE TRIGGER planning_job_events_no_update
    BEFORE UPDATE ON planning_job_events
    FOR EACH ROW EXECUTE FUNCTION bj_pal_reject_event_mutation()
    """,
    "DROP TRIGGER IF EXISTS planning_job_events_no_delete ON planning_job_events",
    """
    CREATE TRIGGER planning_job_events_no_delete
    BEFORE DELETE ON planning_job_events
    FOR EACH ROW EXECUTE FUNCTION bj_pal_reject_event_mutation()
    """,
    "DROP TRIGGER IF EXISTS planning_job_admission_events_no_update ON planning_job_admission_events",
    """
    CREATE TRIGGER planning_job_admission_events_no_update
    BEFORE UPDATE ON planning_job_admission_events
    FOR EACH ROW EXECUTE FUNCTION bj_pal_reject_event_mutation()
    """,
    "DROP TRIGGER IF EXISTS planning_job_admission_events_no_delete ON planning_job_admission_events",
    """
    CREATE TRIGGER planning_job_admission_events_no_delete
    BEFORE DELETE ON planning_job_admission_events
    FOR EACH ROW EXECUTE FUNCTION bj_pal_reject_event_mutation()
    """,
    "DROP TRIGGER IF EXISTS planning_job_store_migrations_no_update ON planning_job_store_migrations",
    """
    CREATE TRIGGER planning_job_store_migrations_no_update
    BEFORE UPDATE ON planning_job_store_migrations
    FOR EACH ROW EXECUTE FUNCTION bj_pal_reject_event_mutation()
    """,
    "DROP TRIGGER IF EXISTS planning_job_store_migrations_no_delete ON planning_job_store_migrations",
    """
    CREATE TRIGGER planning_job_store_migrations_no_delete
    BEFORE DELETE ON planning_job_store_migrations
    FOR EACH ROW EXECUTE FUNCTION bj_pal_reject_event_mutation()
    """,
)


class _PostgresResult:
    def __init__(self, cursor: Any, *, lastrowid: int | None = None) -> None:
        self._cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> dict[str, Any] | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return self._cursor.fetchall()


class _PostgresConnection:
    """Small DB-API compatibility layer for the shared transition code."""

    def __init__(self, connection: Any, lease: Any) -> None:
        self._connection = connection
        self._lease = lease

    def __enter__(self) -> _PostgresConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            return bool(self._lease.__exit__(exc_type, exc, traceback))
        except Exception as transaction_error:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store transaction failed"
            ) from transaction_error

    @property
    def in_transaction(self) -> bool:
        from psycopg.pq import TransactionStatus

        return self._connection.info.transaction_status != TransactionStatus.IDLE

    def commit(self) -> None:
        try:
            self._connection.commit()
        except Exception as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store transaction failed"
            ) from exc

    def rollback(self) -> None:
        try:
            self._connection.rollback()
        except Exception as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store rollback failed"
            ) from exc

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] | list[object] = (),
    ) -> _PostgresResult:
        try:
            normalized = statement.strip().rstrip(";")
            if normalized == "BEGIN IMMEDIATE":
                self._connection.execute("BEGIN")
                cursor = self._connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (POSTGRES_WRITE_LOCK_KEY,),
                )
                return _PostgresResult(cursor)
            if normalized == "BEGIN":
                cursor = self._connection.execute(
                    "BEGIN ISOLATION LEVEL REPEATABLE READ"
                )
                return _PostgresResult(cursor)

            translated = statement.replace("?", "%s")
            returns_event_id = normalized.startswith(
                "INSERT INTO planning_job_events"
            ) or normalized.startswith("INSERT INTO planning_job_admission_events")
            if returns_event_id:
                translated = f"{translated.rstrip().rstrip(';')} RETURNING event_id"
            cursor = self._connection.execute(translated, parameters)
            if returns_event_id:
                row = cursor.fetchone()
                return _PostgresResult(cursor, lastrowid=int(row["event_id"]))
            return _PostgresResult(cursor)
        except JobStoreUnavailable:
            raise
        except Exception as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store operation failed"
            ) from exc


class PostgresPlanningJobRepository(PlanningJobRepository):
    """Drop-in durable repository backed by PostgreSQL.

    The DSN is intentionally never exposed through ``repr``.  A dedicated
    schema can be supplied for integration tests or tenant-isolated deployment
    units; identifiers are validated before interpolation.
    """

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        pool_min_size: int = DEFAULT_POSTGRES_POOL_MIN_SIZE,
        pool_max_size: int = DEFAULT_POSTGRES_POOL_MAX_SIZE,
        pool_timeout_seconds: float = DEFAULT_POSTGRES_POOL_TIMEOUT_SECONDS,
        pool_max_waiting: int = DEFAULT_POSTGRES_POOL_MAX_WAITING,
    ) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be empty")
        if not POSTGRES_SCHEMA_PATTERN.fullmatch(schema):
            raise ValueError("PostgreSQL schema must be a safe lowercase identifier")
        if isinstance(pool_min_size, bool) or not isinstance(pool_min_size, int):
            raise ValueError("PostgreSQL pool_min_size must be an integer")
        if isinstance(pool_max_size, bool) or not isinstance(pool_max_size, int):
            raise ValueError("PostgreSQL pool_max_size must be an integer")
        if not 0 <= pool_min_size <= MAX_POSTGRES_POOL_SIZE:
            raise ValueError(
                f"PostgreSQL pool_min_size must be between 0 and {MAX_POSTGRES_POOL_SIZE}"
            )
        if not 1 <= pool_max_size <= MAX_POSTGRES_POOL_SIZE:
            raise ValueError(
                f"PostgreSQL pool_max_size must be between 1 and {MAX_POSTGRES_POOL_SIZE}"
            )
        if pool_min_size > pool_max_size:
            raise ValueError(
                "PostgreSQL pool_min_size must not exceed pool_max_size"
            )
        if isinstance(pool_timeout_seconds, bool) or not isinstance(
            pool_timeout_seconds, (int, float)
        ):
            raise ValueError("PostgreSQL pool_timeout_seconds must be numeric")
        if not 0.05 <= float(pool_timeout_seconds) <= 60.0:
            raise ValueError(
                "PostgreSQL pool_timeout_seconds must be between 0.05 and 60"
            )
        if isinstance(pool_max_waiting, bool) or not isinstance(pool_max_waiting, int):
            raise ValueError("PostgreSQL pool_max_waiting must be an integer")
        if not 1 <= pool_max_waiting <= MAX_POSTGRES_POOL_WAITING:
            raise ValueError(
                "PostgreSQL pool_max_waiting must be between "
                f"1 and {MAX_POSTGRES_POOL_WAITING}"
            )
        self._dsn = dsn
        self.schema = schema
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.pool_timeout_seconds = float(pool_timeout_seconds)
        self.pool_max_waiting = pool_max_waiting
        self._create_postgres_schema()
        self._pool = self._create_pool()
        try:
            self._pool.open(wait=True, timeout=5.0)
            self._initialize_postgres_schema()
        except JobStoreUnavailable:
            self._pool.close()
            raise
        except Exception as exc:
            self._pool.close()
            raise JobStoreUnavailable(
                "PostgreSQL durable job store pool initialization failed"
            ) from exc

    def __repr__(self) -> str:
        return f"{type(self).__name__}(schema={self.schema!r})"

    def _create_pool(self) -> Any:
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - exercised by packaging gate
            raise RuntimeError(
                "PostgreSQL job store requires psycopg_pool; install requirements.txt"
            ) from exc
        return ConnectionPool(
            conninfo=self._dsn,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": 5,
                "application_name": "bj-pal-job-store",
            },
            min_size=self.pool_min_size,
            max_size=self.pool_max_size,
            timeout=self.pool_timeout_seconds,
            max_waiting=self.pool_max_waiting,
            configure=self._configure_pool_connection,
            check=ConnectionPool.check_connection,
            name="bj-pal-job-store",
            open=False,
        )

    def _configure_pool_connection(self, connection: Any) -> None:
        from psycopg import sql

        connection.execute(
            sql.SQL("SET search_path TO {}, pg_catalog").format(
                sql.Identifier(self.schema)
            )
        )
        connection.commit()

    def _raw_connect(self, *, autocommit: bool = False) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - exercised by packaging gate
            raise RuntimeError(
                "PostgreSQL job store requires psycopg; install requirements.txt"
            ) from exc
        try:
            connection = psycopg.connect(
                self._dsn,
                autocommit=autocommit,
                row_factory=dict_row,
                connect_timeout=5,
                application_name="bj-pal-job-store-direct",
            )
            return connection
        except psycopg.Error as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store connection failed"
            ) from exc

    def _connect(self) -> _PostgresConnection:
        from psycopg_pool import PoolClosed, PoolTimeout, TooManyRequests

        try:
            lease = self._pool.connection(timeout=self.pool_timeout_seconds)
            connection = lease.__enter__()
            return _PostgresConnection(connection, lease)
        except TooManyRequests as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store connection queue is full"
            ) from exc
        except PoolTimeout as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store connection acquisition timed out"
            ) from exc
        except PoolClosed as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store connection pool is closed"
            ) from exc
        except JobStoreUnavailable:
            raise
        except Exception as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store connection failed"
            ) from exc

    def close(self, *, timeout: float = 5.0) -> None:
        self._pool.close(timeout=timeout)

    @property
    def closed(self) -> bool:
        return bool(self._pool.closed)

    def pool_stats(self) -> dict[str, int]:
        return {key: int(value) for key, value in self._pool.get_stats().items()}

    def _create_postgres_schema(self) -> None:
        from psycopg import sql

        try:
            with self._raw_connect(autocommit=True) as connection:
                connection.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        sql.Identifier(self.schema)
                    )
                )
        except JobStoreUnavailable:
            raise
        except Exception as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store schema creation failed"
            ) from exc

    def _initialize_postgres_schema(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for statement in POSTGRES_SCHEMA_STATEMENTS:
                    connection.execute(statement)
        except JobStoreUnavailable:
            raise
        except Exception as exc:
            raise JobStoreUnavailable(
                "PostgreSQL durable job store schema initialization failed"
            ) from exc
