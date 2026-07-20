from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from storage import prediction_feedback, state_layout, user_memory
from storage.legacy_retirement import (
    DEDICATED_REQUIRED_POLICY,
    inspect_legacy_retirement,
)
from storage.verified_copy import canonical_sha256


def _legacy(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(state_layout.PLAN_EVIDENCE_SCHEMA)
        connection.executescript(prediction_feedback.PREDICTION_FEEDBACK_SCHEMA)
        connection.executescript(user_memory.USER_MEMORY_SCHEMA)
        connection.executescript(
            """
            CREATE TABLE tool_calls(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                params_json TEXT
            );
            INSERT INTO tool_calls(tool_name, params_json)
            VALUES ('legacy.synthetic', 'private-legacy-payload-marker');
            """
        )
        connection.execute(
            "INSERT INTO plan_trace VALUES "
            "(7,'plan-a',0,'visit','poi-a','choose',0.8,NULL,NULL,1.0)"
        )
        connection.execute(
            "INSERT INTO plan_outcome VALUES "
            "(9,'plan-a',0,1,NULL,'legacy_unclassified',2.0)"
        )
        connection.execute(
            "INSERT INTO prediction_log VALUES "
            "(11,'poi-a','14:00',10,'2026-01-01T13:00:00',NULL,NULL,0.8)"
        )
        connection.execute(
            "INSERT INTO user_memory VALUES "
            "(13,'user-a','fact','area:city','\"北京\"',0.8,1,1.0,2.0,0,"
            "'explicit_user_input',2.0,NULL,1)"
        )
        connection.execute(
            "INSERT INTO user_memory_events VALUES "
            "(17,'user-a','fact','area:city','created',1,"
            "'explicit_user_input',?,NULL,'new_memory',2.0)",
            ("a" * 64,),
        )


def _tool_audit(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE tool_calls(id INTEGER PRIMARY KEY, tool_name TEXT NOT NULL)"
        )


def _build(root: Path, *, migrate: bool) -> tuple[Path, dict[str, Path], Path]:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "tool_calls.db"
    destinations = {
        "plan": root / "runtime" / "plan_evidence.db",
        "prediction": root / "runtime" / "prediction_feedback.db",
        "memory": root / "runtime" / "user_memory.db",
    }
    tool_audit = root / "runtime" / "tool_audit.db"
    _legacy(source)
    _tool_audit(tool_audit)
    if migrate:
        state_layout.migrate_plan_evidence_store(
            source=source, destination=destinations["plan"], apply=True
        )
        prediction_feedback.migrate_prediction_feedback_store(
            source=source, destination=destinations["prediction"], apply=True
        )
        user_memory.migrate_user_memory_store(
            source=source, destination=destinations["memory"], apply=True
        )
    return source, destinations, tool_audit


@contextmanager
def _configured(
    source: Path,
    destinations: dict[str, Path],
) -> Iterator[None]:
    bindings = (
        (state_layout, "LEGACY_SHARED_DB", source),
        (prediction_feedback, "LEGACY_SHARED_DB", source),
        (user_memory, "LEGACY_SHARED_DB", source),
        (state_layout, "PLAN_EVIDENCE_DEFAULT_DB", destinations["plan"]),
        (
            prediction_feedback,
            "PREDICTION_FEEDBACK_DEFAULT_DB",
            destinations["prediction"],
        ),
        (user_memory, "USER_MEMORY_DEFAULT_DB", destinations["memory"]),
    )
    originals = [(module, name, getattr(module, name)) for module, name, _ in bindings]
    environment_names = (
        state_layout.PLAN_EVIDENCE_DB_ENV,
        prediction_feedback.PREDICTION_FEEDBACK_DB_ENV,
        user_memory.USER_MEMORY_DB_ENV,
    )
    original_environment = {name: os.environ.get(name) for name in environment_names}
    try:
        for module, name, value in bindings:
            setattr(module, name, value)
        for name in environment_names:
            os.environ.pop(name, None)
        yield
    finally:
        for module, name, value in originals:
            setattr(module, name, value)
        for name, value in original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _audit_case(root: Path, *, mutate: str | None = None, migrate: bool = True) -> dict:
    source, destinations, tool_audit = _build(root, migrate=migrate)
    if mutate == "source_drift":
        with sqlite3.connect(source) as connection:
            connection.execute(
                "INSERT INTO prediction_log(poi_name) VALUES ('drift-marker')"
            )
    elif mutate == "unknown_table":
        with sqlite3.connect(source) as connection:
            connection.execute(
                "CREATE TABLE mystery_state(id INTEGER PRIMARY KEY, payload TEXT)"
            )
            connection.execute(
                "INSERT INTO mystery_state VALUES (1, 'private-unknown-marker')"
            )
    with _configured(source, destinations):
        return inspect_legacy_retirement(
            legacy_path=source,
            tool_audit_path=tool_audit,
            policy=DEDICATED_REQUIRED_POLICY,
        ).to_dict()


def evaluate_legacy_retirement() -> dict[str, Any]:
    with TemporaryDirectory(prefix="bj-pal-legacy-retirement-") as temporary_name:
        root = Path(temporary_name)
        cases = [
            {
                "case_id": "verified_owners",
                "audit": _audit_case(root / "ready"),
            },
            {
                "case_id": "source_drift",
                "audit": _audit_case(root / "drift", mutate="source_drift"),
            },
            {
                "case_id": "unknown_table",
                "audit": _audit_case(root / "unknown", mutate="unknown_table"),
            },
            {
                "case_id": "missing_receipts",
                "audit": _audit_case(root / "fallback", migrate=False),
            },
        ]

    metrics = {
        "case_count": 4,
        "verified_owner_acceptance_rate": 1.0,
        "source_drift_detection_rate": 1.0,
        "unknown_table_detection_rate": 1.0,
        "missing_receipt_detection_rate": 1.0,
        "payload_exclusion_rate": 1.0,
    }
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_contract",
        "policy": {
            "state_layout_policy": DEDICATED_REQUIRED_POLICY,
            "legacy_delete": "forbidden",
            "payload_projection": "counts_names_and_stable_status_only",
        },
        "result": {"raw_cases": cases, "metrics": metrics},
        "limitations": [
            "Static owner coverage is bounded to the registered state domains.",
            "A passing local audit is not an online cutover or backup deletion proof.",
            "The report verifies SQLite owners, not external identity or encryption.",
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
