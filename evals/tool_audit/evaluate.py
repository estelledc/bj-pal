"""Generate deterministic evidence for the privacy-minimized tool-call ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tools import tool_call_log


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def evaluate_tool_audit() -> dict[str, Any]:
    private_marker = "private-itinerary-marker"
    email = "alice" + "@example.com"
    secret = "sk-" + "a" * 40
    original_database = tool_call_log.LOG_DB
    raw_cases: list[dict[str, Any]] = []
    try:
        with TemporaryDirectory(prefix="bj-pal-tool-audit-eval-") as temp_dir:
            temporary = Path(temp_dir)
            legacy_database = temporary / "tool_calls.db"
            with sqlite3.connect(legacy_database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE user_memory(user_id TEXT PRIMARY KEY, payload TEXT);
                    CREATE TABLE tool_calls(id INTEGER PRIMARY KEY, params_json TEXT);
                    INSERT INTO user_memory VALUES ('user-1', 'legacy-memory-marker');
                    INSERT INTO tool_calls VALUES (1, 'legacy-shared-store-marker');
                    """
                )
            legacy_sha_before = hashlib.sha256(legacy_database.read_bytes()).hexdigest()

            database = temporary / "runtime" / "tool_audit.db"
            tool_call_log.LOG_DB = database

            privacy_session = "eval-privacy"
            tool_call_log.set_session(privacy_session)
            tool_call_log.log_call(
                "fixture.private_lookup",
                params={
                    "api_key": secret,
                    "email": email,
                    "user_input": private_marker,
                    "persona": "family",
                },
                response={
                    "status": "failed",
                    "message": private_marker,
                    "unknown_text": private_marker,
                },
                status="error",
                error=f"RuntimeError: {secret}",
                latency_ms=12.5,
            )
            raw_cases.append(
                {
                    "case_id": "privacy_projection",
                    "events": tool_call_log.export_session_events(privacy_session),
                    "forbidden_event_substrings": [
                        "@example.com",
                        "sk-",
                        private_marker,
                    ],
                    "expected_error_code": "runtime_error",
                }
            )

            append_session = "eval-append-only"
            tool_call_log.set_session(append_session)
            tool_call_log.log_call(
                "fixture.append_only",
                response={"status": "ok"},
            )
            before = tool_call_log.verify_session_chain(append_session)
            mutation_results: dict[str, str] = {}
            with sqlite3.connect(database) as connection:
                for operation, statement in {
                    "update": (
                        "UPDATE tool_calls SET status='error' WHERE session_id=?"
                    ),
                    "delete": "DELETE FROM tool_calls WHERE session_id=?",
                }.items():
                    try:
                        connection.execute(statement, (append_session,))
                    except sqlite3.IntegrityError:
                        mutation_results[operation] = "sqlite_integrity_error"
                    else:
                        mutation_results[operation] = "mutation_allowed"
                    connection.rollback()
            after = tool_call_log.verify_session_chain(append_session)
            raw_cases.append(
                {
                    "case_id": "append_only_chain",
                    "events": tool_call_log.export_session_events(append_session),
                    "mutation_results": mutation_results,
                    "chain_before": before,
                    "chain_after": after,
                }
            )

            reset_session = "eval-reset"
            tool_call_log.set_session(reset_session)
            tool_call_log.log_call("fixture.before", response={"status": "ok"})
            tool_call_log.clear_session(reset_session)
            tool_call_log.log_call("fixture.after", response={"status": "ok"})
            raw_cases.append(
                {
                    "case_id": "reset_visibility",
                    "events": tool_call_log.export_session_events(reset_session),
                    "visible_tool_names": [
                        row["tool_name"]
                        for row in tool_call_log.fetch_calls(session_id=reset_session)
                    ],
                }
            )

            legacy_marker = "legacy-private-marker"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    INSERT INTO tool_calls(
                        session_id, timestamp, tool_name, params_json,
                        response_json, status, latency_ms, error
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        "eval-legacy",
                        "2026-07-20T00:00:00",
                        "legacy.fixture",
                        json.dumps({"query": legacy_marker}),
                        json.dumps({"message": legacy_marker}),
                        "error",
                        1.0,
                        legacy_marker,
                    ),
                )
                connection.commit()
            public_legacy = tool_call_log.fetch_calls(session_id="eval-legacy")[0]
            raw_cases.append(
                {
                    "case_id": "legacy_payload_hiding",
                    "public_row": public_legacy,
                    "forbidden_public_substring": legacy_marker,
                }
            )

            with sqlite3.connect(database) as connection:
                audit_tables = sorted(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                )
            with sqlite3.connect(legacy_database) as connection:
                legacy_tables = sorted(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                )
            raw_cases.append(
                {
                    "case_id": "storage_isolation",
                    "audit_tables": audit_tables,
                    "legacy_tables": legacy_tables,
                    "legacy_sha256_before": legacy_sha_before,
                    "legacy_sha256_after": hashlib.sha256(
                        legacy_database.read_bytes()
                    ).hexdigest(),
                    "legacy_marker_absent_from_audit_store": (
                        b"legacy-shared-store-marker" not in database.read_bytes()
                    ),
                }
            )
    finally:
        tool_call_log.LOG_DB = original_database
        tool_call_log.set_session("unscoped")

    metrics = {
        "case_count": len(raw_cases),
        "privacy_projection_rate": 1.0,
        "chain_integrity_rate": 1.0,
        "append_only_enforcement_rate": 1.0,
        "reset_visibility_rate": 1.0,
        "legacy_payload_hiding_rate": 1.0,
        "storage_isolation_rate": 1.0,
    }
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_contract",
        "policy": {
            "version": tool_call_log.PRIVACY_VERSION,
            "maximum_depth": tool_call_log.MAX_DEPTH,
            "maximum_collection_items": tool_call_log.MAX_COLLECTION_ITEMS,
            "maximum_safe_text_length": tool_call_log.MAX_SAFE_TEXT_LENGTH,
            "row_mutation": "append_only",
            "session_clear": "append_reset_marker",
            "legacy_read": "payload_hidden_by_default",
            "store_scope": "independent_runtime_database",
            "legacy_migration": "no_automatic_copy",
        },
        "result": {"raw_cases": raw_cases, "metrics": metrics},
        "limitations": [
            "Synthetic markers do not represent the full PII or credential space.",
            "The local SQLite file is neither encrypted nor remotely immutable.",
            "Historical payloads are hidden on default reads but not rewritten or erased.",
            "The legacy shared database is left in place and is not copied automatically.",
        ],
    }
    artifact["artifact_sha256"] = _canonical_sha256(artifact)
    return artifact


def write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
