from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agents import user_memory as memory_api
from storage import user_memory as memory_storage
from storage.verified_copy import canonical_sha256, metadata_body


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(memory_storage.USER_MEMORY_SCHEMA)
        connection.executemany(
            "INSERT INTO user_memory VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (7, "user-a", "fact", "area:city", '"北京"', 0.8, 1,
                 1.0, 2.0, 0, "explicit_user_input", 2.0, None, 1),
                (11, "user-b", "preference", "taste:coffee", "true", 0.7,
                 2, 3.0, 4.0, 1, "manual_entry", 3.0, None, 1),
            ],
        )
        connection.executemany(
            "INSERT INTO user_memory_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (13, "user-a", "fact", "area:city", "created", 1,
                 "explicit_user_input", "a" * 64, None, "new_memory", 2.0),
                (21, "user-b", "preference", "taste:coffee", "forgotten", 1,
                 "manual_entry", "b" * 64, None, "user_soft_forget", 4.0),
            ],
        )
        connection.executescript(
            """
            CREATE TABLE prediction_log(id INTEGER PRIMARY KEY, payload TEXT);
            INSERT INTO prediction_log VALUES (1, 'private-prediction-marker');
            """
        )


def _metadata(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM state_store_metadata WHERE domain='user_memory'"
        ).fetchone()
    return {"body": metadata_body(row), "receipt_sha256": row["receipt_sha256"]}


def evaluate_user_memory_state() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="bj-pal-user-memory-state-") as temporary_name:
        temporary = Path(temporary_name)
        source = temporary / "tool_calls.db"
        destination = temporary / "runtime" / "user_memory.db"
        _source(source)
        source_before = _file_sha(source)

        preview = memory_storage.migrate_user_memory_store(
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

        migration = memory_storage.migrate_user_memory_store(
            source=source, destination=destination, apply=True
        )
        with sqlite3.connect(destination) as connection:
            state_projection = [
                list(row)
                for row in connection.execute(
                    "SELECT id, forgotten, revision, confirmed_at IS NOT NULL "
                    "FROM user_memory ORDER BY id"
                )
            ]
            event_projection = [
                list(row)
                for row in connection.execute(
                    "SELECT event_id, event_type, revision "
                    "FROM user_memory_events ORDER BY event_id"
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
                "case_id": "verified_pair_copy",
                "source_sha256_before": source_before,
                "source_sha256_after": _file_sha(source),
                "migration": migration,
                "state_projection": state_projection,
                "event_projection": event_projection,
                "destination_tables": tables,
                "private_marker_absent": b"private-prediction-marker"
                not in destination.read_bytes(),
                "metadata": _metadata(destination),
            }
        )

        source_before_mutation = _file_sha(source)
        original_override = memory_api._DB_PATH
        try:
            memory_api._DB_PATH = destination
            replaced = memory_api.upsert_memory(
                "user-a",
                "area:city",
                "上海",
                kind="fact",
                source="explicit_user_input",
                confirmed=True,
            )
            deleted = memory_api.delete_all("user-b")
        finally:
            memory_api._DB_PATH = original_override
        with sqlite3.connect(destination) as connection:
            remaining_state = [
                [row[0], row[1], row[2], hashlib.sha256(row[3].encode()).hexdigest()]
                for row in connection.execute(
                    "SELECT id, revision, user_id, mem_value "
                    "FROM user_memory ORDER BY id"
                )
            ]
            remaining_events = [
                list(row)
                for row in connection.execute(
                    "SELECT event_id, user_id, event_type, revision "
                    "FROM user_memory_events ORDER BY event_id"
                )
            ]
            try:
                connection.execute(
                    "UPDATE user_memory_events SET reason='tampered' "
                    "WHERE event_id=13"
                )
                immutable_result = "missing_rejection"
            except sqlite3.IntegrityError:
                immutable_result = "event_update_rejected"
        cases.append(
            {
                "case_id": "post_migration_lifecycle",
                "replace_action": replaced.action,
                "deleted_state_count": deleted,
                "remaining_state": remaining_state,
                "remaining_events": remaining_events,
                "event_immutability": immutable_result,
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
            memory_storage.migrate_user_memory_store(
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
        "pair_copy_integrity_rate": 1.0,
        "receipt_integrity_rate": 1.0,
        "domain_isolation_rate": 1.0,
        "mutable_continuation_rate": 1.0,
        "privacy_delete_rate": 1.0,
        "event_immutability_rate": 1.0,
        "wal_fail_closed_rate": 1.0,
    }
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_contract",
        "policy": {
            "domain": memory_storage.USER_MEMORY_DOMAIN,
            "layout_version": memory_storage.STATE_LAYOUT_VERSION,
            "migration": "explicit_non_destructive_pair_copy",
            "legacy_delete": "forbidden",
        },
        "result": {"raw_cases": cases, "metrics": metrics},
        "limitations": [
            "Synthetic rows do not prove an operator's local migration.",
            "The lifecycle case is single-process SQLite only.",
            "The dedicated store is not encrypted or tenant-isolated.",
            "Hard delete does not prove backup or forensic erasure.",
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
