"""Privacy-minimized, tamper-evident local tool-call diagnostics.

The original hackathon logger persisted arbitrary params, responses and exception
messages.  New ``tool_call_audit_v2`` rows instead retain a bounded structured
projection, a stable error code and a per-session SHA-256 chain.  Historical rows
are not rewritten automatically; ``fetch_calls`` hides their payload by default.

New writes default to the dedicated ``runtime/tool_audit.db`` store instead of the
legacy shared ``tool_calls.db``.  This remains a local diagnostic ledger, not a
compliance audit system: the SQLite file is not encrypted or remotely immutable and
an operator can still delete the whole file.  Row-level UPDATE/DELETE is rejected for
v2 rows, while ``clear_session`` appends a reset marker rather than destroying evidence.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOG_DB = ROOT / "runtime" / "tool_audit.db"
TOOL_AUDIT_DB_ENV = "BJ_PAL_TOOL_AUDIT_DB"
# Explicit test/operator override.  Normal runtime resolution remains dynamic so a
# subprocess can select an isolated store through BJ_PAL_TOOL_AUDIT_DB before use.
LOG_DB: Path | None = None
PRIVACY_VERSION = "tool_call_audit_v2"
RESET_TOOL_NAME = "audit.session_reset"
MAX_DEPTH = 6
MAX_COLLECTION_ITEMS = 25
MAX_SAFE_TEXT_LENGTH = 160

_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    timestamp TEXT,
    tool_name TEXT NOT NULL,
    params_json TEXT,
    response_json TEXT,
    status TEXT,
    latency_ms REAL,
    error TEXT,
    privacy_version TEXT,
    sequence INTEGER,
    previous_event_sha256 TEXT,
    event_sha256 TEXT,
    redaction_count INTEGER,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool ON tool_calls(tool_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_audit_sequence
    ON tool_calls(session_id, sequence)
    WHERE privacy_version = 'tool_call_audit_v2';
CREATE TRIGGER IF NOT EXISTS tool_calls_v2_no_update
BEFORE UPDATE ON tool_calls
WHEN OLD.privacy_version = 'tool_call_audit_v2'
BEGIN
    SELECT RAISE(ABORT, 'tool_call_audit_v2 rows are append-only');
END;
CREATE TRIGGER IF NOT EXISTS tool_calls_v2_no_delete
BEFORE DELETE ON tool_calls
WHEN OLD.privacy_version = 'tool_call_audit_v2'
BEGIN
    SELECT RAISE(ABORT, 'tool_call_audit_v2 rows are append-only');
END;
"""

_ADDITIVE_COLUMNS = {
    "privacy_version": "TEXT",
    "sequence": "INTEGER",
    "previous_event_sha256": "TEXT",
    "event_sha256": "TEXT",
    "redaction_count": "INTEGER",
    "error_code": "TEXT",
}

_SESSION_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "bj_pal_tool_log_session",
    default=None,
)
_SCHEMA_LOCK = threading.Lock()
_INITIALIZED_DATABASES: set[str] = set()

_SAFE_TEXT_KEYS = {
    "action",
    "area_anchor",
    "audience",
    "classification",
    "delivery_time",
    "kind",
    "persona",
    "poi_id",
    "poi_name",
    "reason",
    "restaurant",
    "source",
    "status",
    "strategy",
    "style",
    "target_time",
    "type",
}
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "authorization",
    "body",
    "budget",
    "contact",
    "cookie",
    "cost",
    "credential",
    "email",
    "greeting",
    "location",
    "message",
    "mobile",
    "note",
    "password",
    "phone",
    "price",
    "prompt",
    "raw_text",
    "secret",
    "token",
    "url",
    "user_input",
)
_SENSITIVE_EXACT_KEYS = {"input", "query", "text", "value"}
_CREDENTIAL_PATTERNS = (
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{24,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{12,}\b"),
)
_EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:@/-]{1,160}$")
_SAFE_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
_ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def database_path() -> Path:
    """Resolve the dedicated diagnostic store without touching the legacy DB."""
    if LOG_DB is not None:
        return Path(LOG_DB)
    configured = os.environ.get(TOOL_AUDIT_DB_ENV)
    return Path(configured) if configured else DEFAULT_LOG_DB


def _conn() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_log_db() -> None:
    path = database_path()
    database_key = str(path.resolve())
    if database_key in _INITIALIZED_DATABASES and path.exists():
        return
    with _SCHEMA_LOCK:
        if database_key in _INITIALIZED_DATABASES and path.exists():
            return
        conn = _conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    tool_name TEXT NOT NULL,
                    params_json TEXT,
                    response_json TEXT,
                    status TEXT,
                    latency_ms REAL,
                    error TEXT
                )
                """
            )
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(tool_calls)")
            }
            for name, column_type in _ADDITIVE_COLUMNS.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE tool_calls ADD COLUMN {name} {column_type}")
            conn.executescript(_BASE_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _INITIALIZED_DATABASES.add(database_key)


def set_session(session_id: str) -> None:
    _SESSION_ID.set(_safe_session_id(session_id))


def log_call(
    tool_name: str,
    params: Optional[dict] = None,
    response: Optional[Any] = None,
    status: str = "ok",
    latency_ms: float = 0.0,
    error: Optional[str] = None,
) -> None:
    """Append one bounded v2 event without persisting arbitrary free text."""
    _append_event(
        session_id=_SESSION_ID.get(),
        tool_name=tool_name,
        params=params,
        response=response,
        status=status,
        latency_ms=latency_ms,
        error=error,
    )


@contextmanager
def timed_call(tool_name: str, params: Optional[dict] = None):
    """Capture a tool span and persist only a privacy-minimized projection."""
    from agents.tracing import trace_span

    record: dict = {"response": None, "status": "ok", "error": None}
    started = time.monotonic()
    with trace_span(f"tool.{tool_name}"):
        try:
            yield record
        except Exception as exc:
            record["status"] = "error"
            record["error"] = type(exc).__name__
            raise
        finally:
            log_call(
                tool_name=tool_name,
                params=params,
                response=record["response"],
                status=record["status"],
                latency_ms=(time.monotonic() - started) * 1000,
                error=record["error"],
            )


def fetch_calls(session_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    """Return current-session rows and hide payloads from legacy unverified rows."""
    init_log_db()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        params: list[Any] = []
        where: list[str] = ["tool_name != ?"]
        params.append(RESET_TOOL_NAME)
        if session_id:
            safe_session = _safe_session_id(session_id)
            reset_id = _latest_reset_id(conn, safe_session)
            where.extend(["session_id = ?", "id > ?"])
            params.extend([safe_session, reset_id])
        sql = "SELECT * FROM tool_calls WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return [_public_row(row) for row in rows]


def clear_session(session_id: str) -> None:
    """Start a new visible segment without deleting append-only v2 evidence."""
    _append_event(
        session_id=session_id,
        tool_name=RESET_TOOL_NAME,
        params={"reason": "session_reset"},
        response={"status": "accepted"},
        status="ok",
        latency_ms=0.0,
        error=None,
    )


def verify_session_chain(session_id: str) -> dict[str, Any]:
    """Recompute the complete v2 chain for one session from stored row bodies."""
    init_log_db()
    safe_session = _safe_session_id(session_id)
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT * FROM tool_calls
            WHERE session_id = ? AND privacy_version = ?
            ORDER BY sequence ASC, id ASC
            """,
            (safe_session, PRIVACY_VERSION),
        ).fetchall()
    finally:
        conn.close()
    previous: str | None = None
    valid = True
    for expected_sequence, row in enumerate(rows, start=1):
        observed_previous = row["previous_event_sha256"] or None
        if row["sequence"] != expected_sequence or observed_previous != previous:
            valid = False
        try:
            expected_sha = _event_sha256(_event_body_from_row(row))
        except (TypeError, ValueError, json.JSONDecodeError):
            expected_sha = ""
            valid = False
        if row["event_sha256"] != expected_sha:
            valid = False
        previous = str(row["event_sha256"] or "") or None
    return {
        "privacy_version": PRIVACY_VERSION,
        "session_id": safe_session,
        "event_count": len(rows),
        "head_sha256": previous,
        "chain_valid": bool(rows) and valid,
    }


def export_session_events(session_id: str) -> list[dict[str, Any]]:
    """Export only v2 projected event bodies for offline verification."""
    init_log_db()
    safe_session = _safe_session_id(session_id)
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT * FROM tool_calls
            WHERE session_id = ? AND privacy_version = ?
            ORDER BY sequence ASC, id ASC
            """,
            (safe_session, PRIVACY_VERSION),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            **_event_body_from_row(row),
            "event_sha256": row["event_sha256"],
        }
        for row in rows
    ]


def _append_event(
    *,
    session_id: str | None,
    tool_name: str,
    params: Any,
    response: Any,
    status: str,
    latency_ms: float,
    error: str | None,
) -> None:
    init_log_db()
    safe_session = _safe_session_id(session_id or "unscoped")
    safe_tool_name = _safe_tool_name(tool_name)
    safe_status = _safe_status(status)
    safe_latency = _safe_latency(latency_ms)
    safe_params, params_redactions = _privacy_projection(params or {})
    safe_response, response_redactions = _privacy_projection(response)
    error_code = _safe_error_code(error, status=safe_status)
    timestamp = datetime.now().isoformat(timespec="milliseconds")
    params_json = _canonical_json(safe_params)
    response_json = (
        _canonical_json(safe_response) if response is not None else None
    )
    redaction_count = params_redactions + response_redactions + int(error is not None)

    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            """
            SELECT sequence, event_sha256 FROM tool_calls
            WHERE session_id = ? AND privacy_version = ?
            ORDER BY sequence DESC LIMIT 1
            """,
            (safe_session, PRIVACY_VERSION),
        ).fetchone()
        sequence = int(previous[0]) + 1 if previous else 1
        previous_sha = str(previous[1]) if previous and previous[1] else None
        body = {
            "privacy_version": PRIVACY_VERSION,
            "session_id": safe_session,
            "sequence": sequence,
            "previous_event_sha256": previous_sha,
            "timestamp": timestamp,
            "tool_name": safe_tool_name,
            "params": safe_params,
            "response": safe_response if response is not None else None,
            "status": safe_status,
            "latency_ms": safe_latency,
            "redaction_count": redaction_count,
            "error_code": error_code,
        }
        event_sha = _event_sha256(body)
        conn.execute(
            """
            INSERT INTO tool_calls(
                session_id, timestamp, tool_name, params_json, response_json,
                status, latency_ms, error, privacy_version, sequence,
                previous_event_sha256, event_sha256, redaction_count, error_code
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                safe_session,
                timestamp,
                safe_tool_name,
                params_json,
                response_json,
                safe_status,
                safe_latency,
                None,
                PRIVACY_VERSION,
                sequence,
                previous_sha,
                event_sha,
                redaction_count,
                error_code,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _privacy_projection(value: Any, *, key: str | None = None, depth: int = 0) -> tuple[Any, int]:
    if depth > MAX_DEPTH:
        return {"_type": "truncated", "reason": "max_depth"}, 1
    if is_dataclass(value):
        value = asdict(value)
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value, 0
    if isinstance(value, float):
        if math.isfinite(value):
            return value, 0
        return {"_type": "number", "value": "non_finite"}, 1
    if isinstance(value, datetime):
        return value.isoformat(), 0
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        redactions = 0
        items = sorted(value.items(), key=lambda item: str(item[0]))
        for index, (raw_key, item) in enumerate(items):
            if index >= MAX_COLLECTION_ITEMS:
                projected["_truncated_keys"] = len(items) - MAX_COLLECTION_ITEMS
                redactions += len(items) - MAX_COLLECTION_ITEMS
                break
            raw_key_text = str(raw_key)
            if (
                not _SAFE_FIELD_NAME.fullmatch(raw_key_text)
                or _contains_sensitive_literal(raw_key_text)
            ):
                item_key = f"_field_{index}"
                redactions += 1
            else:
                item_key = raw_key_text
            if _is_sensitive_key(item_key):
                projected[item_key] = {"_redacted": "sensitive_field"}
                redactions += 1
                continue
            projected[item_key], item_redactions = _privacy_projection(
                item,
                key=item_key,
                depth=depth + 1,
            )
            redactions += item_redactions
        return projected, redactions
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        projected_items: list[Any] = []
        redactions = 0
        for item in items[:MAX_COLLECTION_ITEMS]:
            projected, item_redactions = _privacy_projection(
                item,
                key=key,
                depth=depth + 1,
            )
            projected_items.append(projected)
            redactions += item_redactions
        if len(items) > MAX_COLLECTION_ITEMS:
            projected_items.append(
                {"_type": "truncated", "remaining_items": len(items) - MAX_COLLECTION_ITEMS}
            )
            redactions += len(items) - MAX_COLLECTION_ITEMS
        return projected_items, redactions
    if isinstance(value, str):
        if _contains_sensitive_literal(value):
            return {"_redacted": "sensitive_literal"}, 1
        if key in _SAFE_TEXT_KEYS and len(value) <= MAX_SAFE_TEXT_LENGTH:
            return value, 0
        return {"_type": "text", "length": len(value)}, 1
    return {"_type": type(value).__name__}, 1


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    return normalized in _SENSITIVE_EXACT_KEYS or any(
        fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS
    )


def _contains_sensitive_literal(value: str) -> bool:
    return bool(
        _EMAIL_PATTERN.search(value)
        or _PHONE_PATTERN.search(value)
        or any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS)
    )


def _safe_session_id(value: str) -> str:
    text = str(value or "unscoped")
    if _SAFE_IDENTIFIER.fullmatch(text) and not _contains_sensitive_literal(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"session-sha256-{digest}"


def _safe_tool_name(value: str) -> str:
    text = str(value)
    if not _SAFE_IDENTIFIER.fullmatch(text) or _contains_sensitive_literal(text):
        raise ValueError("tool_name must be a bounded opaque identifier")
    return text


def _safe_status(value: str) -> str:
    normalized = str(value or "error").strip().lower()
    if normalized not in {"ok", "error"}:
        raise ValueError("tool call status must be ok or error")
    return normalized


def _safe_latency(value: float) -> float:
    latency = float(value)
    if not math.isfinite(latency) or latency < 0:
        raise ValueError("tool call latency must be finite and non-negative")
    return round(latency, 3)


def _safe_error_code(error: str | None, *, status: str) -> str | None:
    if status == "ok":
        return None
    if not error:
        return "tool_call_error"
    candidate = str(error).split(":", 1)[0].strip()
    if not _ERROR_CODE.fullmatch(candidate):
        return "tool_call_error"
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", candidate).lower()
    return snake[:64]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _event_sha256(body: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _event_body_from_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    params = json.loads(row["params_json"] or "{}")
    response = json.loads(row["response_json"]) if row["response_json"] is not None else None
    return {
        "privacy_version": row["privacy_version"],
        "session_id": row["session_id"],
        "sequence": row["sequence"],
        "previous_event_sha256": row["previous_event_sha256"] or None,
        "timestamp": row["timestamp"],
        "tool_name": row["tool_name"],
        "params": params,
        "response": response,
        "status": row["status"],
        "latency_ms": float(row["latency_ms"]),
        "redaction_count": int(row["redaction_count"] or 0),
        "error_code": row["error_code"] or None,
    }


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("privacy_version") != PRIVACY_VERSION:
        row["params_json"] = _canonical_json({"_legacy_payload": "hidden"})
        row["response_json"] = _canonical_json({"_legacy_payload": "hidden"})
        row["error"] = None
        row["error_code"] = "legacy_unverified"
        row["integrity_valid"] = False
        return row
    try:
        row["integrity_valid"] = row.get("event_sha256") == _event_sha256(
            _event_body_from_row(row)
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        row["integrity_valid"] = False
    return row


def _latest_reset_id(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        """
        SELECT MAX(id) FROM tool_calls
        WHERE session_id = ? AND tool_name = ? AND privacy_version = ?
        """,
        (session_id, RESET_TOOL_NAME, PRIVACY_VERSION),
    ).fetchone()
    return int(row[0] or 0)
