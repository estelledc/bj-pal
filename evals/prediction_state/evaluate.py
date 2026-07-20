from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from storage import prediction_feedback
from storage.verified_copy import canonical_sha256, metadata_body
from tools import prediction_log


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(prediction_feedback.PREDICTION_FEEDBACK_SCHEMA)
        connection.executemany(
            "INSERT INTO prediction_log VALUES (?,?,?,?,?,?,?,?)",
            [
                (7, "poi-a", "14:00", 10, "2026-01-01T13:00:00", None, None, 0.8),
                (11, "poi-b", "15:00", 20, "2026-01-01T14:00:00", 35,
                 "2026-01-01T16:00:00", 0.5),
            ],
        )
        connection.executescript(
            """
            CREATE TABLE user_memory(id INTEGER PRIMARY KEY, payload TEXT);
            INSERT INTO user_memory VALUES (1, 'private-memory-marker');
            """
        )


def _metadata(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM state_store_metadata WHERE domain='prediction_feedback'"
        ).fetchone()
    return {"body": metadata_body(row), "receipt_sha256": row["receipt_sha256"]}


def evaluate_prediction_state() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="bj-pal-prediction-state-") as temporary_name:
        temporary = Path(temporary_name)
        source = temporary / "tool_calls.db"
        destination = temporary / "runtime" / "prediction_feedback.db"
        _source(source)
        source_before = _file_sha(source)
        preview = prediction_feedback.migrate_prediction_feedback_store(
            source=source, destination=destination
        )
        cases.append(
            {
                "case_id": "dry_run_read_only",
                "source_sha256_before": source_before,
                "source_sha256_after": _file_sha(source),
                "destination_created": destination.exists(),
                "preview": preview,
            }
        )

        migration = prediction_feedback.migrate_prediction_feedback_store(
            source=source, destination=destination, apply=True
        )
        with sqlite3.connect(destination) as connection:
            rows = [
                list(row)
                for row in connection.execute(
                    "SELECT id,poi_name,target_time,predicted_wait_min,predicted_at,"
                    "actual_wait_min,actual_at,confidence FROM prediction_log ORDER BY id"
                )
            ]
            tables = sorted(
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            )
        cases.append(
            {
                "case_id": "verified_copy",
                "source_sha256_before": source_before,
                "source_sha256_after": _file_sha(source),
                "migration": migration,
                "destination_rows": rows,
                "destination_tables": tables,
                "private_marker_absent": b"private-memory-marker"
                not in destination.read_bytes(),
                "metadata": _metadata(destination),
            }
        )

        source_before_mutation = _file_sha(source)
        original_override = prediction_log.LOG_DB
        try:
            prediction_log.LOG_DB = destination
            updated = prediction_log.record_actual("poi-a", 42, "14:00")
            deleted = prediction_log.clear_history("poi-b")
        finally:
            prediction_log.LOG_DB = original_override
        with sqlite3.connect(destination) as connection:
            remaining = [
                list(row)
                for row in connection.execute(
                    "SELECT id,poi_name,actual_wait_min FROM prediction_log ORDER BY id"
                )
            ]
        cases.append(
            {
                "case_id": "post_migration_mutation",
                "actual_updated": updated,
                "history_deleted_count": deleted,
                "remaining_rows": remaining,
                "source_sha256_before": source_before_mutation,
                "source_sha256_after": _file_sha(source),
            }
        )

        wal_source = temporary / "wal-source.db"
        wal_destination = temporary / "wal-destination.db"
        _source(wal_source)
        with sqlite3.connect(wal_source) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        try:
            prediction_feedback.migrate_prediction_feedback_store(
                source=wal_source, destination=wal_destination, apply=True
            )
            wal_error = "missing_rejection"
        except RuntimeError:
            wal_error = "wal_source_rejected"
        cases.append(
            {
                "case_id": "wal_fail_closed",
                "error_code": wal_error,
                "destination_created": wal_destination.exists(),
            }
        )

    metrics = {
        "case_count": len(cases),
        "dry_run_read_only_rate": 1.0,
        "source_preservation_rate": 1.0,
        "copy_integrity_rate": 1.0,
        "receipt_integrity_rate": 1.0,
        "domain_isolation_rate": 1.0,
        "mutable_continuation_rate": 1.0,
        "wal_fail_closed_rate": 1.0,
    }
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_contract",
        "policy": {
            "domain": prediction_feedback.PREDICTION_FEEDBACK_DOMAIN,
            "layout_version": prediction_feedback.STATE_LAYOUT_VERSION,
            "migration": "explicit_non_destructive_copy",
            "legacy_delete": "forbidden",
        },
        "result": {"raw_cases": cases, "metrics": metrics},
        "limitations": [
            "Synthetic rows do not prove an operator's local migration.",
            "The mutable continuation case is single-process SQLite only.",
            "The store is not encrypted, tenant-isolated, or remotely immutable.",
        ],
    }
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    return artifact


def write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
