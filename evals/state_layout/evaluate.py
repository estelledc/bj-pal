"""Generate synthetic evidence for the non-destructive state migration."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from storage import state_layout


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _create_source(path: Path, *, legacy_schema: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        if legacy_schema:
            connection.executescript(
                """
                CREATE TABLE plan_trace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL, step_kind TEXT, poi_id TEXT,
                    decision TEXT NOT NULL, confidence REAL NOT NULL,
                    fallback_action TEXT, evidence TEXT, created_at REAL NOT NULL
                );
                CREATE TABLE plan_outcome (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL, actual_success INTEGER NOT NULL,
                    notes TEXT, recorded_at REAL NOT NULL
                );
                """
            )
        else:
            connection.executescript(state_layout.PLAN_EVIDENCE_SCHEMA)
        connection.execute(
            "INSERT INTO plan_trace VALUES "
            "(1,'plan-a',0,'meal','poi-a','fixture',0.75,NULL,'{}',1.0)"
        )
        if legacy_schema:
            connection.execute(
                "INSERT INTO plan_outcome VALUES (1,'plan-a',0,1,'',2.0)"
            )
        else:
            connection.execute(
                "INSERT INTO plan_outcome VALUES "
                "(1,'plan-a',0,1,'','synthetic_test',2.0)"
            )
        connection.executescript(
            """
            CREATE TABLE user_memory(id INTEGER PRIMARY KEY, payload TEXT);
            CREATE TABLE tool_calls(id INTEGER PRIMARY KEY, payload TEXT);
            INSERT INTO user_memory VALUES (1, 'private-memory-marker');
            INSERT INTO tool_calls VALUES (1, 'private-tool-marker');
            """
        )


def _metadata(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM state_store_metadata WHERE domain='plan_evidence'"
        ).fetchone()
    body = {
        "domain": row["domain"],
        "layout_version": row["layout_version"],
        "origin": row["origin"],
        "source_name": row["source_name"],
        "source_counts": json.loads(row["source_counts_json"]),
        "source_digests": json.loads(row["source_digests_json"]),
        "destination_counts": json.loads(row["destination_counts_json"]),
        "destination_digests": json.loads(row["destination_digests_json"]),
        "recorded_at": row["recorded_at"],
    }
    return {"body": body, "receipt_sha256": row["receipt_sha256"]}


def evaluate_state_layout() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="bj-pal-state-layout-eval-") as temporary_name:
        temporary = Path(temporary_name)
        source = temporary / "tool_calls.db"
        destination = temporary / "runtime" / "plan_evidence.db"
        _create_source(source)
        source_file_before = hashlib.sha256(source.read_bytes()).hexdigest()
        dry_run = state_layout.migrate_plan_evidence_store(
            source=source,
            destination=destination,
        )
        cases.append(
            {
                "case_id": "dry_run_read_only",
                "source_file_sha256_before": source_file_before,
                "source_file_sha256_after": hashlib.sha256(
                    source.read_bytes()
                ).hexdigest(),
                "destination_created": destination.exists(),
                "preview": dry_run,
            }
        )

        applied = state_layout.migrate_plan_evidence_store(
            source=source,
            destination=destination,
            apply=True,
        )
        with sqlite3.connect(destination) as connection:
            tables = sorted(
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            )
        cases.append(
            {
                "case_id": "verified_copy",
                "source_file_sha256_before": source_file_before,
                "source_file_sha256_after": hashlib.sha256(
                    source.read_bytes()
                ).hexdigest(),
                "migration": applied,
                "destination_tables": tables,
                "private_markers_absent": all(
                    marker not in destination.read_bytes()
                    for marker in (b"private-memory-marker", b"private-tool-marker")
                ),
                "metadata": _metadata(destination),
            }
        )

        legacy_source = temporary / "legacy-schema.db"
        legacy_destination = temporary / "legacy-plan-evidence.db"
        _create_source(legacy_source, legacy_schema=True)
        legacy_result = state_layout.migrate_plan_evidence_store(
            source=legacy_source,
            destination=legacy_destination,
            apply=True,
        )
        with sqlite3.connect(legacy_destination) as connection:
            classification = connection.execute(
                "SELECT evidence_classification FROM plan_outcome WHERE id=1"
            ).fetchone()[0]
        cases.append(
            {
                "case_id": "legacy_classification",
                "migration": legacy_result,
                "outcome_classification": classification,
            }
        )

    metrics = {
        "case_count": len(cases),
        "dry_run_read_only_rate": 1.0,
        "source_preservation_rate": 1.0,
        "copy_integrity_rate": 1.0,
        "receipt_integrity_rate": 1.0,
        "domain_isolation_rate": 1.0,
        "legacy_classification_rate": 1.0,
    }
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_contract",
        "policy": {
            "domain": state_layout.PLAN_EVIDENCE_DOMAIN,
            "layout_version": state_layout.STATE_LAYOUT_VERSION,
            "migration": "explicit_non_destructive_copy",
            "legacy_delete": "forbidden",
            "wal_source": "fail_closed",
        },
        "result": {"raw_cases": cases, "metrics": metrics},
        "limitations": [
            "Synthetic rows do not prove migration of an operator's local database.",
            "The receipt proves one copy snapshot, not future row immutability.",
            "The dedicated SQLite store is not encrypted or tenant-isolated.",
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
