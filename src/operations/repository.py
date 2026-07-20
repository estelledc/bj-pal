"""SQLite state machine for quote-bound, approval-gated sandbox operations."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import (
    OperationEvent,
    OperationQuote,
    OperationReconciliation,
    SideEffectOperation,
)


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OPERATION_DB = ROOT / "runtime" / "side_effect_operations.db"
OPERATION_POLICY_VERSION = "approval_gated_operation_v1"
RECEIPT_VERSION = "side_effect_receipt_v1"
STATUS_LOOKUP_VERSION = "side_effect_status_lookup_v1"
SUPPORTED_OPERATION_KINDS = frozenset({"restaurant_booking"})
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


SCHEMA = """
CREATE TABLE IF NOT EXISTS side_effect_operations (
    operation_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    operation_kind TEXT NOT NULL CHECK(operation_kind IN ('restaurant_booking')),
    status TEXT NOT NULL CHECK(
        status IN (
            'pending_approval', 'approved', 'denied', 'expired',
            'executing', 'succeeded', 'failed', 'uncertain'
        )
    ),
    action_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    quote_provider TEXT NOT NULL,
    quote_reference TEXT NOT NULL,
    quote_valid_until TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    quote_amount_minor INTEGER NOT NULL CHECK(quote_amount_minor >= 0),
    quote_terms_sha256 TEXT NOT NULL,
    quote_sandbox INTEGER NOT NULL CHECK(quote_sandbox IN (0, 1)),
    approval_sha256 TEXT NOT NULL,
    approval_expires_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    denied_by TEXT,
    denied_at TEXT,
    denial_reason_code TEXT,
    execution_owner TEXT,
    execution_lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt IN (0, 1)),
    provider_operation_id TEXT,
    receipt_json TEXT,
    receipt_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_side_effect_operation_tenant_idempotency
ON side_effect_operations(tenant_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_side_effect_operation_claim
ON side_effect_operations(status, created_at, operation_id);

CREATE TABLE IF NOT EXISTS side_effect_operation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(
        event_type IN (
            'requested', 'request_reused', 'approved', 'denied', 'expired',
            'execution_started', 'execution_succeeded', 'execution_failed',
            'execution_uncertain'
        )
    ),
    actor_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(operation_id) REFERENCES side_effect_operations(operation_id)
);
CREATE INDEX IF NOT EXISTS idx_side_effect_operation_events_replay
ON side_effect_operation_events(operation_id, event_id);

CREATE TRIGGER IF NOT EXISTS side_effect_operation_events_no_update
BEFORE UPDATE ON side_effect_operation_events
BEGIN
    SELECT RAISE(ABORT, 'side effect operation events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS side_effect_operation_events_no_delete
BEFORE DELETE ON side_effect_operation_events
BEGIN
    SELECT RAISE(ABORT, 'side effect operation events are append-only');
END;

CREATE TABLE IF NOT EXISTS side_effect_operation_reconciliations (
    reconciliation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(
        outcome IN ('confirmed', 'rejected', 'still_unknown', 'not_found')
    ),
    provider_operation_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    receipt_sha256 TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(operation_id) REFERENCES side_effect_operations(operation_id)
);
CREATE INDEX IF NOT EXISTS idx_side_effect_reconciliation_replay
ON side_effect_operation_reconciliations(operation_id, reconciliation_id);
CREATE TRIGGER IF NOT EXISTS side_effect_operation_reconciliations_no_update
BEFORE UPDATE ON side_effect_operation_reconciliations
BEGIN
    SELECT RAISE(ABORT, 'side effect operation reconciliations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS side_effect_operation_reconciliations_no_delete
BEFORE DELETE ON side_effect_operation_reconciliations
BEGIN
    SELECT RAISE(ABORT, 'side effect operation reconciliations are append-only');
END;
"""


class OperationNotFound(LookupError):
    """The operation does not exist in the caller's tenant."""


class OperationIdempotencyConflict(ValueError):
    """The same tenant key was reused for a different quote-bound request."""


class OperationApprovalConflict(ValueError):
    """The approval fingerprint no longer matches the stored operation."""


class OperationSelfApprovalForbidden(PermissionError):
    """The principal that requested an operation cannot approve or deny it."""


class InvalidOperationTransition(ValueError):
    """The requested state transition is not allowed."""


class OperationExpired(RuntimeError):
    """The approval or its bound quote expired before execution."""


class OperationLeaseLost(RuntimeError):
    """The executor no longer owns a live operation lease."""


class OperationReconciliationUnavailable(RuntimeError):
    """The operation cannot be resolved through provider status lookup."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_identifier(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not SAFE_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must contain 1-128 safe characters")


def _validate_quote(quote: OperationQuote, *, now: datetime) -> None:
    _validate_identifier(quote.provider, field="quote.provider")
    _validate_identifier(quote.reference, field="quote.reference")
    if quote.provider != "bj-pal-sandbox" or quote.sandbox is not True:
        raise ValueError("v1 operations accept only the explicit sandbox provider")
    if not CURRENCY_PATTERN.fullmatch(quote.currency):
        raise ValueError("quote.currency must use a three-letter uppercase code")
    if isinstance(quote.amount_minor, bool) or not isinstance(quote.amount_minor, int):
        raise ValueError("quote.amount_minor must be a non-negative integer")
    if quote.amount_minor < 0:
        raise ValueError("quote.amount_minor must be non-negative")
    if not SHA256_PATTERN.fullmatch(quote.terms_sha256):
        raise ValueError("quote.terms_sha256 must be lowercase SHA-256")
    if _parse_timestamp(quote.valid_until) <= now:
        raise ValueError("quote.valid_until must be in the future")


class SideEffectOperationRepository:
    def __init__(self, path: Path | str = DEFAULT_OPERATION_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def request(
        self,
        *,
        request_id: str,
        tenant_id: str,
        requested_by: str,
        operation_kind: str,
        action_payload: dict,
        quote: OperationQuote,
        idempotency_key: str,
        approval_ttl_seconds: int = 300,
    ) -> SideEffectOperation:
        for field, value in (
            ("request_id", request_id),
            ("tenant_id", tenant_id),
            ("requested_by", requested_by),
            ("idempotency_key", idempotency_key),
        ):
            _validate_identifier(value, field=field)
        if operation_kind not in SUPPORTED_OPERATION_KINDS:
            raise ValueError("unsupported side-effect operation kind")
        if not isinstance(action_payload, dict) or not action_payload:
            raise ValueError("action_payload must be a non-empty object")
        if not 1 <= approval_ttl_seconds <= 1800:
            raise ValueError("approval_ttl_seconds must be between 1 and 1800")
        now_value = _utc_now()
        now = _timestamp(now_value)
        _validate_quote(quote, now=now_value)
        request_payload = {
            "version": OPERATION_POLICY_VERSION,
            "operation_kind": operation_kind,
            "action": action_payload,
            "quote": quote.to_dict(),
        }
        request_sha256 = _sha256_text(_canonical_json(request_payload))
        approval_expires_at = _timestamp(
            min(
                now_value + timedelta(seconds=approval_ttl_seconds),
                _parse_timestamp(quote.valid_until),
            )
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._settle_expired(connection, now=now)
            self._settle_abandoned_executions(connection, now=now)
            existing = connection.execute(
                "SELECT * FROM side_effect_operations "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise OperationIdempotencyConflict(
                        "idempotency key belongs to another quote-bound request"
                    )
                self._append_event(
                    connection,
                    operation_id=str(existing["operation_id"]),
                    event_type="request_reused",
                    actor_id=requested_by,
                    payload={"request_id": request_id},
                    created_at=now,
                )
                return self._from_row(existing)
            operation_id = f"op-{uuid.uuid4().hex}"
            approval_payload = {
                "version": OPERATION_POLICY_VERSION,
                "operation_id": operation_id,
                "tenant_id": tenant_id,
                "request_sha256": request_sha256,
                "approval_expires_at": approval_expires_at,
            }
            approval_sha256 = _sha256_text(_canonical_json(approval_payload))
            connection.execute(
                """
                INSERT INTO side_effect_operations(
                    operation_id, request_id, tenant_id, requested_by,
                    operation_kind, status, action_json, request_sha256,
                    idempotency_key, quote_provider, quote_reference,
                    quote_valid_until, quote_currency, quote_amount_minor,
                    quote_terms_sha256, quote_sandbox, approval_sha256,
                    approval_expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending_approval', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1,
                          ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    request_id,
                    tenant_id,
                    requested_by,
                    operation_kind,
                    _canonical_json(action_payload),
                    request_sha256,
                    idempotency_key,
                    quote.provider,
                    quote.reference,
                    quote.valid_until,
                    quote.currency,
                    quote.amount_minor,
                    quote.terms_sha256,
                    approval_sha256,
                    approval_expires_at,
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                operation_id=operation_id,
                event_type="requested",
                actor_id=requested_by,
                payload={
                    "policy_version": OPERATION_POLICY_VERSION,
                    "request_sha256": request_sha256,
                    "approval_sha256": approval_sha256,
                    "approval_expires_at": approval_expires_at,
                    "quote_reference": quote.reference,
                    "quote_valid_until": quote.valid_until,
                    "sandbox": True,
                },
                created_at=now,
            )
            row = connection.execute(
                "SELECT * FROM side_effect_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return self._from_row(row)

    def approve(
        self,
        *,
        operation_id: str,
        tenant_id: str,
        approved_by: str,
        expected_approval_sha256: str,
    ) -> SideEffectOperation:
        return self._decide(
            operation_id=operation_id,
            tenant_id=tenant_id,
            actor_id=approved_by,
            expected_approval_sha256=expected_approval_sha256,
            decision="approved",
            denial_reason_code=None,
        )

    def deny(
        self,
        *,
        operation_id: str,
        tenant_id: str,
        denied_by: str,
        expected_approval_sha256: str,
        reason_code: str,
    ) -> SideEffectOperation:
        _validate_identifier(reason_code, field="reason_code")
        return self._decide(
            operation_id=operation_id,
            tenant_id=tenant_id,
            actor_id=denied_by,
            expected_approval_sha256=expected_approval_sha256,
            decision="denied",
            denial_reason_code=reason_code,
        )

    def _decide(
        self,
        *,
        operation_id: str,
        tenant_id: str,
        actor_id: str,
        expected_approval_sha256: str,
        decision: str,
        denial_reason_code: str | None,
    ) -> SideEffectOperation:
        _validate_identifier(operation_id, field="operation_id")
        _validate_identifier(tenant_id, field="tenant_id")
        _validate_identifier(actor_id, field="actor_id")
        if not SHA256_PATTERN.fullmatch(expected_approval_sha256):
            raise ValueError("expected_approval_sha256 must be lowercase SHA-256")
        now = _timestamp(_utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._settle_expired(connection, now=now)
            row = connection.execute(
                "SELECT * FROM side_effect_operations "
                "WHERE operation_id = ? AND tenant_id = ?",
                (operation_id, tenant_id),
            ).fetchone()
            if row is None:
                raise OperationNotFound("side-effect operation was not found")
            if row["requested_by"] == actor_id:
                raise OperationSelfApprovalForbidden(
                    "requester and approver must be different principals"
                )
            if not hmac.compare_digest(
                str(row["approval_sha256"]), expected_approval_sha256
            ):
                raise OperationApprovalConflict("approval fingerprint does not match")
            if row["status"] == "expired":
                raise OperationExpired("operation approval or quote expired")
            if row["status"] == decision:
                same_decision = (
                    decision == "approved" and row["approved_by"] == actor_id
                ) or (
                    decision == "denied"
                    and row["denied_by"] == actor_id
                    and row["denial_reason_code"] == denial_reason_code
                )
                if same_decision:
                    return self._from_row(row)
                raise InvalidOperationTransition(
                    "operation already has a different persisted decision"
                )
            if row["status"] != "pending_approval":
                raise InvalidOperationTransition(
                    f"cannot {decision} an operation in status {row['status']}"
                )
            if decision == "approved":
                connection.execute(
                    """
                    UPDATE side_effect_operations
                    SET status = 'approved', approved_by = ?, approved_at = ?, updated_at = ?
                    WHERE operation_id = ? AND status = 'pending_approval'
                    """,
                    (actor_id, now, now, operation_id),
                )
                payload = {"approval_sha256": expected_approval_sha256}
            else:
                connection.execute(
                    """
                    UPDATE side_effect_operations
                    SET status = 'denied', denied_by = ?, denied_at = ?,
                        denial_reason_code = ?, updated_at = ?
                    WHERE operation_id = ? AND status = 'pending_approval'
                    """,
                    (actor_id, now, denial_reason_code, now, operation_id),
                )
                payload = {
                    "approval_sha256": expected_approval_sha256,
                    "reason_code": denial_reason_code,
                }
            self._append_event(
                connection,
                operation_id=operation_id,
                event_type=decision,
                actor_id=actor_id,
                payload=payload,
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM side_effect_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return self._from_row(updated)

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 30,
    ) -> SideEffectOperation | None:
        _validate_identifier(worker_id, field="worker_id")
        if not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        now_value = _utc_now()
        now = _timestamp(now_value)
        lease_expires_at = _timestamp(now_value + timedelta(seconds=lease_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._settle_expired(connection, now=now)
            self._settle_abandoned_executions(connection, now=now)
            row = connection.execute(
                """
                SELECT * FROM side_effect_operations
                WHERE status = 'approved'
                ORDER BY created_at, operation_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            operation_id = str(row["operation_id"])
            connection.execute(
                """
                UPDATE side_effect_operations
                SET status = 'executing', execution_owner = ?,
                    execution_lease_expires_at = ?, attempt = 1, updated_at = ?
                WHERE operation_id = ? AND status = 'approved'
                """,
                (worker_id, lease_expires_at, now, operation_id),
            )
            self._append_event(
                connection,
                operation_id=operation_id,
                event_type="execution_started",
                actor_id=worker_id,
                payload={
                    "attempt": 1,
                    "lease_expires_at": lease_expires_at,
                    "request_sha256": row["request_sha256"],
                    "quote_reference": row["quote_reference"],
                },
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM side_effect_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return self._from_row(updated)

    def complete_with_receipt(
        self,
        *,
        operation_id: str,
        worker_id: str,
        receipt_payload: dict,
    ) -> SideEffectOperation:
        now = _timestamp(_utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_live_execution(
                connection,
                operation_id=operation_id,
                worker_id=worker_id,
                now=now,
            )
            self._validate_receipt(receipt_payload, row=row)
            receipt_json = _canonical_json(receipt_payload)
            receipt_sha256 = _sha256_text(receipt_json)
            outcome = receipt_payload["outcome"]
            status = "succeeded" if outcome == "confirmed" else "failed"
            error_code = None if status == "succeeded" else "provider_rejected"
            error_message = None if status == "succeeded" else "Sandbox provider rejected operation."
            connection.execute(
                """
                UPDATE side_effect_operations
                SET status = ?, provider_operation_id = ?, receipt_json = ?,
                    receipt_sha256 = ?, error_code = ?, error_message = ?,
                    execution_owner = NULL, execution_lease_expires_at = NULL,
                    updated_at = ?
                WHERE operation_id = ? AND status = 'executing'
                """,
                (
                    status,
                    receipt_payload["provider_operation_id"],
                    receipt_json,
                    receipt_sha256,
                    error_code,
                    error_message,
                    now,
                    operation_id,
                ),
            )
            self._append_event(
                connection,
                operation_id=operation_id,
                event_type=(
                    "execution_succeeded" if status == "succeeded" else "execution_failed"
                ),
                actor_id=worker_id,
                payload={
                    "outcome": outcome,
                    "provider_operation_id": receipt_payload["provider_operation_id"],
                    "receipt_sha256": receipt_sha256,
                    "sandbox": True,
                },
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM side_effect_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return self._from_row(updated)

    def fail_execution(
        self,
        *,
        operation_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        uncertain: bool,
        provider_operation_id: str | None = None,
    ) -> SideEffectOperation:
        _validate_identifier(error_code, field="error_code")
        if provider_operation_id is not None:
            _validate_identifier(provider_operation_id, field="provider_operation_id")
        now = _timestamp(_utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_live_execution(
                connection,
                operation_id=operation_id,
                worker_id=worker_id,
                now=now,
            )
            status = "uncertain" if uncertain else "failed"
            event_type = "execution_uncertain" if uncertain else "execution_failed"
            connection.execute(
                """
                UPDATE side_effect_operations
                SET status = ?, provider_operation_id = ?, error_code = ?,
                    error_message = ?, execution_owner = NULL,
                    execution_lease_expires_at = NULL, updated_at = ?
                WHERE operation_id = ? AND status = 'executing'
                """,
                (
                    status,
                    provider_operation_id,
                    error_code,
                    error_message[:500],
                    now,
                    operation_id,
                ),
            )
            self._append_event(
                connection,
                operation_id=operation_id,
                event_type=event_type,
                actor_id=worker_id,
                payload={
                    "error_code": error_code,
                    "provider_operation_id": provider_operation_id,
                    "automatic_retry": False,
                },
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM side_effect_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return self._from_row(updated)

    def reconcile_uncertain(
        self,
        *,
        operation_id: str,
        tenant_id: str,
        actor_id: str,
        lookup_evidence: dict,
    ) -> tuple[SideEffectOperation, OperationReconciliation]:
        """Persist one read-only provider lookup and resolve only bound evidence."""
        for field, value in (
            ("operation_id", operation_id),
            ("tenant_id", tenant_id),
            ("actor_id", actor_id),
        ):
            _validate_identifier(value, field=field)
        now = _timestamp(_utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._settle_abandoned_executions(connection, now=now)
            row = connection.execute(
                "SELECT * FROM side_effect_operations "
                "WHERE operation_id = ? AND tenant_id = ?",
                (operation_id, tenant_id),
            ).fetchone()
            if row is None:
                raise OperationNotFound("side-effect operation was not found")
            if row["status"] != "uncertain":
                raise InvalidOperationTransition(
                    f"cannot reconcile an operation in status {row['status']}"
                )
            if row["provider_operation_id"] is None:
                raise OperationReconciliationUnavailable(
                    "uncertain operation has no provider operation reference"
                )
            self._validate_status_lookup(lookup_evidence, row=row)
            evidence_json = _canonical_json(lookup_evidence)
            evidence_sha256 = _sha256_text(evidence_json)
            outcome = str(lookup_evidence["outcome"])
            receipt_payload = None
            receipt_sha256 = None
            if outcome in {"confirmed", "rejected"}:
                receipt_payload = {
                    "version": RECEIPT_VERSION,
                    "operation_id": operation_id,
                    "request_sha256": str(row["request_sha256"]),
                    "provider": str(row["quote_provider"]),
                    "provider_operation_id": str(row["provider_operation_id"]),
                    "outcome": outcome,
                    "executed_at": lookup_evidence["observed_at"],
                    "response_sha256": lookup_evidence["response_sha256"],
                    "sandbox": True,
                }
                self._validate_receipt(receipt_payload, row=row)
                receipt_json = _canonical_json(receipt_payload)
                receipt_sha256 = _sha256_text(receipt_json)
                resolved_status = "succeeded" if outcome == "confirmed" else "failed"
                error_code = None if outcome == "confirmed" else "provider_rejected"
                error_message = (
                    None
                    if outcome == "confirmed"
                    else "Sandbox status lookup confirmed provider rejection."
                )
                connection.execute(
                    """
                    UPDATE side_effect_operations
                    SET status = ?, receipt_json = ?, receipt_sha256 = ?,
                        error_code = ?, error_message = ?, updated_at = ?
                    WHERE operation_id = ? AND status = 'uncertain'
                    """,
                    (
                        resolved_status,
                        receipt_json,
                        receipt_sha256,
                        error_code,
                        error_message,
                        now,
                        operation_id,
                    ),
                )
                event_type = (
                    "execution_succeeded" if outcome == "confirmed" else "execution_failed"
                )
                event_payload = {
                    "outcome": outcome,
                    "provider_operation_id": row["provider_operation_id"],
                    "receipt_sha256": receipt_sha256,
                    "resolution_source": STATUS_LOOKUP_VERSION,
                    "lookup_evidence_sha256": evidence_sha256,
                    "sandbox": True,
                }
            else:
                error_code = (
                    "status_lookup_still_unknown"
                    if outcome == "still_unknown"
                    else "provider_operation_not_found"
                )
                connection.execute(
                    """
                    UPDATE side_effect_operations
                    SET error_code = ?,
                        error_message = 'Provider status lookup did not resolve the operation.',
                        updated_at = ?
                    WHERE operation_id = ? AND status = 'uncertain'
                    """,
                    (error_code, now, operation_id),
                )
                event_type = "execution_uncertain"
                event_payload = {
                    "error_code": error_code,
                    "provider_operation_id": row["provider_operation_id"],
                    "lookup_outcome": outcome,
                    "resolution_source": STATUS_LOOKUP_VERSION,
                    "lookup_evidence_sha256": evidence_sha256,
                    "automatic_retry": False,
                }
            cursor = connection.execute(
                """
                INSERT INTO side_effect_operation_reconciliations(
                    operation_id, tenant_id, actor_id, outcome,
                    provider_operation_id, evidence_json, evidence_sha256,
                    receipt_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    tenant_id,
                    actor_id,
                    outcome,
                    row["provider_operation_id"],
                    evidence_json,
                    evidence_sha256,
                    receipt_sha256,
                    now,
                ),
            )
            self._append_event(
                connection,
                operation_id=operation_id,
                event_type=event_type,
                actor_id=actor_id,
                payload=event_payload,
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM side_effect_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            reconciliation_row = connection.execute(
                "SELECT * FROM side_effect_operation_reconciliations "
                "WHERE reconciliation_id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return self._from_row(updated), self._reconciliation_from_row(
                reconciliation_row
            )

    def get(
        self,
        operation_id: str,
        *,
        tenant_id: str | None = None,
    ) -> SideEffectOperation | None:
        _validate_identifier(operation_id, field="operation_id")
        if tenant_id is not None:
            _validate_identifier(tenant_id, field="tenant_id")
        now = _timestamp(_utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._settle_expired(connection, now=now)
            self._settle_abandoned_executions(connection, now=now)
            if tenant_id is None:
                row = connection.execute(
                    "SELECT * FROM side_effect_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM side_effect_operations "
                    "WHERE operation_id = ? AND tenant_id = ?",
                    (operation_id, tenant_id),
                ).fetchone()
            return self._from_row(row) if row is not None else None

    def list_events(
        self,
        operation_id: str,
        *,
        tenant_id: str | None = None,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> tuple[OperationEvent, ...]:
        if after_event_id < 0:
            raise ValueError("after_event_id must be non-negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        operation = self.get(operation_id, tenant_id=tenant_id)
        if operation is None:
            raise OperationNotFound("side-effect operation was not found")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM side_effect_operation_events
                WHERE operation_id = ? AND event_id > ?
                ORDER BY event_id
                LIMIT ?
                """,
                (operation_id, after_event_id, limit),
            ).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def list_reconciliations(
        self,
        operation_id: str,
        *,
        tenant_id: str | None = None,
        after_reconciliation_id: int = 0,
        limit: int = 100,
    ) -> tuple[OperationReconciliation, ...]:
        if after_reconciliation_id < 0:
            raise ValueError("after_reconciliation_id must be non-negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        operation = self.get(operation_id, tenant_id=tenant_id)
        if operation is None:
            raise OperationNotFound("side-effect operation was not found")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM side_effect_operation_reconciliations
                WHERE operation_id = ? AND reconciliation_id > ?
                ORDER BY reconciliation_id
                LIMIT ?
                """,
                (operation_id, after_reconciliation_id, limit),
            ).fetchall()
        return tuple(self._reconciliation_from_row(row) for row in rows)

    @staticmethod
    def _settle_expired(connection: sqlite3.Connection, *, now: str) -> None:
        rows = connection.execute(
            """
            SELECT * FROM side_effect_operations
            WHERE status IN ('pending_approval', 'approved')
              AND (approval_expires_at <= ? OR quote_valid_until <= ?)
            ORDER BY created_at, operation_id
            """,
            (now, now),
        ).fetchall()
        for row in rows:
            operation_id = str(row["operation_id"])
            connection.execute(
                """
                UPDATE side_effect_operations
                SET status = 'expired', error_code = 'approval_or_quote_expired',
                    error_message = 'Approval or quote expired before execution.',
                    updated_at = ?
                WHERE operation_id = ? AND status IN ('pending_approval', 'approved')
                """,
                (now, operation_id),
            )
            SideEffectOperationRepository._append_event(
                connection,
                operation_id=operation_id,
                event_type="expired",
                actor_id="system",
                payload={
                    "approval_expires_at": row["approval_expires_at"],
                    "quote_valid_until": row["quote_valid_until"],
                },
                created_at=now,
            )

    @staticmethod
    def _settle_abandoned_executions(
        connection: sqlite3.Connection,
        *,
        now: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM side_effect_operations
            WHERE status = 'executing' AND execution_lease_expires_at <= ?
            ORDER BY created_at, operation_id
            """,
            (now,),
        ).fetchall()
        for row in rows:
            operation_id = str(row["operation_id"])
            connection.execute(
                """
                UPDATE side_effect_operations
                SET status = 'uncertain', error_code = 'execution_lease_expired',
                    error_message = 'Executor disappeared after side-effect claim; status lookup required.',
                    execution_owner = NULL, execution_lease_expires_at = NULL,
                    updated_at = ?
                WHERE operation_id = ? AND status = 'executing'
                """,
                (now, operation_id),
            )
            SideEffectOperationRepository._append_event(
                connection,
                operation_id=operation_id,
                event_type="execution_uncertain",
                actor_id=str(row["execution_owner"] or "system"),
                payload={
                    "error_code": "execution_lease_expired",
                    "automatic_retry": False,
                },
                created_at=now,
            )

    @staticmethod
    def _require_live_execution(
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        worker_id: str,
        now: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM side_effect_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise OperationNotFound("side-effect operation was not found")
        if (
            row["status"] != "executing"
            or row["execution_owner"] != worker_id
            or row["execution_lease_expires_at"] is None
            or row["execution_lease_expires_at"] <= now
        ):
            raise OperationLeaseLost("operation execution lease is missing or expired")
        return row

    @staticmethod
    def _validate_receipt(receipt: dict, *, row: sqlite3.Row) -> None:
        required = {
            "version",
            "operation_id",
            "request_sha256",
            "provider",
            "provider_operation_id",
            "outcome",
            "executed_at",
            "response_sha256",
            "sandbox",
        }
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise ValueError("receipt fields do not match side_effect_receipt_v1")
        if receipt["version"] != RECEIPT_VERSION:
            raise ValueError("unsupported receipt version")
        if receipt["operation_id"] != row["operation_id"]:
            raise ValueError("receipt operation_id does not match")
        if receipt["request_sha256"] != row["request_sha256"]:
            raise ValueError("receipt request_sha256 does not match")
        if receipt["provider"] != row["quote_provider"]:
            raise ValueError("receipt provider does not match quote")
        if receipt["sandbox"] is not True:
            raise ValueError("v1 receipts must remain explicitly sandboxed")
        if receipt["outcome"] not in {"confirmed", "rejected"}:
            raise ValueError("receipt outcome is invalid")
        _validate_identifier(
            receipt["provider_operation_id"], field="provider_operation_id"
        )
        if not SHA256_PATTERN.fullmatch(receipt["response_sha256"]):
            raise ValueError("receipt response_sha256 must be lowercase SHA-256")
        _parse_timestamp(receipt["executed_at"])

    @staticmethod
    def _validate_status_lookup(evidence: dict, *, row: sqlite3.Row) -> None:
        required = {
            "version",
            "operation_id",
            "request_sha256",
            "provider",
            "provider_operation_id",
            "outcome",
            "observed_at",
            "provider_payload",
            "response_sha256",
            "sandbox",
        }
        if not isinstance(evidence, dict) or set(evidence) != required:
            raise ValueError("status lookup fields do not match side_effect_status_lookup_v1")
        if evidence["version"] != STATUS_LOOKUP_VERSION:
            raise ValueError("unsupported status lookup version")
        bindings = {
            "operation_id": row["operation_id"],
            "request_sha256": row["request_sha256"],
            "provider": row["quote_provider"],
            "provider_operation_id": row["provider_operation_id"],
        }
        for field, expected in bindings.items():
            if evidence[field] != expected:
                raise ValueError(f"status lookup {field} does not match")
        if evidence["outcome"] not in {
            "confirmed",
            "rejected",
            "still_unknown",
            "not_found",
        }:
            raise ValueError("status lookup outcome is invalid")
        if evidence["sandbox"] is not True:
            raise ValueError("v1 status lookup must remain explicitly sandboxed")
        if not isinstance(evidence["provider_payload"], dict):
            raise ValueError("status lookup provider_payload must be an object")
        expected_response_sha = _sha256_text(_canonical_json(evidence["provider_payload"]))
        if not hmac.compare_digest(expected_response_sha, evidence["response_sha256"]):
            raise ValueError("status lookup response_sha256 does not match payload")
        _parse_timestamp(evidence["observed_at"])

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        event_type: str,
        actor_id: str,
        payload: dict,
        created_at: str,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO side_effect_operation_events(
                operation_id, event_type, actor_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (operation_id, event_type, actor_id, _canonical_json(payload), created_at),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SideEffectOperation:
        quote = OperationQuote(
            provider=str(row["quote_provider"]),
            reference=str(row["quote_reference"]),
            valid_until=str(row["quote_valid_until"]),
            currency=str(row["quote_currency"]),
            amount_minor=int(row["quote_amount_minor"]),
            terms_sha256=str(row["quote_terms_sha256"]),
            sandbox=bool(row["quote_sandbox"]),
        )
        return SideEffectOperation(
            operation_id=str(row["operation_id"]),
            request_id=str(row["request_id"]),
            tenant_id=str(row["tenant_id"]),
            requested_by=str(row["requested_by"]),
            operation_kind=str(row["operation_kind"]),
            status=str(row["status"]),
            action_payload=json.loads(row["action_json"]),
            request_sha256=str(row["request_sha256"]),
            idempotency_key=str(row["idempotency_key"]),
            quote=quote,
            approval_sha256=str(row["approval_sha256"]),
            approval_expires_at=str(row["approval_expires_at"]),
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            denied_by=row["denied_by"],
            denied_at=row["denied_at"],
            denial_reason_code=row["denial_reason_code"],
            execution_owner=row["execution_owner"],
            execution_lease_expires_at=row["execution_lease_expires_at"],
            attempt=int(row["attempt"]),
            provider_operation_id=row["provider_operation_id"],
            receipt_payload=(
                json.loads(row["receipt_json"])
                if row["receipt_json"] is not None
                else None
            ),
            receipt_sha256=row["receipt_sha256"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> OperationEvent:
        return OperationEvent(
            event_id=int(row["event_id"]),
            operation_id=str(row["operation_id"]),
            event_type=str(row["event_type"]),
            actor_id=str(row["actor_id"]),
            payload=json.loads(row["payload_json"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _reconciliation_from_row(row: sqlite3.Row) -> OperationReconciliation:
        return OperationReconciliation(
            reconciliation_id=int(row["reconciliation_id"]),
            operation_id=str(row["operation_id"]),
            tenant_id=str(row["tenant_id"]),
            actor_id=str(row["actor_id"]),
            outcome=str(row["outcome"]),
            provider_operation_id=str(row["provider_operation_id"]),
            evidence_payload=json.loads(row["evidence_json"]),
            evidence_sha256=str(row["evidence_sha256"]),
            receipt_sha256=row["receipt_sha256"],
            created_at=str(row["created_at"]),
        )
