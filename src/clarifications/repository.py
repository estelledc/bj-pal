"""SQLite persistence and fencing for clarification continuations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import ClarificationOption, ClarificationSession


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CLARIFICATION_DB = ROOT / "runtime" / "clarifications.db"
CONTINUATION_ID_PATTERN = re.compile(r"^clar-[a-f0-9]{32}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clarification_sessions (
    continuation_id TEXT PRIMARY KEY,
    delivery TEXT NOT NULL CHECK(delivery IN ('sync', 'job')),
    status TEXT NOT NULL CHECK(
        status IN ('pending', 'resolved', 'executing', 'completed', 'expired')
    ),
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    decision_sha256 TEXT NOT NULL,
    constraints_json TEXT,
    job_policy_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    resolution_json TEXT,
    resolution_sha256 TEXT,
    resolved_request_json TEXT,
    resolved_request_sha256 TEXT,
    result_json TEXT,
    result_sha256 TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resolved_at TEXT,
    completed_at TEXT,
    execution_owner TEXT,
    execution_lease_expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_clarification_expiry
ON clarification_sessions(status, expires_at);
"""


class ClarificationNotFound(LookupError):
    pass


class ClarificationExpired(ValueError):
    pass


class ClarificationResolutionConflict(ValueError):
    pass


class ClarificationInProgress(RuntimeError):
    pass


class InvalidClarificationTransition(ValueError):
    pass


class ClarificationIntegrityError(ValueError):
    """Persisted continuation evidence no longer matches its hash chain."""


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ClarificationRepository:
    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get("BJ_PAL_CLARIFICATION_DB")
        self.path = path or (Path(configured) if configured else DEFAULT_CLARIFICATION_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_integrity_columns(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _migrate_integrity_columns(connection: sqlite3.Connection) -> None:
        """Add and backfill the v5.6 hash chain for early local v5.6 databases."""
        existing = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(clarification_sessions)"
            ).fetchall()
        }
        for column in (
            "resolution_sha256",
            "resolved_request_sha256",
            "result_sha256",
        ):
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE clarification_sessions ADD COLUMN {column} TEXT"
                )
        rows = connection.execute(
            """
            SELECT continuation_id, resolution_json, resolution_sha256,
                   resolved_request_json, resolved_request_sha256,
                   result_json, result_sha256
            FROM clarification_sessions
            """
        ).fetchall()
        for row in rows:
            updates: dict[str, str] = {}
            for json_column, hash_column in (
                ("resolution_json", "resolution_sha256"),
                ("resolved_request_json", "resolved_request_sha256"),
                ("result_json", "result_sha256"),
            ):
                raw = row[json_column]
                if raw is not None and row[hash_column] is None:
                    updates[hash_column] = sha256_json(json.loads(str(raw)))
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                connection.execute(
                    f"UPDATE clarification_sessions SET {assignments} "
                    "WHERE continuation_id = ?",
                    (*updates.values(), row["continuation_id"]),
                )

    def issue(
        self,
        *,
        delivery: str,
        request_payload: dict[str, Any],
        decision_payload: dict[str, Any],
        constraints_payload: dict[str, Any] | None,
        job_policy: dict[str, Any],
        options: tuple[ClarificationOption, ...],
        ttl_seconds: int,
    ) -> ClarificationSession:
        if delivery not in {"sync", "job"}:
            raise ValueError("clarification delivery must be sync or job")
        if not 60 <= ttl_seconds <= 86_400:
            raise ValueError("clarification ttl_seconds must be between 60 and 86400")
        if not 2 <= len(options) <= 3:
            raise ValueError("clarification continuation requires two or three options")
        if len({item.option_id for item in options}) != len(options):
            raise ValueError("clarification option IDs must be unique")
        self.purge_expired(retention_seconds=86_400)
        now = utc_now()
        continuation_id = f"clar-{uuid.uuid4().hex}"
        request_json = canonical_json(request_payload)
        request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        options_payload = [item.to_dict() for item in options]
        decision_evidence = {
            "request_sha256": request_sha256,
            "delivery": delivery,
            "requirements": decision_payload,
            "constraints": constraints_payload,
            "job_policy": job_policy,
            "options": options_payload,
        }
        decision_json = canonical_json(decision_payload)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO clarification_sessions (
                    continuation_id, delivery, status,
                    request_json, request_sha256,
                    decision_json, decision_sha256, constraints_json,
                    job_policy_json, options_json,
                    created_at, expires_at
                ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    continuation_id,
                    delivery,
                    request_json,
                    request_sha256,
                    decision_json,
                    sha256_json(decision_evidence),
                    canonical_json(constraints_payload) if constraints_payload else None,
                    canonical_json(job_policy),
                    canonical_json(options_payload),
                    timestamp(now),
                    timestamp(now + timedelta(seconds=ttl_seconds)),
                ),
            )
        session = self.get(continuation_id)
        assert session is not None
        return session

    def get(self, continuation_id: str) -> ClarificationSession | None:
        if not CONTINUATION_ID_PATTERN.fullmatch(continuation_id):
            return None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM clarification_sessions WHERE continuation_id = ?",
                (continuation_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            if row["status"] != "expired" and _is_expired(row["expires_at"]):
                connection.execute(
                    """
                    UPDATE clarification_sessions
                    SET status = 'expired', execution_owner = NULL,
                        execution_lease_expires_at = NULL
                    WHERE continuation_id = ?
                    """,
                    (continuation_id,),
                )
                row = connection.execute(
                    "SELECT * FROM clarification_sessions WHERE continuation_id = ?",
                    (continuation_id,),
                ).fetchone()
            connection.commit()
        return _row_to_session(row)

    def resolve(
        self,
        *,
        continuation_id: str,
        resolution_payload: dict[str, Any],
        resolved_request_payload: dict[str, Any],
    ) -> ClarificationSession:
        resolution_json = canonical_json(resolution_payload)
        request_json = canonical_json(resolved_request_payload)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._required_row(connection, continuation_id)
            if row["status"] != "expired" and _is_expired(row["expires_at"]):
                connection.execute(
                    "UPDATE clarification_sessions SET status = 'expired' WHERE continuation_id = ?",
                    (continuation_id,),
                )
                connection.commit()
                raise ClarificationExpired("clarification continuation expired")
            if row["status"] == "pending":
                now = timestamp(utc_now())
                connection.execute(
                    """
                    UPDATE clarification_sessions
                    SET status = 'resolved', resolution_json = ?,
                        resolution_sha256 = ?, resolved_request_json = ?,
                        resolved_request_sha256 = ?, resolved_at = ?
                    WHERE continuation_id = ? AND status = 'pending'
                    """,
                    (
                        resolution_json,
                        hashlib.sha256(resolution_json.encode("utf-8")).hexdigest(),
                        request_json,
                        hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
                        now,
                        continuation_id,
                    ),
                )
            elif row["resolution_json"] != resolution_json:
                connection.rollback()
                raise ClarificationResolutionConflict(
                    "clarification was already resolved with a different answer"
                )
            elif row["resolved_request_json"] != request_json:
                connection.rollback()
                raise ClarificationResolutionConflict(
                    "clarification resolved request does not match the stored answer"
                )
            row = self._required_row(connection, continuation_id)
            connection.commit()
        return _row_to_session(row)

    def claim_execution(
        self,
        *,
        continuation_id: str,
        owner: str,
        lease_seconds: int = 900,
    ) -> ClarificationSession:
        if not owner or len(owner) > 128:
            raise ValueError("clarification execution owner is invalid")
        if not 1 <= lease_seconds <= 86_400:
            raise ValueError("clarification execution lease is invalid")
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._required_row(connection, continuation_id)
            if row["status"] == "expired" or _is_expired(row["expires_at"]):
                connection.execute(
                    "UPDATE clarification_sessions SET status = 'expired' WHERE continuation_id = ?",
                    (continuation_id,),
                )
                connection.commit()
                raise ClarificationExpired("clarification continuation expired")
            if row["status"] == "completed":
                connection.commit()
                return _row_to_session(row)
            if row["status"] == "executing" and not _is_expired(
                row["execution_lease_expires_at"]
            ):
                connection.rollback()
                raise ClarificationInProgress("clarification continuation is executing")
            if row["status"] not in {"resolved", "executing"}:
                connection.rollback()
                raise InvalidClarificationTransition(
                    "clarification must be resolved before execution"
                )
            connection.execute(
                """
                UPDATE clarification_sessions
                SET status = 'executing', execution_owner = ?,
                    execution_lease_expires_at = ?
                WHERE continuation_id = ?
                """,
                (
                    owner,
                    timestamp(now + timedelta(seconds=lease_seconds)),
                    continuation_id,
                ),
            )
            row = self._required_row(connection, continuation_id)
            connection.commit()
        return _row_to_session(row)

    def complete(
        self,
        *,
        continuation_id: str,
        owner: str,
        result_payload: dict[str, Any],
    ) -> ClarificationSession:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._required_row(connection, continuation_id)
            if row["status"] == "completed":
                connection.commit()
                return _row_to_session(row)
            if row["status"] != "executing" or row["execution_owner"] != owner:
                connection.rollback()
                raise InvalidClarificationTransition(
                    "clarification execution owner lost its lease"
                )
            connection.execute(
                """
                UPDATE clarification_sessions
                SET status = 'completed', result_json = ?, result_sha256 = ?,
                    completed_at = ?,
                    execution_owner = NULL, execution_lease_expires_at = NULL
                WHERE continuation_id = ? AND status = 'executing'
                    AND execution_owner = ?
                """,
                (
                    canonical_json(result_payload),
                    sha256_json(result_payload),
                    timestamp(utc_now()),
                    continuation_id,
                    owner,
                ),
            )
            row = self._required_row(connection, continuation_id)
            connection.commit()
        return _row_to_session(row)

    def release_execution(self, *, continuation_id: str, owner: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE clarification_sessions
                SET status = 'resolved', execution_owner = NULL,
                    execution_lease_expires_at = NULL
                WHERE continuation_id = ? AND status = 'executing'
                    AND execution_owner = ?
                """,
                (continuation_id, owner),
            )

    def purge_expired(self, *, retention_seconds: int = 86_400) -> int:
        """Delete expired raw requests after a bounded diagnostic retention window."""
        if not 0 <= retention_seconds <= 30 * 86_400:
            raise ValueError("clarification retention_seconds is invalid")
        cutoff = timestamp(utc_now() - timedelta(seconds=retention_seconds))
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM clarification_sessions WHERE expires_at <= ?",
                (cutoff,),
            )
            return int(cursor.rowcount)

    @staticmethod
    def _required_row(
        connection: sqlite3.Connection,
        continuation_id: str,
    ) -> sqlite3.Row:
        if not CONTINUATION_ID_PATTERN.fullmatch(continuation_id):
            raise ClarificationNotFound("clarification continuation not found")
        row = connection.execute(
            "SELECT * FROM clarification_sessions WHERE continuation_id = ?",
            (continuation_id,),
        ).fetchone()
        if row is None:
            raise ClarificationNotFound("clarification continuation not found")
        return row


def _is_expired(value: str | None) -> bool:
    if not value:
        return True
    return datetime.fromisoformat(value.replace("Z", "+00:00")) <= utc_now()


def _row_to_session(row: sqlite3.Row) -> ClarificationSession:
    request_payload = _required_json_object(
        row["request_json"],
        label="request",
    )
    request_sha256 = hashlib.sha256(
        canonical_json(request_payload).encode("utf-8")
    ).hexdigest()
    if request_sha256 != row["request_sha256"]:
        raise ClarificationIntegrityError("clarification request SHA-256 mismatch")
    decision_payload = _required_json_object(
        row["decision_json"],
        label="decision",
    )
    constraints_payload = (
        _required_json_object(row["constraints_json"], label="constraints")
        if row["constraints_json"]
        else None
    )
    job_policy = _required_json_object(
        row["job_policy_json"],
        label="job policy",
    )
    options_payload = _required_json_array(
        row["options_json"],
        label="options",
    )
    decision_evidence = {
        "request_sha256": request_sha256,
        "delivery": row["delivery"],
        "requirements": decision_payload,
        "constraints": constraints_payload,
        "job_policy": job_policy,
        "options": options_payload,
    }
    if sha256_json(decision_evidence) != row["decision_sha256"]:
        raise ClarificationIntegrityError("clarification decision SHA-256 mismatch")
    resolution_payload = _verified_optional_json(
        row,
        json_column="resolution_json",
        hash_column="resolution_sha256",
        label="resolution",
    )
    resolved_request_payload = _verified_optional_json(
        row,
        json_column="resolved_request_json",
        hash_column="resolved_request_sha256",
        label="resolved request",
    )
    result_payload = _verified_optional_json(
        row,
        json_column="result_json",
        hash_column="result_sha256",
        label="result",
    )
    return ClarificationSession(
        continuation_id=row["continuation_id"],
        delivery=row["delivery"],
        status=row["status"],
        request_payload=request_payload,
        request_sha256=request_sha256,
        decision_payload=decision_payload,
        decision_sha256=row["decision_sha256"],
        constraints_payload=constraints_payload,
        job_policy=job_policy,
        options=_restore_options(options_payload),
        resolution_payload=resolution_payload,
        resolution_sha256=row["resolution_sha256"],
        resolved_request_payload=resolved_request_payload,
        resolved_request_sha256=row["resolved_request_sha256"],
        result_payload=result_payload,
        result_sha256=row["result_sha256"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        resolved_at=row["resolved_at"],
        completed_at=row["completed_at"],
        execution_owner=row["execution_owner"],
        execution_lease_expires_at=row["execution_lease_expires_at"],
    )


def _verified_optional_json(
    row: sqlite3.Row,
    *,
    json_column: str,
    hash_column: str,
    label: str,
) -> dict[str, Any] | None:
    raw = row[json_column]
    recorded = row[hash_column]
    if raw is None:
        if recorded is not None:
            raise ClarificationIntegrityError(
                f"clarification {label} SHA-256 has no payload"
            )
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClarificationIntegrityError(
            f"clarification {label} payload is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ClarificationIntegrityError(
            f"clarification {label} payload must be an object"
        )
    if recorded != sha256_json(payload):
        raise ClarificationIntegrityError(
            f"clarification {label} SHA-256 mismatch"
        )
    return payload


def _required_json_object(raw: str, *, label: str) -> dict[str, Any]:
    payload = _required_json(raw, label=label)
    if not isinstance(payload, dict):
        raise ClarificationIntegrityError(
            f"clarification {label} payload must be an object"
        )
    return payload


def _required_json_array(raw: str, *, label: str) -> list[Any]:
    payload = _required_json(raw, label=label)
    if not isinstance(payload, list):
        raise ClarificationIntegrityError(
            f"clarification {label} payload must be an array"
        )
    return payload


def _required_json(raw: str, *, label: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClarificationIntegrityError(
            f"clarification {label} payload is not valid JSON"
        ) from exc


def _restore_options(payload: list[Any]) -> tuple[ClarificationOption, ...]:
    try:
        options = tuple(ClarificationOption.from_dict(item) for item in payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise ClarificationIntegrityError(
            "clarification options payload is invalid"
        ) from exc
    if not 2 <= len(options) <= 3:
        raise ClarificationIntegrityError(
            "clarification options payload must contain two or three items"
        )
    return options
