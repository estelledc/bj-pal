"""SQLite repository with idempotent submit and recoverable worker leases."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import (
    PlanningAdmissionEvent,
    PlanningJob,
    PlanningJobEvent,
    PlanningJobSummary,
    PlanningJobWindowEvidence,
)
from .workload_health import (
    MAX_WORKLOAD_EVENTS,
    MAX_WORKLOAD_JOBS,
    JobWorkloadEvidenceLimitExceeded,
    canonical_timestamp,
    derive_status_from_events,
    validate_window,
)


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_JOB_DB = ROOT / "runtime" / "planning_jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS planning_jobs (
    job_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN (
            'queued', 'running', 'succeeded', 'failed',
            'dead_lettered', 'cancelled', 'timed_out'
        )
    ),
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    idempotency_key TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
    priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 9),
    deadline_seconds INTEGER NOT NULL DEFAULT 900
        CHECK(deadline_seconds BETWEEN 1 AND 86400),
    deadline_at TEXT,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cancel_requested_at TEXT,
    cancelled_at TEXT,
    cancel_reason_code TEXT,
    replayed_from_job_id TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    artifact_id TEXT,
    artifact_sha256 TEXT,
    result_json TEXT,
    error_code TEXT,
    error_message TEXT,
    FOREIGN KEY(replayed_from_job_id) REFERENCES planning_jobs(job_id)
);
CREATE INDEX IF NOT EXISTS idx_planning_jobs_claim
ON planning_jobs(status, available_at, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_planning_jobs_schedule
ON planning_jobs(status, priority, available_at, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_planning_jobs_list
ON planning_jobs(status, created_at, job_id);
CREATE INDEX IF NOT EXISTS idx_planning_jobs_replay_origin
ON planning_jobs(replayed_from_job_id, created_at, job_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_planning_jobs_tenant_idempotency
ON planning_jobs(tenant_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS planning_job_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(
        event_type IN (
            'submitted', 'claimed', 'heartbeat', 'retry_scheduled',
            'lease_reclaimed', 'cancel_requested', 'cancelled',
            'replay_requested', 'timed_out', 'succeeded', 'failed', 'dead_lettered'
        )
    ),
    attempt INTEGER NOT NULL,
    worker_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES planning_jobs(job_id)
);
CREATE INDEX IF NOT EXISTS idx_planning_job_events_replay
ON planning_job_events(job_id, event_id);

CREATE TRIGGER IF NOT EXISTS planning_job_events_no_update
BEFORE UPDATE ON planning_job_events
BEGIN
    SELECT RAISE(ABORT, 'planning job events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS planning_job_events_no_delete
BEFORE DELETE ON planning_job_events
BEGIN
    SELECT RAISE(ABORT, 'planning job events are append-only');
END;

CREATE TABLE IF NOT EXISTS planning_job_admission_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_version TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN ('submit', 'replay')),
    decision TEXT NOT NULL CHECK(
        decision IN ('admitted', 'rejected', 'idempotent_reuse')
    ),
    reason_code TEXT,
    job_id TEXT,
    idempotency_key_present INTEGER NOT NULL CHECK(idempotency_key_present IN (0, 1)),
    active_jobs_before INTEGER NOT NULL,
    recent_submissions_before INTEGER NOT NULL,
    active_job_limit INTEGER,
    submission_limit_per_minute INTEGER,
    submission_window_seconds INTEGER NOT NULL,
    retry_after_seconds INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES planning_jobs(job_id)
);
CREATE INDEX IF NOT EXISTS idx_planning_job_admission_tenant
ON planning_job_admission_events(tenant_id, event_id);
CREATE TRIGGER IF NOT EXISTS planning_job_admission_events_no_update
BEFORE UPDATE ON planning_job_admission_events
BEGIN
    SELECT RAISE(ABORT, 'planning job admission events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS planning_job_admission_events_no_delete
BEFORE DELETE ON planning_job_admission_events
BEGIN
    SELECT RAISE(ABORT, 'planning job admission events are append-only');
END;

CREATE TABLE IF NOT EXISTS planning_tenant_scheduler_state (
    tenant_id TEXT PRIMARY KEY,
    last_claimed_event_id INTEGER NOT NULL,
    claim_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class IdempotencyConflict(ValueError):
    """The same key was reused for a different canonical request."""


class JobNotFound(LookupError):
    """The requested durable job or cursor does not exist."""


class InvalidJobTransition(ValueError):
    """The requested control operation is not valid for the current status."""


class JobStoreUnavailable(RuntimeError):
    """The configured durable store could not safely serve an operation."""


class TenantAdmissionRejected(RuntimeError):
    """A tenant-scoped active-job or sliding-window admission policy denied work."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        active_jobs: int,
        recent_submissions: int,
        active_job_limit: int,
        submission_limit_per_minute: int,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.active_jobs = active_jobs
        self.recent_submissions = recent_submissions
        self.active_job_limit = active_job_limit
        self.submission_limit_per_minute = submission_limit_per_minute
        self.retry_after_seconds = retry_after_seconds


CANCEL_REASON_CODES = frozenset({"user_requested", "superseded", "operator_requested"})
REPLAYABLE_STATUSES = frozenset({"failed", "dead_lettered", "timed_out"})
TIMEOUT_ERROR_CODE = "job_deadline_exceeded"
TIMEOUT_ERROR_MESSAGE = "Planning job exceeded its durable deadline."
SCHEDULING_POLICY_VERSION = "tenant_fair_priority_aging_v2"
PRIORITY_POLICY_VERSION = "priority_aging_v1"
TENANT_FAIRNESS_POLICY_VERSION = "least_recently_served_tenant_v1"
ADMISSION_POLICY_VERSION = "tenant_admission_v1"
PRIORITY_AGING_SECONDS = 60
SUBMISSION_RATE_WINDOW_SECONDS = 60
MIN_PRIORITY = 0
MAX_PRIORITY = 9
SCOPE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
JOB_STATUSES = frozenset(
    {
        "queued",
        "running",
        "succeeded",
        "failed",
        "dead_lettered",
        "cancelled",
        "timed_out",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compute_effective_priority(
    *,
    priority: int,
    eligible_at: str,
    claimed_at: str,
) -> tuple[int, int]:
    """Return effective priority and eligible queue wait in milliseconds."""
    if (
        isinstance(priority, bool)
        or not isinstance(priority, int)
        or not MIN_PRIORITY <= priority <= MAX_PRIORITY
    ):
        raise ValueError("priority must be an integer between 0 and 9")
    wait = _parse_timestamp(claimed_at) - _parse_timestamp(eligible_at)
    wait_ms = max(
        0,
        wait.days * 86_400_000 + wait.seconds * 1000 + wait.microseconds // 1000,
    )
    aging_steps = wait_ms // (PRIORITY_AGING_SECONDS * 1000)
    return min(MAX_PRIORITY, priority + aging_steps), wait_ms


def _sql_effective_priority(priority: int, eligible_at: str, claimed_at: str) -> int:
    return compute_effective_priority(
        priority=int(priority),
        eligible_at=str(eligible_at),
        claimed_at=str(claimed_at),
    )[0]


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _validate_scope_identifier(value: str, *, field: str) -> None:
    if not SCOPE_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must contain 1-64 safe characters")


def _validate_admission_limits(
    *,
    tenant_active_job_limit: int | None,
    tenant_submission_limit_per_minute: int | None,
) -> None:
    if (tenant_active_job_limit is None) != (
        tenant_submission_limit_per_minute is None
    ):
        raise ValueError("tenant admission limits must be configured together")
    if tenant_active_job_limit is None:
        return
    if (
        isinstance(tenant_active_job_limit, bool)
        or not isinstance(tenant_active_job_limit, int)
        or not 1 <= tenant_active_job_limit <= 10_000
    ):
        raise ValueError("tenant_active_job_limit must be between 1 and 10000")
    if (
        isinstance(tenant_submission_limit_per_minute, bool)
        or not isinstance(tenant_submission_limit_per_minute, int)
        or not 1 <= tenant_submission_limit_per_minute <= 10_000
    ):
        raise ValueError(
            "tenant_submission_limit_per_minute must be between 1 and 10000"
        )


class PlanningJobRepository:
    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get("BJ_PAL_JOB_DB")
        self.path = path or (Path(configured) if configured else DEFAULT_JOB_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._initialize_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function(
            "bj_pal_effective_priority",
            3,
            _sql_effective_priority,
            deterministic=True,
        )
        return connection

    def probe(self) -> bool:
        """Check connectivity without reading request or result payloads."""
        with self._connect() as connection:
            row = connection.execute("SELECT 1 AS ready").fetchone()
        return row is not None and int(row["ready"]) == 1

    @staticmethod
    def _initialize_schema(connection: sqlite3.Connection) -> None:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "planning_jobs" not in tables:
            connection.executescript(SCHEMA)
            return

        job_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(planning_jobs)")
        }
        event_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'planning_job_events'"
        ).fetchone()
        event_sql = str(event_sql_row["sql"] or "") if event_sql_row else ""
        jobs_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'planning_jobs'"
        ).fetchone()
        jobs_sql = str(jobs_sql_row["sql"] or "") if jobs_sql_row else ""
        base_current = (
            {
                "max_attempts",
                "deadline_seconds",
                "deadline_at",
                "available_at",
                "cancel_requested_at",
                "cancelled_at",
                "cancel_reason_code",
                "replayed_from_job_id",
            }
            <= job_columns
            and "dead_lettered" in jobs_sql
            and "cancelled" in jobs_sql
            and "timed_out" in jobs_sql
            and "retry_scheduled" in event_sql
            and "heartbeat" in event_sql
            and "cancel_requested" in event_sql
            and "replay_requested" in event_sql
            and "timed_out" in event_sql
        )
        identity_current = {"priority", "tenant_id", "submitted_by"} <= job_columns
        if not base_current or not identity_current:
            PlanningJobRepository._migrate_legacy_schema(connection, job_columns)
        connection.executescript(SCHEMA)

    @staticmethod
    def _migrate_legacy_schema(
        connection: sqlite3.Connection,
        job_columns: set[str],
    ) -> None:
        """Rebuild legacy job/event tables because SQLite cannot alter CHECK clauses."""
        max_attempts_expr = "max_attempts" if "max_attempts" in job_columns else "3"
        deadline_seconds_expr = (
            "deadline_seconds" if "deadline_seconds" in job_columns else "900"
        )
        deadline_at_expr = "deadline_at" if "deadline_at" in job_columns else "NULL"
        available_at_expr = "available_at" if "available_at" in job_columns else "created_at"
        cancel_requested_expr = (
            "cancel_requested_at" if "cancel_requested_at" in job_columns else "NULL"
        )
        cancelled_expr = "cancelled_at" if "cancelled_at" in job_columns else "NULL"
        cancel_reason_expr = (
            "cancel_reason_code" if "cancel_reason_code" in job_columns else "NULL"
        )
        replayed_from_expr = (
            "replayed_from_job_id" if "replayed_from_job_id" in job_columns else "NULL"
        )
        priority_expr = "priority" if "priority" in job_columns else "0"
        tenant_expr = "tenant_id" if "tenant_id" in job_columns else "'default'"
        submitted_by_expr = (
            "submitted_by" if "submitted_by" in job_columns else "'legacy-migration'"
        )
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.executescript(
                f"""
                BEGIN IMMEDIATE;
                DROP TRIGGER IF EXISTS planning_job_events_no_update;
                DROP TRIGGER IF EXISTS planning_job_events_no_delete;

                CREATE TABLE planning_jobs_v59 (
                    job_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    submitted_by TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(
                        status IN (
                            'queued', 'running', 'succeeded', 'failed',
                            'dead_lettered', 'cancelled', 'timed_out'
                        )
                    ),
                    request_json TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    idempotency_key TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3
                        CHECK(max_attempts BETWEEN 1 AND 10),
                    priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 9),
                    deadline_seconds INTEGER NOT NULL DEFAULT 900
                        CHECK(deadline_seconds BETWEEN 1 AND 86400),
                    deadline_at TEXT,
                    available_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    cancel_requested_at TEXT,
                    cancelled_at TEXT,
                    cancel_reason_code TEXT,
                    replayed_from_job_id TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    artifact_id TEXT,
                    artifact_sha256 TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    FOREIGN KEY(replayed_from_job_id) REFERENCES planning_jobs_v59(job_id)
                );
                INSERT INTO planning_jobs_v59(
                    job_id, request_id, tenant_id, submitted_by,
                    status, request_json, request_sha256,
                    idempotency_key, attempt, max_attempts, priority, deadline_seconds,
                    deadline_at, available_at,
                    created_at, updated_at, cancel_requested_at, cancelled_at,
                    cancel_reason_code, replayed_from_job_id, lease_owner, lease_expires_at,
                    artifact_id, artifact_sha256, result_json, error_code, error_message
                )
                SELECT
                    job_id, request_id, {tenant_expr}, {submitted_by_expr},
                    status, request_json, request_sha256,
                    idempotency_key, attempt, {max_attempts_expr}, {priority_expr},
                    {deadline_seconds_expr}, {deadline_at_expr}, {available_at_expr},
                    created_at, updated_at, {cancel_requested_expr}, {cancelled_expr},
                    {cancel_reason_expr}, {replayed_from_expr}, lease_owner, lease_expires_at,
                    artifact_id, artifact_sha256, result_json, error_code, error_message
                FROM planning_jobs;

                CREATE TABLE planning_job_events_v59 (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(
                        event_type IN (
                            'submitted', 'claimed', 'heartbeat', 'retry_scheduled',
                            'lease_reclaimed', 'cancel_requested', 'cancelled',
                            'replay_requested', 'timed_out',
                            'succeeded', 'failed', 'dead_lettered'
                        )
                    ),
                    attempt INTEGER NOT NULL,
                    worker_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES planning_jobs_v59(job_id)
                );
                INSERT INTO planning_job_events_v59(
                    event_id, job_id, event_type, attempt, worker_id, payload_json, created_at
                )
                SELECT event_id, job_id, event_type, attempt, worker_id, payload_json, created_at
                FROM planning_job_events;

                DROP TABLE planning_job_events;
                DROP TABLE planning_jobs;
                ALTER TABLE planning_jobs_v59 RENAME TO planning_jobs;
                ALTER TABLE planning_job_events_v59 RENAME TO planning_job_events;
                COMMIT;
                """
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _admission_snapshot(
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        now_value: datetime,
    ) -> tuple[int, int, str | None]:
        active_row = connection.execute(
            """
            SELECT COUNT(*) AS active_jobs
            FROM planning_jobs
            WHERE tenant_id = ? AND status IN ('queued', 'running')
            """,
            (tenant_id,),
        ).fetchone()
        window_start = _timestamp(
            now_value - timedelta(seconds=SUBMISSION_RATE_WINDOW_SECONDS)
        )
        recent_row = connection.execute(
            """
            SELECT COUNT(*) AS recent_submissions, MIN(created_at) AS oldest_created_at
            FROM planning_jobs
            WHERE tenant_id = ? AND created_at > ?
            """,
            (tenant_id, window_start),
        ).fetchone()
        return (
            int(active_row["active_jobs"]),
            int(recent_row["recent_submissions"]),
            (
                str(recent_row["oldest_created_at"])
                if recent_row["oldest_created_at"] is not None
                else None
            ),
        )

    def _enforce_tenant_admission(
        self,
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        submitted_by: str,
        request_id: str,
        operation: str,
        idempotency_key_present: bool,
        tenant_active_job_limit: int | None,
        tenant_submission_limit_per_minute: int | None,
        now_value: datetime,
        now: str,
    ) -> tuple[int, int]:
        active_jobs, recent_submissions, oldest_created_at = self._admission_snapshot(
            connection,
            tenant_id=tenant_id,
            now_value=now_value,
        )
        if tenant_active_job_limit is None:
            return active_jobs, recent_submissions
        rejection: TenantAdmissionRejected | None = None
        if active_jobs >= tenant_active_job_limit:
            rejection = TenantAdmissionRejected(
                code="tenant_active_job_limit_exceeded",
                message="The tenant has reached its active durable-job limit.",
                active_jobs=active_jobs,
                recent_submissions=recent_submissions,
                active_job_limit=tenant_active_job_limit,
                submission_limit_per_minute=tenant_submission_limit_per_minute,
            )
        elif recent_submissions >= tenant_submission_limit_per_minute:
            retry_after_seconds = 1
            if oldest_created_at is not None:
                retry_after_seconds = max(
                    1,
                    math.ceil(
                        (
                            _parse_timestamp(oldest_created_at)
                            + timedelta(seconds=SUBMISSION_RATE_WINDOW_SECONDS)
                            - now_value
                        ).total_seconds()
                    ),
                )
            rejection = TenantAdmissionRejected(
                code="tenant_submission_rate_exceeded",
                message="The tenant has reached its one-minute submission limit.",
                active_jobs=active_jobs,
                recent_submissions=recent_submissions,
                active_job_limit=tenant_active_job_limit,
                submission_limit_per_minute=tenant_submission_limit_per_minute,
                retry_after_seconds=retry_after_seconds,
            )
        if rejection is None:
            return active_jobs, recent_submissions
        self._append_admission_event(
            connection,
            tenant_id=tenant_id,
            submitted_by=submitted_by,
            request_id=request_id,
            operation=operation,
            decision="rejected",
            reason_code=rejection.code,
            job_id=None,
            idempotency_key_present=idempotency_key_present,
            active_jobs_before=active_jobs,
            recent_submissions_before=recent_submissions,
            active_job_limit=tenant_active_job_limit,
            submission_limit_per_minute=tenant_submission_limit_per_minute,
            retry_after_seconds=rejection.retry_after_seconds,
            created_at=now,
        )
        connection.commit()
        raise rejection

    def submit(
        self,
        *,
        request_id: str,
        request_payload: dict,
        tenant_id: str = "default",
        submitted_by: str = "system",
        idempotency_key: str | None = None,
        max_attempts: int = 3,
        priority: int = 0,
        deadline_seconds: int = 900,
        tenant_active_job_limit: int | None = None,
        tenant_submission_limit_per_minute: int | None = None,
    ) -> PlanningJob:
        _validate_scope_identifier(tenant_id, field="tenant_id")
        _validate_scope_identifier(submitted_by, field="submitted_by")
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not 1 <= deadline_seconds <= 86400:
            raise ValueError("deadline_seconds must be between 1 and 86400")
        if (
            isinstance(priority, bool)
            or not isinstance(priority, int)
            or not MIN_PRIORITY <= priority <= MAX_PRIORITY
        ):
            raise ValueError("priority must be an integer between 0 and 9")
        _validate_admission_limits(
            tenant_active_job_limit=tenant_active_job_limit,
            tenant_submission_limit_per_minute=(
                tenant_submission_limit_per_minute
            ),
        )
        request_json = _canonical_json(request_payload)
        request_sha = _sha256(request_json)
        now_value = _utc_now()
        now = _timestamp(now_value)
        deadline_at = _timestamp(now_value + timedelta(seconds=deadline_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM planning_jobs WHERE tenant_id = ? AND idempotency_key = ?",
                    (tenant_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["request_sha256"] != request_sha
                        or existing["replayed_from_job_id"] is not None
                        or int(existing["deadline_seconds"]) != deadline_seconds
                        or int(existing["priority"]) != priority
                    ):
                        raise IdempotencyConflict(
                            "idempotency key already belongs to a different operation"
                        )
                    active_jobs, recent_submissions, _ = self._admission_snapshot(
                        connection,
                        tenant_id=tenant_id,
                        now_value=now_value,
                    )
                    self._append_admission_event(
                        connection,
                        tenant_id=tenant_id,
                        submitted_by=submitted_by,
                        request_id=request_id,
                        operation="submit",
                        decision="idempotent_reuse",
                        reason_code=None,
                        job_id=str(existing["job_id"]),
                        idempotency_key_present=True,
                        active_jobs_before=active_jobs,
                        recent_submissions_before=recent_submissions,
                        active_job_limit=tenant_active_job_limit,
                        submission_limit_per_minute=(
                            tenant_submission_limit_per_minute
                        ),
                        retry_after_seconds=None,
                        created_at=now,
                    )
                    return self._from_row(existing)
            self._settle_expired_deadlines(connection, now=now)
            active_jobs, recent_submissions = self._enforce_tenant_admission(
                connection,
                tenant_id=tenant_id,
                submitted_by=submitted_by,
                request_id=request_id,
                operation="submit",
                idempotency_key_present=idempotency_key is not None,
                tenant_active_job_limit=tenant_active_job_limit,
                tenant_submission_limit_per_minute=(
                    tenant_submission_limit_per_minute
                ),
                now_value=now_value,
                now=now,
            )
            job_id = f"job-{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO planning_jobs(
                    job_id, request_id, tenant_id, submitted_by,
                    status, request_json, request_sha256,
                    idempotency_key, attempt, max_attempts, deadline_seconds,
                    priority, deadline_at, available_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request_id,
                    tenant_id,
                    submitted_by,
                    request_json,
                    request_sha,
                    idempotency_key,
                    max_attempts,
                    deadline_seconds,
                    priority,
                    deadline_at,
                    now,
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                job_id=job_id,
                event_type="submitted",
                attempt=0,
                worker_id=None,
                payload={
                    "request_sha256": request_sha,
                    "idempotency_key_present": idempotency_key is not None,
                    "max_attempts": max_attempts,
                    "tenant_id": tenant_id,
                    "submitted_by": submitted_by,
                    "priority": priority,
                    "deadline_seconds": deadline_seconds,
                    "deadline_at": deadline_at,
                },
                created_at=now,
            )
            self._append_admission_event(
                connection,
                tenant_id=tenant_id,
                submitted_by=submitted_by,
                request_id=request_id,
                operation="submit",
                decision="admitted",
                reason_code=None,
                job_id=job_id,
                idempotency_key_present=idempotency_key is not None,
                active_jobs_before=active_jobs,
                recent_submissions_before=recent_submissions,
                active_job_limit=tenant_active_job_limit,
                submission_limit_per_minute=tenant_submission_limit_per_minute,
                retry_after_seconds=None,
                created_at=now,
            )
            row = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._from_row(row)

    def get(self, job_id: str, *, tenant_id: str | None = None) -> PlanningJob | None:
        if tenant_id is not None:
            _validate_scope_identifier(tenant_id, field="tenant_id")
        with self._connect() as connection:
            if tenant_id is None:
                row = connection.execute(
                    "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM planning_jobs WHERE job_id = ? AND tenant_id = ?",
                    (job_id, tenant_id),
                ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_jobs(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        after_job_id: str | None = None,
        limit: int = 100,
    ) -> tuple[PlanningJobSummary, ...]:
        if tenant_id is not None:
            _validate_scope_identifier(tenant_id, field="tenant_id")
        if status is not None and status not in JOB_STATUSES:
            raise ValueError("unknown planning job status")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses: list[str] = []
        parameters: list[object] = []
        with self._connect() as connection:
            if tenant_id is not None:
                clauses.append("tenant_id = ?")
                parameters.append(tenant_id)
            if status is not None:
                clauses.append("status = ?")
                parameters.append(status)
            if after_job_id is not None:
                if tenant_id is None:
                    cursor = connection.execute(
                        "SELECT rowid AS job_sequence FROM planning_jobs WHERE job_id = ?",
                        (after_job_id,),
                    ).fetchone()
                else:
                    cursor = connection.execute(
                        "SELECT rowid AS job_sequence FROM planning_jobs "
                        "WHERE job_id = ? AND tenant_id = ?",
                        (after_job_id, tenant_id),
                    ).fetchone()
                if cursor is None:
                    raise JobNotFound("planning job cursor was not found")
                clauses.append("rowid > ?")
                parameters.append(cursor["job_sequence"])
            where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = connection.execute(
                f"""
                SELECT
                    job_id, request_id, tenant_id, submitted_by,
                    status, attempt, max_attempts,
                    priority, deadline_seconds, deadline_at, available_at,
                    created_at, updated_at, cancel_requested_at, cancelled_at,
                    cancel_reason_code, replayed_from_job_id, artifact_id, error_code
                FROM planning_jobs
                {where_clause}
                ORDER BY rowid
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()
        return tuple(self._summary_from_row(row) for row in rows)

    def request_cancel(
        self,
        *,
        job_id: str,
        reason_code: str,
        tenant_id: str | None = None,
    ) -> PlanningJob:
        if tenant_id is not None:
            _validate_scope_identifier(tenant_id, field="tenant_id")
        if reason_code not in CANCEL_REASON_CODES:
            raise ValueError("unknown cancellation reason code")
        now = _timestamp(_utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if tenant_id is None:
                row = connection.execute(
                    "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM planning_jobs WHERE job_id = ? AND tenant_id = ?",
                    (job_id, tenant_id),
                ).fetchone()
            if row is None:
                raise JobNotFound("planning job was not found")
            if row["status"] == "cancelled":
                return self._from_row(row)
            deadline_reached = (
                row["status"] in {"queued", "running"}
                and row["deadline_at"] is not None
                and row["deadline_at"] <= now
            )
            if deadline_reached:
                settled = self._settle_control(
                    connection,
                    row=row,
                    now=now,
                    worker_id=row["lease_owner"],
                )
                if settled is not None:
                    return settled
            if row["status"] == "queued":
                return self._mark_cancelled(
                    connection,
                    row=row,
                    reason_code=reason_code,
                    created_at=now,
                    worker_id=None,
                )
            if row["status"] != "running":
                raise InvalidJobTransition(
                    f"cannot cancel a planning job in status {row['status']}"
                )
            if row["cancel_requested_at"] is not None:
                return self._from_row(row)
            connection.execute(
                """
                UPDATE planning_jobs
                SET cancel_requested_at = ?, cancel_reason_code = ?, updated_at = ?
                WHERE job_id = ? AND status = 'running' AND cancel_requested_at IS NULL
                """,
                (now, reason_code, now, job_id),
            )
            self._append_event(
                connection,
                job_id=job_id,
                event_type="cancel_requested",
                attempt=int(row["attempt"]),
                worker_id=row["lease_owner"],
                payload={"reason_code": reason_code},
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._from_row(updated)

    def finalize_stopped(self, *, job_id: str, worker_id: str) -> PlanningJob:
        now = _timestamp(_utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is not None and row["status"] in {"cancelled", "timed_out"}:
                return self._from_row(row)
            if row is None:
                raise RuntimeError(
                    "job stop signal is missing, expired, or owned by another worker"
                )
            if (
                row["status"] != "running"
                or row["lease_owner"] != worker_id
                or row["lease_expires_at"] is None
                or row["lease_expires_at"] <= now
            ):
                raise RuntimeError(
                    "job stop signal is missing, expired, or owned by another worker"
                )
            settled = self._settle_control(
                connection,
                row=row,
                now=now,
                worker_id=worker_id,
            )
            if settled is None:
                raise RuntimeError("job cancellation or deadline is not pending")
            return settled

    def finalize_cancelled(self, *, job_id: str, worker_id: str) -> PlanningJob:
        """Backward-compatible alias; chronological control precedence still applies."""
        return self.finalize_stopped(job_id=job_id, worker_id=worker_id)

    def pending_stop_reason(self, job_id: str) -> str | None:
        """Return the control outcome a worker should observe at a safe boundary."""
        now = _timestamp(_utc_now())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        if row["status"] in {"cancelled", "timed_out"}:
            return str(row["status"])
        if row["status"] not in {"queued", "running"}:
            return None
        return self._control_outcome(row, now=now)

    def replay(
        self,
        *,
        job_id: str,
        request_id: str,
        idempotency_key: str,
        tenant_id: str | None = None,
        submitted_by: str = "system",
        tenant_active_job_limit: int | None = None,
        tenant_submission_limit_per_minute: int | None = None,
    ) -> PlanningJob:
        if not idempotency_key:
            raise ValueError("idempotency_key must not be empty")
        if tenant_id is not None:
            _validate_scope_identifier(tenant_id, field="tenant_id")
        _validate_scope_identifier(submitted_by, field="submitted_by")
        _validate_admission_limits(
            tenant_active_job_limit=tenant_active_job_limit,
            tenant_submission_limit_per_minute=(
                tenant_submission_limit_per_minute
            ),
        )
        now_value = _utc_now()
        now = _timestamp(now_value)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if tenant_id is None:
                source = connection.execute(
                    "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            else:
                source = connection.execute(
                    "SELECT * FROM planning_jobs WHERE job_id = ? AND tenant_id = ?",
                    (job_id, tenant_id),
                ).fetchone()
            if source is None:
                raise JobNotFound("planning job was not found")
            source_tenant = str(source["tenant_id"])
            existing = connection.execute(
                "SELECT * FROM planning_jobs WHERE tenant_id = ? AND idempotency_key = ?",
                (source_tenant, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["replayed_from_job_id"] != job_id
                    or existing["request_sha256"] != source["request_sha256"]
                ):
                    raise IdempotencyConflict(
                        "idempotency key already belongs to another operation"
                    )
                active_jobs, recent_submissions, _ = self._admission_snapshot(
                    connection,
                    tenant_id=source_tenant,
                    now_value=now_value,
                )
                self._append_admission_event(
                    connection,
                    tenant_id=source_tenant,
                    submitted_by=submitted_by,
                    request_id=request_id,
                    operation="replay",
                    decision="idempotent_reuse",
                    reason_code=None,
                    job_id=str(existing["job_id"]),
                    idempotency_key_present=True,
                    active_jobs_before=active_jobs,
                    recent_submissions_before=recent_submissions,
                    active_job_limit=tenant_active_job_limit,
                    submission_limit_per_minute=(
                        tenant_submission_limit_per_minute
                    ),
                    retry_after_seconds=None,
                    created_at=now,
                )
                return self._from_row(existing)
            if source["status"] not in REPLAYABLE_STATUSES:
                raise InvalidJobTransition(
                    f"cannot replay a planning job in status {source['status']}"
                )
            self._settle_expired_deadlines(connection, now=now)
            active_jobs, recent_submissions = self._enforce_tenant_admission(
                connection,
                tenant_id=source_tenant,
                submitted_by=submitted_by,
                request_id=request_id,
                operation="replay",
                idempotency_key_present=True,
                tenant_active_job_limit=tenant_active_job_limit,
                tenant_submission_limit_per_minute=(
                    tenant_submission_limit_per_minute
                ),
                now_value=now_value,
                now=now,
            )

            replay_job_id = f"job-{uuid.uuid4().hex}"
            deadline_seconds = int(source["deadline_seconds"])
            deadline_at = _timestamp(
                now_value + timedelta(seconds=deadline_seconds)
            )
            connection.execute(
                """
                INSERT INTO planning_jobs(
                    job_id, request_id, tenant_id, submitted_by,
                    status, request_json, request_sha256,
                    idempotency_key, attempt, max_attempts, priority, deadline_seconds,
                    deadline_at, available_at, created_at, updated_at,
                    replayed_from_job_id
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    replay_job_id,
                    request_id,
                    source_tenant,
                    submitted_by,
                    source["request_json"],
                    source["request_sha256"],
                    idempotency_key,
                    int(source["max_attempts"]),
                    int(source["priority"]),
                    deadline_seconds,
                    deadline_at,
                    now,
                    now,
                    now,
                    job_id,
                ),
            )
            self._append_event(
                connection,
                job_id=job_id,
                event_type="replay_requested",
                attempt=int(source["attempt"]),
                worker_id=None,
                payload={"replay_job_id": replay_job_id},
                created_at=now,
            )
            self._append_admission_event(
                connection,
                tenant_id=source_tenant,
                submitted_by=submitted_by,
                request_id=request_id,
                operation="replay",
                decision="admitted",
                reason_code=None,
                job_id=replay_job_id,
                idempotency_key_present=True,
                active_jobs_before=active_jobs,
                recent_submissions_before=recent_submissions,
                active_job_limit=tenant_active_job_limit,
                submission_limit_per_minute=tenant_submission_limit_per_minute,
                retry_after_seconds=None,
                created_at=now,
            )
            self._append_event(
                connection,
                job_id=replay_job_id,
                event_type="submitted",
                attempt=0,
                worker_id=None,
                payload={
                    "request_sha256": source["request_sha256"],
                    "idempotency_key_present": True,
                    "max_attempts": int(source["max_attempts"]),
                    "tenant_id": source_tenant,
                    "submitted_by": submitted_by,
                    "priority": int(source["priority"]),
                    "deadline_seconds": deadline_seconds,
                    "deadline_at": deadline_at,
                    "replayed_from_job_id": job_id,
                },
                created_at=now,
            )
            replayed = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (replay_job_id,)
            ).fetchone()
            return self._from_row(replayed)

    def claim_next(self, *, worker_id: str, lease_seconds: int = 300) -> PlanningJob | None:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        now_value = _utc_now()
        now = _timestamp(now_value)
        lease_until = _timestamp(now_value + timedelta(seconds=lease_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._settle_expired_deadlines(connection, now=now)
            self._cancel_expired_requested(connection, now=now)
            self._dead_letter_expired_exhausted(connection, now=now)
            row = connection.execute(
                """
                SELECT planning_jobs.*,
                    CASE
                        WHEN status = 'queued' THEN available_at
                        ELSE lease_expires_at
                    END AS scheduler_eligible_at,
                    bj_pal_effective_priority(
                        priority,
                        CASE
                            WHEN status = 'queued' THEN available_at
                            ELSE lease_expires_at
                        END,
                        ?
                    ) AS scheduler_effective_priority,
                    COALESCE(
                        planning_tenant_scheduler_state.last_claimed_event_id,
                        0
                    ) AS scheduler_tenant_last_claimed_event_id
                FROM planning_jobs
                LEFT JOIN planning_tenant_scheduler_state
                    ON planning_tenant_scheduler_state.tenant_id = planning_jobs.tenant_id
                WHERE (
                        status = 'queued'
                    AND available_at <= ?
                    AND attempt < max_attempts
                    AND (deadline_at IS NULL OR deadline_at > ?)
                ) OR (
                        status = 'running'
                    AND lease_expires_at <= ?
                    AND attempt < max_attempts
                    AND cancel_requested_at IS NULL
                    AND (deadline_at IS NULL OR deadline_at > ?)
                )
                ORDER BY
                    scheduler_effective_priority DESC,
                    scheduler_tenant_last_claimed_event_id ASC,
                    scheduler_eligible_at ASC,
                    created_at ASC,
                    job_id ASC
                LIMIT 1
                """,
                (now, now, now, now, now),
            ).fetchone()
            if row is None:
                return None
            previous_status = row["status"]
            attempt = int(row["attempt"]) + 1
            eligible_at = str(row["scheduler_eligible_at"])
            effective_priority, queue_wait_ms = compute_effective_priority(
                priority=int(row["priority"]),
                eligible_at=eligible_at,
                claimed_at=now,
            )
            if effective_priority != int(row["scheduler_effective_priority"]):
                raise RuntimeError("scheduler priority calculation is inconsistent")
            connection.execute(
                """
                UPDATE planning_jobs
                SET status = 'running', attempt = attempt + 1,
                    lease_owner = ?, lease_expires_at = ?, updated_at = ?,
                    error_code = NULL, error_message = NULL
                WHERE job_id = ?
                """,
                (worker_id, lease_until, now, row["job_id"]),
            )
            tenant_last_claimed_event_id = int(
                row["scheduler_tenant_last_claimed_event_id"]
            )
            claim_event_id = self._append_event(
                connection,
                job_id=row["job_id"],
                event_type="claimed" if previous_status == "queued" else "lease_reclaimed",
                attempt=attempt,
                worker_id=worker_id,
                payload={
                    "lease_expires_at": lease_until,
                    "max_attempts": int(row["max_attempts"]),
                    "scheduling_policy": SCHEDULING_POLICY_VERSION,
                    "priority_policy": PRIORITY_POLICY_VERSION,
                    "tenant_fairness_policy": TENANT_FAIRNESS_POLICY_VERSION,
                    "tenant_id": str(row["tenant_id"]),
                    "tenant_last_claimed_event_id_before": (
                        tenant_last_claimed_event_id
                    ),
                    "base_priority": int(row["priority"]),
                    "effective_priority": effective_priority,
                    "priority_aging_seconds": PRIORITY_AGING_SECONDS,
                    "eligible_at": eligible_at,
                    "queue_wait_ms": queue_wait_ms,
                },
                created_at=now,
            )
            connection.execute(
                """
                INSERT INTO planning_tenant_scheduler_state(
                    tenant_id, last_claimed_event_id, claim_count, updated_at
                ) VALUES (?, ?, 1, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    last_claimed_event_id = excluded.last_claimed_event_id,
                    claim_count = planning_tenant_scheduler_state.claim_count + 1,
                    updated_at = excluded.updated_at
                """,
                (row["tenant_id"], claim_event_id, now),
            )
            claimed = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            return self._from_row(claimed)

    def heartbeat(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> PlanningJob:
        """Extend an active lease and persist the ownership proof as an event."""
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        now_value = _utc_now()
        now = _timestamp(now_value)
        lease_until = _timestamp(now_value + timedelta(seconds=lease_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is not None and row["status"] in {"cancelled", "timed_out"}:
                return self._from_row(row)
            if row is None:
                raise RuntimeError("job lease is missing, expired, or owned by another worker")
            if (
                row["status"] != "running"
                or row["lease_owner"] != worker_id
                or row["lease_expires_at"] is None
                or row["lease_expires_at"] <= now
            ):
                raise RuntimeError("job lease is missing, expired, or owned by another worker")
            settled = self._settle_control(
                connection,
                row=row,
                now=now,
                worker_id=worker_id,
            )
            if settled is not None:
                return settled
            connection.execute(
                """
                UPDATE planning_jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?
                """,
                (lease_until, now, job_id, worker_id),
            )
            row = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            self._append_event(
                connection,
                job_id=job_id,
                event_type="heartbeat",
                attempt=int(row["attempt"]),
                worker_id=worker_id,
                payload={"lease_expires_at": lease_until},
                created_at=now,
            )
            return self._from_row(row)

    def retry_or_dead_letter(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        backoff_seconds: float,
    ) -> PlanningJob:
        """Schedule another attempt, or move an exhausted job to dead letter."""
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        now_value = _utc_now()
        now = _timestamp(now_value)
        available_at = _timestamp(now_value + timedelta(seconds=backoff_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is not None and row["status"] in {"cancelled", "timed_out"}:
                return self._from_row(row)
            if row is None:
                raise RuntimeError("job lease is missing, expired, or owned by another worker")
            if (
                row["status"] != "running"
                or row["lease_owner"] != worker_id
                or row["lease_expires_at"] is None
                or row["lease_expires_at"] <= now
            ):
                raise RuntimeError("job lease is missing, expired, or owned by another worker")

            settled = self._settle_control(
                connection,
                row=row,
                now=now,
                worker_id=worker_id,
            )
            if settled is not None:
                return settled

            attempt = int(row["attempt"])
            max_attempts = int(row["max_attempts"])
            exhausted = attempt >= max_attempts
            next_status = "dead_lettered" if exhausted else "queued"
            connection.execute(
                """
                UPDATE planning_jobs
                SET status = ?, available_at = ?, error_code = ?, error_message = ?,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    next_status,
                    available_at,
                    error_code,
                    error_message,
                    now,
                    job_id,
                ),
            )
            self._append_event(
                connection,
                job_id=job_id,
                event_type="dead_lettered" if exhausted else "retry_scheduled",
                attempt=attempt,
                worker_id=worker_id,
                payload={
                    "error_code": error_code,
                    "max_attempts": max_attempts,
                    "available_at": available_at,
                    "backoff_seconds": round(backoff_seconds, 3),
                },
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._from_row(updated)

    @staticmethod
    def _mark_cancelled(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        reason_code: str,
        created_at: str,
        worker_id: str | None,
    ) -> PlanningJob:
        connection.execute(
            """
            UPDATE planning_jobs
            SET status = 'cancelled',
                cancel_requested_at = COALESCE(cancel_requested_at, ?),
                cancelled_at = ?, cancel_reason_code = ?,
                lease_owner = NULL, lease_expires_at = NULL,
                error_code = NULL, error_message = NULL, updated_at = ?
            WHERE job_id = ? AND status IN ('queued', 'running')
            """,
            (created_at, created_at, reason_code, created_at, row["job_id"]),
        )
        PlanningJobRepository._append_event(
            connection,
            job_id=row["job_id"],
            event_type="cancelled",
            attempt=int(row["attempt"]),
            worker_id=worker_id,
            payload={"reason_code": reason_code},
            created_at=created_at,
        )
        updated = connection.execute(
            "SELECT * FROM planning_jobs WHERE job_id = ?", (row["job_id"],)
        ).fetchone()
        return PlanningJobRepository._from_row(updated)

    @staticmethod
    def _mark_timed_out(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        created_at: str,
        worker_id: str | None,
    ) -> PlanningJob:
        connection.execute(
            """
            UPDATE planning_jobs
            SET status = 'timed_out', lease_owner = NULL, lease_expires_at = NULL,
                artifact_id = NULL, artifact_sha256 = NULL, result_json = NULL,
                error_code = ?, error_message = ?, updated_at = ?
            WHERE job_id = ? AND status IN ('queued', 'running')
            """,
            (
                TIMEOUT_ERROR_CODE,
                TIMEOUT_ERROR_MESSAGE,
                created_at,
                row["job_id"],
            ),
        )
        PlanningJobRepository._append_event(
            connection,
            job_id=row["job_id"],
            event_type="timed_out",
            attempt=int(row["attempt"]),
            worker_id=worker_id,
            payload={
                "deadline_at": row["deadline_at"],
                "error_code": TIMEOUT_ERROR_CODE,
            },
            created_at=created_at,
        )
        updated = connection.execute(
            "SELECT * FROM planning_jobs WHERE job_id = ?", (row["job_id"],)
        ).fetchone()
        return PlanningJobRepository._from_row(updated)

    @staticmethod
    def _control_outcome(row: sqlite3.Row, *, now: str) -> str | None:
        """Resolve cancel/deadline races by the timestamp of the first signal."""
        cancel_at = row["cancel_requested_at"]
        deadline_at = row["deadline_at"]
        if cancel_at is not None and (
            deadline_at is None or str(cancel_at) <= str(deadline_at)
        ):
            return "cancelled"
        if deadline_at is not None and str(deadline_at) <= now:
            return "timed_out"
        return None

    @staticmethod
    def _settle_control(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        now: str,
        worker_id: str | None,
    ) -> PlanningJob | None:
        outcome = PlanningJobRepository._control_outcome(row, now=now)
        if outcome == "cancelled":
            return PlanningJobRepository._mark_cancelled(
                connection,
                row=row,
                reason_code=row["cancel_reason_code"] or "operator_requested",
                created_at=now,
                worker_id=worker_id,
            )
        if outcome == "timed_out":
            return PlanningJobRepository._mark_timed_out(
                connection,
                row=row,
                created_at=now,
                worker_id=worker_id,
            )
        return None

    @staticmethod
    def _settle_expired_deadlines(
        connection: sqlite3.Connection,
        *,
        now: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM planning_jobs
            WHERE status IN ('queued', 'running')
              AND deadline_at IS NOT NULL AND deadline_at <= ?
            ORDER BY created_at, job_id
            """,
            (now,),
        ).fetchall()
        for row in rows:
            PlanningJobRepository._settle_control(
                connection,
                row=row,
                now=now,
                worker_id=row["lease_owner"],
            )

    @staticmethod
    def _cancel_expired_requested(
        connection: sqlite3.Connection,
        *,
        now: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM planning_jobs
            WHERE status = 'running' AND cancel_requested_at IS NOT NULL
              AND lease_expires_at < ?
            ORDER BY created_at, job_id
            """,
            (now,),
        ).fetchall()
        for row in rows:
            PlanningJobRepository._mark_cancelled(
                connection,
                row=row,
                reason_code=row["cancel_reason_code"] or "operator_requested",
                created_at=now,
                worker_id=row["lease_owner"],
            )

    @staticmethod
    def _dead_letter_expired_exhausted(
        connection: sqlite3.Connection,
        *,
        now: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM planning_jobs
            WHERE status = 'running' AND lease_expires_at < ?
              AND attempt >= max_attempts
              AND cancel_requested_at IS NULL
            ORDER BY created_at, job_id
            """,
            (now,),
        ).fetchall()
        for row in rows:
            error_code = "lease_expired_attempts_exhausted"
            connection.execute(
                """
                UPDATE planning_jobs
                SET status = 'dead_lettered', error_code = ?,
                    error_message = ?, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (
                    error_code,
                    "Worker lease expired after the final attempt.",
                    now,
                    row["job_id"],
                ),
            )
            PlanningJobRepository._append_event(
                connection,
                job_id=row["job_id"],
                event_type="dead_lettered",
                attempt=int(row["attempt"]),
                worker_id=row["lease_owner"],
                payload={
                    "error_code": error_code,
                    "max_attempts": int(row["max_attempts"]),
                },
                created_at=now,
            )

    def succeed(self, *, job_id: str, worker_id: str, result_payload: dict) -> PlanningJob:
        result_json = _canonical_json(result_payload)
        artifact_sha = _sha256(result_json)
        artifact_id = f"artifact-{job_id.removeprefix('job-')}"
        return self._finish(
            job_id=job_id,
            worker_id=worker_id,
            status="succeeded",
            artifact_id=artifact_id,
            artifact_sha256=artifact_sha,
            result_json=result_json,
            error_code=None,
            error_message=None,
        )

    def fail(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
    ) -> PlanningJob:
        return self._finish(
            job_id=job_id,
            worker_id=worker_id,
            status="failed",
            artifact_id=None,
            artifact_sha256=None,
            result_json=None,
            error_code=error_code,
            error_message=error_message,
        )

    def _finish(
        self,
        *,
        job_id: str,
        worker_id: str,
        status: str,
        artifact_id: str | None,
        artifact_sha256: str | None,
        result_json: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> PlanningJob:
        now = _timestamp(_utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is not None and row["status"] in {"cancelled", "timed_out"}:
                return self._from_row(row)
            if row is None:
                raise RuntimeError("job lease is missing, expired, or owned by another worker")
            if (
                row["status"] != "running"
                or row["lease_owner"] != worker_id
                or row["lease_expires_at"] is None
                or row["lease_expires_at"] <= now
            ):
                raise RuntimeError("job lease is missing, expired, or owned by another worker")
            settled = self._settle_control(
                connection,
                row=row,
                now=now,
                worker_id=worker_id,
            )
            if settled is not None:
                return settled
            connection.execute(
                """
                UPDATE planning_jobs
                SET status = ?, artifact_id = ?, artifact_sha256 = ?, result_json = ?,
                    error_code = ?, error_message = ?, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?
                  AND lease_expires_at > ?
                """,
                (
                    status,
                    artifact_id,
                    artifact_sha256,
                    result_json,
                    error_code,
                    error_message,
                    now,
                    job_id,
                    worker_id,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM planning_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            event_payload = (
                {
                    "artifact_id": artifact_id,
                    "artifact_sha256": artifact_sha256,
                }
                if status == "succeeded"
                else {"error_code": error_code}
            )
            self._append_event(
                connection,
                job_id=job_id,
                event_type=status,
                attempt=int(row["attempt"]),
                worker_id=worker_id,
                payload=event_payload,
                created_at=now,
            )
            return self._from_row(row)

    def list_events(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> tuple[PlanningJobEvent, ...]:
        if tenant_id is not None:
            _validate_scope_identifier(tenant_id, field="tenant_id")
        if after_event_id < 0:
            raise ValueError("after_event_id must be non-negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            if tenant_id is not None:
                owner = connection.execute(
                    "SELECT 1 FROM planning_jobs WHERE job_id = ? AND tenant_id = ?",
                    (job_id, tenant_id),
                ).fetchone()
                if owner is None:
                    raise JobNotFound("planning job was not found")
            rows = connection.execute(
                """
                SELECT * FROM planning_job_events
                WHERE job_id = ? AND event_id > ?
                ORDER BY event_id
                LIMIT ?
                """,
                (job_id, after_event_id, limit),
            ).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def workload_evidence(
        self,
        *,
        tenant_id: str,
        window_start: str,
        window_end: str,
    ) -> tuple[PlanningJobWindowEvidence, ...]:
        """Read one exact tenant/window without loading request or result payloads."""
        _validate_scope_identifier(tenant_id, field="tenant_id")
        start, end = validate_window(window_start, window_end)
        canonical_start = canonical_timestamp(start)
        canonical_end = canonical_timestamp(end)
        with self._connect() as connection:
            connection.execute("BEGIN")
            jobs = connection.execute(
                """
                SELECT job_id, status, created_at
                FROM planning_jobs
                WHERE tenant_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY created_at, job_id
                LIMIT ?
                """,
                (
                    tenant_id,
                    canonical_start,
                    canonical_end,
                    MAX_WORKLOAD_JOBS + 1,
                ),
            ).fetchall()
            if len(jobs) > MAX_WORKLOAD_JOBS:
                raise JobWorkloadEvidenceLimitExceeded(
                    "durable workload job limit exceeded; aggregate was not truncated"
                )
            if not jobs:
                return ()
            events = connection.execute(
                """
                WITH selected_jobs AS (
                    SELECT job_id
                    FROM planning_jobs
                    WHERE tenant_id = ? AND created_at >= ? AND created_at < ?
                    ORDER BY created_at, job_id
                    LIMIT ?
                )
                SELECT event.*
                FROM planning_job_events AS event
                INNER JOIN selected_jobs AS selected ON selected.job_id = event.job_id
                WHERE event.created_at < ?
                ORDER BY event.job_id, event.event_id
                LIMIT ?
                """,
                (
                    tenant_id,
                    canonical_start,
                    canonical_end,
                    MAX_WORKLOAD_JOBS,
                    canonical_end,
                    MAX_WORKLOAD_EVENTS + 1,
                ),
            ).fetchall()
        if len(events) > MAX_WORKLOAD_EVENTS:
            raise JobWorkloadEvidenceLimitExceeded(
                "durable workload event limit exceeded; aggregate was not truncated"
            )
        grouped: dict[str, list[PlanningJobEvent]] = {
            str(row["job_id"]): [] for row in jobs
        }
        for row in events:
            grouped[str(row["job_id"])].append(self._event_from_row(row))
        return tuple(
            PlanningJobWindowEvidence(
                job_id=str(row["job_id"]),
                status=derive_status_from_events(grouped[str(row["job_id"])]),
                created_at=str(row["created_at"]),
                events=tuple(grouped[str(row["job_id"])]),
            )
            for row in jobs
        )

    def list_admission_events(
        self,
        *,
        tenant_id: str,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> tuple[PlanningAdmissionEvent, ...]:
        _validate_scope_identifier(tenant_id, field="tenant_id")
        if after_event_id < 0:
            raise ValueError("after_event_id must be non-negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM planning_job_admission_events
                WHERE tenant_id = ? AND event_id > ?
                ORDER BY event_id
                LIMIT ?
                """,
                (tenant_id, after_event_id, limit),
            ).fetchall()
        return tuple(self._admission_event_from_row(row) for row in rows)

    @staticmethod
    def _append_admission_event(
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        submitted_by: str,
        request_id: str,
        operation: str,
        decision: str,
        reason_code: str | None,
        job_id: str | None,
        idempotency_key_present: bool,
        active_jobs_before: int,
        recent_submissions_before: int,
        active_job_limit: int | None,
        submission_limit_per_minute: int | None,
        retry_after_seconds: int | None,
        created_at: str,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO planning_job_admission_events(
                policy_version, tenant_id, submitted_by, request_id,
                operation, decision, reason_code, job_id,
                idempotency_key_present, active_jobs_before,
                recent_submissions_before, active_job_limit,
                submission_limit_per_minute, submission_window_seconds,
                retry_after_seconds, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ADMISSION_POLICY_VERSION,
                tenant_id,
                submitted_by,
                request_id,
                operation,
                decision,
                reason_code,
                job_id,
                int(idempotency_key_present),
                active_jobs_before,
                recent_submissions_before,
                active_job_limit,
                submission_limit_per_minute,
                SUBMISSION_RATE_WINDOW_SECONDS,
                retry_after_seconds,
                created_at,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        event_type: str,
        attempt: int,
        worker_id: str | None,
        payload: dict,
        created_at: str,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO planning_job_events(
                job_id, event_type, attempt, worker_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                event_type,
                attempt,
                worker_id,
                _canonical_json(payload),
                created_at,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _admission_event_from_row(row: sqlite3.Row) -> PlanningAdmissionEvent:
        return PlanningAdmissionEvent(
            event_id=int(row["event_id"]),
            policy_version=str(row["policy_version"]),
            tenant_id=str(row["tenant_id"]),
            submitted_by=str(row["submitted_by"]),
            request_id=str(row["request_id"]),
            operation=row["operation"],
            decision=row["decision"],
            reason_code=row["reason_code"],
            job_id=row["job_id"],
            idempotency_key_present=bool(row["idempotency_key_present"]),
            active_jobs_before=int(row["active_jobs_before"]),
            recent_submissions_before=int(row["recent_submissions_before"]),
            active_job_limit=(
                int(row["active_job_limit"])
                if row["active_job_limit"] is not None
                else None
            ),
            submission_limit_per_minute=(
                int(row["submission_limit_per_minute"])
                if row["submission_limit_per_minute"] is not None
                else None
            ),
            submission_window_seconds=int(row["submission_window_seconds"]),
            retry_after_seconds=(
                int(row["retry_after_seconds"])
                if row["retry_after_seconds"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PlanningJob:
        return PlanningJob(
            job_id=row["job_id"],
            request_id=row["request_id"],
            tenant_id=row["tenant_id"],
            submitted_by=row["submitted_by"],
            status=row["status"],
            request_payload=json.loads(row["request_json"]),
            request_sha256=row["request_sha256"],
            idempotency_key=row["idempotency_key"],
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            priority=int(row["priority"]),
            deadline_seconds=int(row["deadline_seconds"]),
            deadline_at=row["deadline_at"],
            available_at=row["available_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            cancel_requested_at=row["cancel_requested_at"],
            cancelled_at=row["cancelled_at"],
            cancel_reason_code=row["cancel_reason_code"],
            replayed_from_job_id=row["replayed_from_job_id"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            artifact_id=row["artifact_id"],
            artifact_sha256=row["artifact_sha256"],
            result_payload=json.loads(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> PlanningJobEvent:
        return PlanningJobEvent(
            event_id=int(row["event_id"]),
            job_id=row["job_id"],
            event_type=row["event_type"],
            attempt=int(row["attempt"]),
            worker_id=row["worker_id"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _summary_from_row(row: sqlite3.Row) -> PlanningJobSummary:
        return PlanningJobSummary(
            job_id=row["job_id"],
            request_id=row["request_id"],
            tenant_id=row["tenant_id"],
            submitted_by=row["submitted_by"],
            status=row["status"],
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            priority=int(row["priority"]),
            deadline_seconds=int(row["deadline_seconds"]),
            deadline_at=row["deadline_at"],
            available_at=row["available_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            cancel_requested_at=row["cancel_requested_at"],
            cancelled_at=row["cancelled_at"],
            cancel_reason_code=row["cancel_reason_code"],
            replayed_from_job_id=row["replayed_from_job_id"],
            artifact_id=row["artifact_id"],
            error_code=row["error_code"],
        )
