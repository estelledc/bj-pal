"""Owned plan-evidence state layout built on verified snapshot migration."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from .verified_copy import (
    DomainSpec,
    ensure_metadata,
    inspect_store,
    legacy_has_rows,
    metadata_body,
    metadata_valid,
    migrate_store,
)


ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_SHARED_DB = ROOT / "tool_calls.db"
PLAN_EVIDENCE_DEFAULT_DB = ROOT / "runtime" / "plan_evidence.db"
PLAN_EVIDENCE_DB_ENV = "BJ_PAL_PLAN_EVIDENCE_DB"
PLAN_EVIDENCE_DOMAIN = "plan_evidence"
STATE_LAYOUT_VERSION = "state_layout_v1"

PLAN_EVIDENCE_TABLE_COLUMNS = {
    "plan_trace": (
        "id", "plan_id", "step_index", "step_kind", "poi_id", "decision",
        "confidence", "fallback_action", "evidence", "created_at",
    ),
    "plan_outcome": (
        "id", "plan_id", "step_index", "actual_success", "notes",
        "evidence_classification", "recorded_at",
    ),
}

PLAN_EVIDENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL,
    step_index INTEGER NOT NULL, step_kind TEXT, poi_id TEXT,
    decision TEXT NOT NULL, confidence REAL NOT NULL,
    fallback_action TEXT, evidence TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_trace_plan ON plan_trace(plan_id, step_index);
CREATE TABLE IF NOT EXISTS plan_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL,
    step_index INTEGER NOT NULL, actual_success INTEGER NOT NULL, notes TEXT,
    evidence_classification TEXT NOT NULL DEFAULT 'legacy_unclassified',
    recorded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_outcome_plan ON plan_outcome(plan_id, step_index);
"""

PLAN_EVIDENCE_SPEC = DomainSpec(
    domain=PLAN_EVIDENCE_DOMAIN,
    layout_version=STATE_LAYOUT_VERSION,
    table_columns=PLAN_EVIDENCE_TABLE_COLUMNS,
    schema=PLAN_EVIDENCE_SCHEMA,
    legacy_column_defaults={
        ("plan_outcome", "evidence_classification"): "'legacy_unclassified'"
    },
)


def inspect_plan_evidence_store(path: Path) -> dict[str, Any]:
    return inspect_store(path, PLAN_EVIDENCE_SPEC)


def _metadata_body(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return metadata_body(row)


def _valid_metadata(path: Path) -> bool:
    return metadata_valid(path, PLAN_EVIDENCE_SPEC)


def _legacy_has_plan_evidence(path: Path | None = None) -> bool:
    return legacy_has_rows(path or LEGACY_SHARED_DB, PLAN_EVIDENCE_SPEC)


def resolve_plan_evidence_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    configured = os.environ.get(PLAN_EVIDENCE_DB_ENV)
    if configured:
        return Path(configured)
    if _valid_metadata(PLAN_EVIDENCE_DEFAULT_DB):
        return PLAN_EVIDENCE_DEFAULT_DB
    if _legacy_has_plan_evidence(LEGACY_SHARED_DB):
        return LEGACY_SHARED_DB
    return PLAN_EVIDENCE_DEFAULT_DB


def ensure_plan_evidence_metadata(path: Path, *, origin: str = "native") -> None:
    path = Path(path)
    if path.resolve() == LEGACY_SHARED_DB.resolve():
        return
    ensure_metadata(path, PLAN_EVIDENCE_SPEC, origin=origin)


def migrate_plan_evidence_store(
    *,
    source: Path = LEGACY_SHARED_DB,
    destination: Path = PLAN_EVIDENCE_DEFAULT_DB,
    apply: bool = False,
) -> dict[str, Any]:
    return migrate_store(
        source=source,
        destination=destination,
        spec=PLAN_EVIDENCE_SPEC,
        apply=apply,
    )
